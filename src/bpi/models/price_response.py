"""How much the win probability moves when the price moves.

Reading this off history is biased: reps give bigger discounts on deals they sense are at risk, and
those deals lose more often, so in the data lower prices travel with lower win rates. A model that
controls for everything recorded (competitor, urgency, customer history) still cannot see the
rep's private read of the deal.

The pricing team's randomised test settles it: a share of spare parts and field service quotes got a
random price change on top of the rep's price. Within the test, the change is unrelated to deal
risk, so regressing wins on it (with the observational model's prediction at the rep's own price as
an offset) gives the price slope without that bias. Only lines with a test are allowed to drive
price recommendations; for the others the system says so and keeps the rep's price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

LINES = ["spare_parts", "field_service", "repairs", "upgrades", "ltsa"]


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-x))


def logistic_with_offset(X: np.ndarray, y: np.ndarray, offset: np.ndarray, iters: int = 50) -> tuple[np.ndarray, np.ndarray]:
    """Maximum likelihood logistic regression with a fixed offset (Newton-Raphson). Returns
    coefficients and their standard errors."""
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = _sigmoid(offset + X @ b)
        w = p * (1 - p)
        H = X.T @ (X * w[:, None]) + 1e-9 * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - p))
        b += step
        if np.max(np.abs(step)) < 1e-8:
            break
    p = _sigmoid(offset + X @ b)
    cov = np.linalg.inv(X.T @ (X * (p * (1 - p))[:, None]))
    return b, np.sqrt(np.diag(cov))


@dataclass
class ExperimentSlope:
    """Price slope (d logit / d log price) of one service line, with and without a competitor."""

    line: str
    quotes: int
    no_competitor: float
    competitor: float
    se_no_competitor: float
    se_competitor: float

    def ci95(self, which: str) -> tuple[float, float]:
        b, se = getattr(self, which), getattr(self, f"se_{which}")
        return (b - 1.96 * se, b + 1.96 * se)

    def precise(self, max_se: float) -> bool:
        return max(self.se_no_competitor, self.se_competitor) <= max_se


def experiment_slopes(test_quotes: pd.DataFrame, base_predict) -> dict[str, ExperimentSlope]:
    """Price slopes per service line from the randomised test cells.

    `base_predict(q, ratio)` is the observational model; it is evaluated at the rep's own price
    (before the random change), which the test cell lets us recover exactly. Only the random change
    enters the regression, so deal risk the rep saw cannot bias it."""
    out = {}
    for line, g in test_quotes[test_quotes["price_test_cell"].notna()].groupby("service_line"):
        cell = g["price_test_cell"].to_numpy(dtype=float)
        r_rep = g["price_ratio"].to_numpy() / (1 + cell)
        offset = _logit(base_predict(g, r_rep))
        comp = g["competitor_present"].to_numpy(dtype=float)
        y = g["won"].to_numpy(dtype=float)
        if min(comp.sum(), (1 - comp).sum()) < 30:
            # Too few quotes on one side to split: one slope for both.
            b, se = logistic_with_offset(np.column_stack([np.ones(len(g)), np.log1p(cell)]), y, offset)
            out[str(line)] = ExperimentSlope(str(line), len(g), float(b[1]), float(b[1]), float(se[1]), float(se[1]))
            continue
        X = np.column_stack([np.ones(len(g)), np.log1p(cell) * (1 - comp), np.log1p(cell) * comp])
        b, se = logistic_with_offset(X, y, offset)
        out[str(line)] = ExperimentSlope(str(line), len(g), float(b[1]), float(b[2]), float(se[1]), float(se[2]))
    return out


@dataclass
class PriceResponse:
    """Win probability at any price, anchored on the base model's prediction at the quote's
    current price and moved along the validated slope of its service line and competition."""

    base_predict: object  # callable (quotes, ratio) -> p
    slopes: dict[str, dict[str, float]] = field(default_factory=dict)  # validated lines only

    def validated(self, line: str) -> bool:
        return line in self.slopes

    def slope(self, q: pd.DataFrame) -> np.ndarray:
        comp = q["competitor_present"].to_numpy(dtype=bool)
        b0 = q["service_line"].map(lambda s: self.slopes.get(s, {}).get("no_competitor", 0.0)).to_numpy(dtype=float)
        b1 = q["service_line"].map(lambda s: self.slopes.get(s, {}).get("competitor", 0.0)).to_numpy(dtype=float)
        return np.where(comp, b1, b0)

    def predict(self, q: pd.DataFrame, ratio: np.ndarray) -> np.ndarray:
        r0 = q["price_ratio"].to_numpy()
        anchor = _logit(self.base_predict(q, r0))  # type: ignore[operator]
        return _sigmoid(anchor + self.slope(q) * (np.log(ratio) - np.log(r0)))


def average_effect(predict, q: pd.DataFrame, change: float = 0.05) -> float:
    """Average change in win probability if every quote were priced `change` higher."""
    r = q["price_ratio"].to_numpy()
    return float(np.mean(predict(q, r * (1 + change)) - predict(q, r)))
