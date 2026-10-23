"""Monthly bookings forecast per service line, one to three months ahead.

Candidates, all computed at the start of a month with only what was known then:

- `seasonal_naive`: the same month last year;
- `ets`: Holt-Winters with damped trend and yearly seasonality;
- `pipeline_crm`: open opportunities weighted by the CRM's default stage probabilities and placed
  in their expected close month (what a sales operations report shows), plus the recent run rate
  of bookings that never appear in the pipeline at the start of the month;
- `pipeline_learned`: the same pipeline, but the share that closes as won in each month after the
  snapshot is learned per service line and stage from earlier snapshots whose outcome is known;
- `blend`: the average of `ets` and `pipeline_learned`.

The champion per service line is chosen on validation origins and reported on later test origins,
so the reported error is not flattered by the choice. Intervals come from the champion's
validation errors.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

MODELS = ["seasonal_naive", "ets", "pipeline_crm", "pipeline_learned", "blend"]


def holt_winters(y: np.ndarray, horizon: int, season: int = 12) -> np.ndarray:
    """Additive Holt-Winters with damped trend; parameters by grid search on one-step error."""
    if len(y) < 2 * season:
        return np.repeat(y[-season:].mean() if len(y) else 0.0, horizon)
    best, best_err = None, np.inf
    m1, m2 = y[:season].mean(), y[season : 2 * season].mean()
    trend0 = (m2 - m1) / season
    # Start from the first season with its trend removed, so the seasonal indices do not carry it.
    seas0 = [y[i] - (m1 + (i - (season - 1) / 2) * trend0) for i in range(season)]
    level0 = m1 + (season - 1) / 2 * trend0
    for a, b, g, phi in itertools.product((0.1, 0.2, 0.3, 0.5), (0.0, 0.05, 0.1), (0.05, 0.15, 0.3), (0.9, 0.98)):
        level, trend, seas = level0, trend0, list(seas0)
        err = 0.0
        for t in range(season, len(y)):
            f = level + phi * trend + seas[t - season]
            err += (y[t] - f) ** 2
            new_level = a * (y[t] - seas[t - season]) + (1 - a) * (level + phi * trend)
            trend = b * (new_level - level) + (1 - b) * phi * trend
            seas.append(g * (y[t] - new_level) + (1 - g) * seas[t - season])
            level = new_level
        if err < best_err:
            best_err, best = err, (level, trend, seas, phi)
    assert best is not None
    level, trend, seas, phi = best
    n = len(y)
    out = []
    damp = 0.0
    for h in range(1, horizon + 1):
        damp += phi**h
        out.append(level + damp * trend + seas[n - season + (h - 1) % season])
    return np.maximum(np.array(out), 0.0)


def _months(a: pd.Timestamp, b: pd.Timestamp) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


class Forecaster:
    def __init__(self, bookings: pd.DataFrame, snapshots: pd.DataFrame, stage_probs: dict[str, float]):
        """`bookings`: month, service_line, booked_eur. `snapshots`: fct_pipeline_snapshots joined
        with the quote's booked value (net_eur) and decision month."""
        self.y = bookings.pivot_table(index="month", columns="service_line", values="booked_eur", aggfunc="sum").sort_index()
        self.y.index = pd.to_datetime(self.y.index)
        s = snapshots.copy()
        for c in ("snapshot_month", "expected_close_month", "closed_month"):
            s[c] = pd.to_datetime(s[c])
        s["won_value"] = np.where(s["final_status"] == "closed_won", s["net_eur"].fillna(0.0), 0.0)
        s["k_close"] = [_months(a, b) if pd.notna(b) else -1 for a, b in zip(s["snapshot_month"], s["closed_month"], strict=True)]
        self.s = s
        self.pw = s.groupby(["snapshot_month", "service_line", "k_close"])["won_value"].sum()
        self.stage_probs = stage_probs
        self.lines = list(self.y.columns)

    # -- pipeline pieces ------------------------------------------------------------------------

    def _pipelined_won(self, snapshot: pd.Timestamp, line: str, k: int) -> float:
        return float(self.pw.get((snapshot, line, k), 0.0))

    def _unpipelined_rate(self, origin: pd.Timestamp, line: str, k: int, window: int = 6) -> float:
        """Recent bookings not in the pipeline k months before they closed (known at origin)."""
        vals = []
        for j in range(1, window + 1):
            m = origin - pd.DateOffset(months=j)
            snap = m - pd.DateOffset(months=k)
            if m not in self.y.index or snap < self.s["snapshot_month"].min():
                continue
            vals.append(self.y.loc[m, line] - self._pipelined_won(snap, line, k))
        return float(np.mean(vals)) if vals else 0.0

    def _learned_table(self, origin: pd.Timestamp, max_k: int) -> pd.DataFrame:
        """Share of pipeline value booked k months after the snapshot, by line and stage, from
        snapshots 13-36 months before the origin (outcomes known by then)."""
        lo, hi = origin - pd.DateOffset(months=36), origin - pd.DateOffset(months=13)
        s = self.s[(self.s["snapshot_month"] >= lo) & (self.s["snapshot_month"] <= hi)]
        denom = s.groupby(["service_line", "stage"])["amount_eur"].sum()
        rows = []
        for k in range(max_k):
            num = s[s["k_close"] == k].groupby(["service_line", "stage"])["won_value"].sum()
            rows.append((num / denom).fillna(0.0).rename(k))
        return pd.concat(rows, axis=1).fillna(0.0)

    def forecast(self, origin: pd.Timestamp, horizons: list[int]) -> pd.DataFrame:
        hist = self.y[self.y.index < origin]
        snap = self.s[self.s["snapshot_month"] == origin]
        table = self._learned_table(origin, max(horizons))
        out = []
        for line in self.lines:
            y = hist[line].to_numpy()
            ets = holt_winters(y, max(horizons))
            sl = snap[snap["service_line"] == line]
            for h in horizons:
                k = h - 1
                target = origin + pd.DateOffset(months=k)
                sn = float(self.y[line].get(target - pd.DateOffset(months=12), np.nan))
                unp = self._unpipelined_rate(origin, line, k)
                exp_close = sl["expected_close_month"].where(sl["expected_close_month"] >= origin, origin)
                crm = float((sl["amount_eur"] * sl["stage"].map(self.stage_probs).fillna(0.1))[exp_close == target].sum())
                share = table[k] if k in table.columns else pd.Series(dtype=float)
                learned_share = sl["stage"].map(lambda st, ln=line, sh=share: sh.get((ln, st), 0.0))
                learned = float((sl["amount_eur"] * learned_share).sum())
                f = {
                    "seasonal_naive": sn,
                    "ets": float(ets[k]),
                    "pipeline_crm": crm + unp,
                    "pipeline_learned": learned + unp,
                }
                f["blend"] = 0.5 * (f["ets"] + f["pipeline_learned"])
                for model, v in f.items():
                    out.append((origin, h, target, line, model, v))
        return pd.DataFrame(out, columns=["origin", "horizon", "target", "service_line", "model", "forecast"])

    def backtest(self, origins: list[pd.Timestamp], horizons: list[int]) -> pd.DataFrame:
        fc = pd.concat([self.forecast(o, horizons) for o in origins], ignore_index=True)
        actual = self.y.stack().rename("actual").reset_index().rename(columns={"month": "target"})
        return fc.merge(actual, on=["target", "service_line"], how="inner")


def wape(df: pd.DataFrame) -> float:
    return float((df["forecast"] - df["actual"]).abs().sum() / max(df["actual"].abs().sum(), 1e-9))


def bias(df: pd.DataFrame) -> float:
    return float((df["forecast"] - df["actual"]).sum() / max(df["actual"].abs().sum(), 1e-9))


def select_and_score(bt: pd.DataFrame, valid_origins, test_origins, interval: float = 0.8) -> dict:
    """Champion per line on validation origins; WAPE, bias and interval coverage on test origins."""
    v = bt[bt["origin"].isin(valid_origins)]
    t = bt[bt["origin"].isin(test_origins)]
    lo_q, hi_q = (1 - interval) / 2, 1 - (1 - interval) / 2
    result: dict = {"by_line": {}, "models_on_test": {}}
    champ_rows = []
    for line in sorted(bt["service_line"].unique()):
        vl = v[v["service_line"] == line]
        scores = {m: wape(vl[vl["model"] == m]) for m in MODELS if len(vl[vl["model"] == m].dropna())}
        champion = min(scores, key=lambda m: scores[m])
        cv = vl[vl["model"] == champion].dropna()
        err = cv["actual"] - cv["forecast"]
        lo_e, hi_e = np.quantile(err, lo_q), np.quantile(err, hi_q)
        tl = t[(t["service_line"] == line) & (t["model"] == champion)].dropna().copy()
        tl["lo"], tl["hi"] = (tl["forecast"] + lo_e).clip(lower=0), tl["forecast"] + hi_e
        cover = float(((tl["actual"] >= tl["lo"]) & (tl["actual"] <= tl["hi"])).mean())
        champ_rows.append(tl)
        result["by_line"][line] = {
            "champion": champion,
            "validation_wape": {m: round(s, 3) for m, s in scores.items()},
            "test_wape": round(wape(tl), 3),
            "test_bias": round(bias(tl), 3),
            "test_wape_seasonal_naive": round(wape(t[(t["service_line"] == line) & (t["model"] == "seasonal_naive")].dropna()), 3),
            "test_wape_pipeline_crm": round(wape(t[(t["service_line"] == line) & (t["model"] == "pipeline_crm")].dropna()), 3),
            "interval_coverage": round(cover, 3),
        }
    champ = pd.concat(champ_rows)
    result["total"] = {
        "test_wape_champions": round(wape(champ.groupby(["origin", "horizon"])[["forecast", "actual"]].sum()), 3),
        "interval_coverage": round(float(((champ["actual"] >= champ["lo"]) & (champ["actual"] <= champ["hi"])).mean()), 3),
    }
    for m in MODELS:
        tm = t[t["model"] == m].dropna()
        result["models_on_test"][m] = {
            "wape_total": round(wape(tm.groupby(["origin", "horizon"])[["forecast", "actual"]].sum()), 3),
            "bias_total": round(bias(tm.groupby(["origin", "horizon"])[["forecast", "actual"]].sum()), 3),
        }
    result["champion_rows"] = champ
    return result
