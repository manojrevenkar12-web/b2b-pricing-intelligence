"""Is the win model still describing the market it is pricing?

Two checks, run on a schedule after each warehouse build:

- feature drift: population stability index of each input between the training quotes and the most
  recent quarter of quotes;
- calibration: expected calibration error of the champion on the most recent decided quotes.

Either alert means the model goes back to the pricing team before its recommendations are trusted
again (see docs/RUNBOOK.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bpi.models.win import CATEGORICAL, ece, features

DRIFT_FEATURES = [
    "log_ratio",
    "log_list_eur",
    "competitor_present",
    "outage_urgent",
    "prior_win_rate",
    "log_competitor_index",
]


def psi(expected: pd.Series, actual: pd.Series, bins: int = 10) -> float:
    if not pd.api.types.is_numeric_dtype(expected) or expected.nunique() <= 12:
        e = expected.value_counts(normalize=True)
        a = actual.value_counts(normalize=True)
        cats = e.index.union(a.index)
        e, a = e.reindex(cats).fillna(0) + 1e-4, a.reindex(cats).fillna(0) + 1e-4
    else:
        edges = np.unique(np.quantile(expected.dropna(), np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        e = pd.Series(np.histogram(expected.dropna(), edges)[0] / expected.notna().sum()) + 1e-4
        a = pd.Series(np.histogram(actual.dropna(), edges)[0] / max(actual.notna().sum(), 1)) + 1e-4
    return float(((a - e) * np.log(a / e)).sum())


def check(train: pd.DataFrame, recent: pd.DataFrame, recent_decided: pd.DataFrame, predict, psi_alert: float, ece_alert: float) -> dict:
    ft, fr = features(train), features(recent)
    drift = {c: round(psi(ft[c], fr[c]), 4) for c in DRIFT_FEATURES + CATEGORICAL}
    cal = ece(recent_decided["won"].to_numpy(), predict(recent_decided)) if len(recent_decided) else float("nan")
    alerts = [f"drift in {c} (PSI {v})" for c, v in drift.items() if v > psi_alert]
    if cal > ece_alert:
        alerts.append(f"calibration error {cal:.3f} above {ece_alert}")
    return {
        "psi": drift,
        "recent_quotes": len(recent),
        "recent_decided": len(recent_decided),
        "ece_recent": round(float(cal), 4),
        "alerts": alerts,
    }
