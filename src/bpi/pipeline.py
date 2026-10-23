"""Training and the model registry."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pandas as pd

from bpi import warehouse
from bpi.config import Settings, paths
from bpi.models.price_response import PriceResponse, experiment_slopes
from bpi.models.win import GBM, Logistic, scores, split


def data_fingerprint(raw: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(raw.glob("*.csv")):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:16]


def _git_sha() -> str:
    try:
        return (
            subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=paths().root).stdout.strip()
            or "unknown"
        )
    except OSError:
        return "unknown"


def train(st: Settings) -> Path:
    """Fit the win models, pick the champion on the last six months of the training window, refit
    it on the whole window, and estimate validated price slopes from the price test."""
    q = warehouse.quotes()
    tr, _ = split(q, st.train_end, st.test_end)
    cut = pd.Timestamp(st.train_end) - pd.DateOffset(months=6)
    fit_part = tr[(tr["quote_date"] < cut) & (tr["decision_date"] < cut)]
    val_part = tr[tr["quote_date"] >= cut]
    candidates = {"logistic": Logistic, "gbm": GBM}
    validation = {}
    for name, cls in candidates.items():
        m = cls().fit(fit_part)
        validation[name] = scores(val_part["won"].to_numpy(), m.predict(val_part))
    champion = min(validation, key=lambda n: validation[n]["log_loss"])
    models = {name: cls().fit(tr) for name, cls in candidates.items()}
    slopes = experiment_slopes(tr, models[champion].predict)
    validated = {k: v for k, v in slopes.items() if v.precise(st.max_slope_se)}

    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    d = paths().registry / version
    d.mkdir(parents=True, exist_ok=True)
    for name, m in models.items():
        joblib.dump(m, d / f"win_{name}.joblib")
    meta = {
        "version": version,
        "champion": champion,
        "validation": validation,
        "train_window": {"quotes_before": str(st.train_end), "rows": len(tr)},
        "price_tests": {
            k: {
                "quotes": v.quotes,
                "slope_no_competitor": round(v.no_competitor, 3),
                "slope_competitor": round(v.competitor, 3),
                "se_no_competitor": round(v.se_no_competitor, 3),
                "se_competitor": round(v.se_competitor, 3),
                "validated": k in validated,
            }
            for k, v in slopes.items()
        },
        "validated_slopes": {k: {"no_competitor": v.no_competitor, "competitor": v.competitor} for k, v in validated.items()},
        "data_fingerprint": data_fingerprint(paths().raw),
        "code_version": _git_sha(),
    }
    (d / "metadata.json").write_text(json.dumps(meta, indent=2))
    (paths().registry / "current.json").write_text(json.dumps({"version": version}))
    return d


@dataclass
class Loaded:
    meta: dict
    champion: Logistic | GBM
    response: PriceResponse


def load_current(registry: Path | None = None) -> Loaded:
    registry = registry or paths().registry
    version = json.loads((registry / "current.json").read_text())["version"]
    d = registry / version
    meta = json.loads((d / "metadata.json").read_text())
    champ = joblib.load(d / f"win_{meta['champion']}.joblib")
    return Loaded(meta, champ, PriceResponse(champ.predict, meta["validated_slopes"]))
