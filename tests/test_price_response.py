import numpy as np
import pandas as pd

from bpi.models.price_response import PriceResponse, experiment_slopes, logistic_with_offset


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def test_logistic_with_offset_recovers_slope():
    rng = np.random.default_rng(0)
    n = 20000
    x = rng.normal(0, 0.05, n)
    off = rng.normal(0, 1, n)
    y = (rng.random(n) < _sigmoid(off + 0.2 - 4.0 * x)).astype(float)
    b, se = logistic_with_offset(np.column_stack([np.ones(n), x]), y, off)
    assert abs(b[1] + 4.0) < 3 * se[1]


def test_randomised_cells_beat_confounded_history():
    """Reps discount deals at risk; history then shows no price effect, the test still finds it."""
    rng = np.random.default_rng(1)
    n = 30000
    risk = rng.normal(0, 1, n)
    rep_ratio = np.clip(0.88 - 0.03 * risk + rng.normal(0, 0.01, n), 0.6, 1.0)
    cell = np.where(rng.random(n) < 0.5, rng.choice([-0.06, -0.03, 0.0, 0.03, 0.06], n), np.nan)
    ratio = rep_ratio * (1 + np.nan_to_num(cell))
    true_slope = -5.0
    won = (rng.random(n) < _sigmoid(0.3 - 1.0 * risk + true_slope * np.log(ratio))).astype(int)
    q = pd.DataFrame(
        {"service_line": "spare_parts", "price_ratio": ratio, "price_test_cell": cell, "won": won, "competitor_present": False}
    )

    # Naive model: win rate does not depend on anything but price as observed.
    naive = np.polyfit(np.log(ratio), won, 1)[0]
    assert naive > 0  # wrong sign: low prices go with lost deals

    base = q["won"].mean()
    s = experiment_slopes(q, lambda g, r: np.full(len(g), base))["spare_parts"]
    lo, hi = s.ci95("no_competitor")
    # Marginal slope is attenuated by the unobserved risk, but clearly negative and in range.
    assert hi < 0 and lo < true_slope * 0.5


def test_price_response_moves_along_validated_slope():
    q = pd.DataFrame({"service_line": ["spare_parts", "repairs"], "price_ratio": [0.9, 0.9], "competitor_present": [True, False]})
    pr = PriceResponse(lambda g, r: np.full(len(g), 0.5), {"spare_parts": {"no_competitor": -2.0, "competitor": -6.0}})
    p = pr.predict(q, np.array([0.9 * 1.1, 0.9 * 1.1]))
    assert abs(p[0] - _sigmoid(-6.0 * np.log(1.1))) < 1e-9
    assert abs(p[1] - 0.5) < 1e-12  # unvalidated line: flat
