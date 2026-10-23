"""Price recommendations for quotes, inside the guardrails Global Pricing Strategy sets.

For each quote the recommender scores candidate prices by expected margin, P(win at price) x
(price - cost), and returns the best price and the range within 2% of the best expected margin. It
never leaves the guardrails:

- no recommendation below cost / (1 - minimum margin of the service line);
- no move of more than `max_move_vs_quote` from the rep's price (a recommendation is a range for
  the rep, not an override);
- never above list price;
- service lines priced by the deal desk (LTSA) get no automatic recommendation;
- service lines whose price response has not been validated by a price test keep the rep's price
  and say why.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from bpi.config import Guardrails
from bpi.models.price_response import PriceResponse

GRID = 41


@dataclass
class Recommendation:
    quote_id: str
    status: str  # recommended | hold_unvalidated | deal_desk | rep_below_floor
    current_ratio: float
    recommended_ratio: float
    low_ratio: float
    high_ratio: float
    recommended_price_eur: float
    current_price_eur: float
    p_win_current: float
    p_win_recommended: float
    expected_margin_current_eur: float
    expected_margin_recommended_eur: float
    floor_ratio: float
    reasons: list[str]

    def dict(self) -> dict:
        return asdict(self)


def floor_ratio(q: pd.DataFrame, g: Guardrails) -> np.ndarray:
    m = q["service_line"].map(g.min_margin).to_numpy(dtype=float)
    return q["cost_eur"].to_numpy() / (1 - m) / q["list_eur"].to_numpy()


def bounds(q: pd.DataFrame, g: Guardrails) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r0 = q["price_ratio"].to_numpy()
    fl = floor_ratio(q, g)
    lo = np.maximum(r0 * (1 - g.max_move_vs_quote), fl)
    hi = np.minimum(r0 * (1 + g.max_move_vs_quote), 1 + g.max_above_list)
    # Rep priced so far below the floor that even the largest move cannot reach it: go as far as
    # allowed towards the floor.
    lo = np.minimum(lo, hi)
    return lo, hi, fl


def optimise(q: pd.DataFrame, predict, g: Guardrails) -> pd.DataFrame:
    """Vectorised grid search of expected margin within each quote's bounds."""
    lo, hi, fl = bounds(q, g)
    steps = np.linspace(0, 1, GRID)
    grid = lo[:, None] + (hi - lo)[:, None] * steps[None, :]
    L, C = q["list_eur"].to_numpy(), q["cost_eur"].to_numpy()
    p = np.column_stack([predict(q, grid[:, k]) for k in range(GRID)])
    em = p * (grid * L[:, None] - C[:, None])
    best = em.argmax(axis=1)
    rows = np.arange(len(q))
    ok = em >= em[rows, best][:, None] - 0.02 * np.abs(em[rows, best])[:, None]
    low = np.where(ok, grid, np.inf).min(axis=1)
    high = np.where(ok, grid, -np.inf).max(axis=1)
    return pd.DataFrame(
        {
            "ratio": grid[rows, best],
            "low": low,
            "high": high,
            "p_win": p[rows, best],
            "expected_margin": em[rows, best],
            "floor": fl,
        },
        index=q.index,
    )


def policy_ratios(q: pd.DataFrame, response: PriceResponse | Any, g: Guardrails) -> tuple[np.ndarray, np.ndarray]:
    """The price each quote ends up with under the recommender, and its status."""
    r = q["price_ratio"].to_numpy().copy()
    status = np.full(len(q), "recommended", dtype=object)
    deal_desk = q["service_line"].isin(g.deal_desk_lines).to_numpy()
    unvalidated = ~q["service_line"].map(response.validated).to_numpy(dtype=bool) & ~deal_desk
    status[deal_desk] = "deal_desk"
    status[unvalidated] = "hold_unvalidated"
    go = ~deal_desk & ~unvalidated
    if go.any():
        r[go] = optimise(q[go], response.predict, g)["ratio"].to_numpy()
    return r, status


def recommend(q: pd.DataFrame, response: PriceResponse, g: Guardrails) -> list[Recommendation]:
    """Full recommendations with ranges and reasons, for the API and the sales tools."""
    out: list[Recommendation] = []
    _lo, hi, fl = bounds(q, g)
    r0 = q["price_ratio"].to_numpy()
    L, C = q["list_eur"].to_numpy(), q["cost_eur"].to_numpy()
    p_cur = response.base_predict(q, r0)  # type: ignore[operator]
    opt = None
    go = ~q["service_line"].isin(g.deal_desk_lines) & q["service_line"].map(response.validated).astype(bool)
    if go.any():
        opt = optimise(q[go], response.predict, g)
    for i, (idx, row) in enumerate(q.iterrows()):
        reasons: list[str] = []
        status = "recommended"
        if row["service_line"] in g.deal_desk_lines:
            status = "deal_desk"
            reasons.append("long-term service agreements are priced by the deal desk")
        elif not response.validated(row["service_line"]):
            status = "hold_unvalidated"
            reasons.append(
                f"no price test for {row['service_line']} yet: price sensitivity read from history is biased, so the rep's price stands"
            )
        if r0[i] < fl[i]:
            reasons.append(f"rep's price is below the margin floor ({fl[i]:.1%} of list)")
            if status == "recommended":
                status = "rep_below_floor"
        if opt is not None and idx in opt.index:
            o = opt.loc[idx]
            ratio, low, high, p_rec, em = o["ratio"], o["low"], o["high"], o["p_win"], o["expected_margin"]
            if ratio > r0[i] * 1.001:
                reasons.append("expected margin rises with a higher price: the win probability drops less than the price gains")
            elif ratio < r0[i] * 0.999:
                reasons.append("expected margin rises with a lower price: the win probability gains more than the price loses")
            else:
                reasons.append("the rep's price is already at the best expected margin")
            if np.isclose(ratio, hi[i]) and hi[i] < 1:
                reasons.append("capped by the maximum move from the rep's price")
            if np.isclose(ratio, 1.0):
                reasons.append("capped at list price")
            if np.isclose(ratio, fl[i]):
                reasons.append("held at the margin floor")
        else:
            ratio, low, high, p_rec = r0[i], r0[i], r0[i], p_cur[i]
            em = p_rec * (ratio * L[i] - C[i])
        out.append(
            Recommendation(
                quote_id=str(row.get("quote_id", idx)),
                status=status,
                current_ratio=round(float(r0[i]), 4),
                recommended_ratio=round(float(ratio), 4),
                low_ratio=round(float(low), 4),
                high_ratio=round(float(high), 4),
                recommended_price_eur=round(float(ratio * L[i]), 2),
                current_price_eur=round(float(r0[i] * L[i]), 2),
                p_win_current=round(float(p_cur[i]), 4),
                p_win_recommended=round(float(p_rec), 4),
                expected_margin_current_eur=round(float(p_cur[i] * (r0[i] * L[i] - C[i])), 2),
                expected_margin_recommended_eur=round(float(em), 2),
                floor_ratio=round(float(fl[i]), 4),
                reasons=reasons,
            )
        )
    return out
