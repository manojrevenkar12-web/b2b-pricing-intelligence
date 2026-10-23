"""Win probability of a quote at a given price.

Three models are compared on quotes decided after the training window:

- `segment_rate`: the historical win rate of the service line in the region (what a spreadsheet
  would use);
- `logistic`: logistic regression with price interacted with service line and competitor presence;
  its coefficients are readable by a pricing manager;
- `gbm`: LightGBM, forced to be monotone in price (a higher price can never raise the win
  probability), which keeps price recommendations sane where data is thin.

Price enters every model as log(net price / list price), so the same model scores any candidate
price for a quote.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

CATEGORICAL = ["service_line", "product_family", "region", "segment", "unit_type"]
NUMERIC = [
    "log_ratio",
    "log_list_eur",
    "competitor_present",
    "log_competitor_index",
    "outage_urgent",
    "strategic_account",
    "relationship_years",
    "prior_win_rate",
    "log_prior_decided",
    "month_of_year",
]
LINES = ["spare_parts", "field_service", "repairs", "upgrades", "ltsa"]


def features(q: pd.DataFrame, ratio: np.ndarray | pd.Series | None = None) -> pd.DataFrame:
    """Model inputs for quotes, optionally re-priced at `ratio` of list."""
    r = q["price_ratio"].to_numpy() if ratio is None else np.asarray(ratio, dtype=float)
    ci = q["competitor_index"].fillna(q["competitor_index"].median() if q["competitor_index"].notna().any() else 0.87)
    f = pd.DataFrame(
        {
            "service_line": q["service_line"].astype(str),
            "product_family": q["product_family"].astype(str),
            "region": q["region"].astype(str),
            "segment": q["segment"].astype(str),
            "unit_type": q["unit_type"].astype(str),
            "log_ratio": np.log(r),
            "log_list_eur": np.log(q["list_eur"].astype(float)),
            "competitor_present": q["competitor_present"].astype(float),
            "log_competitor_index": np.log(ci.astype(float)),
            "outage_urgent": q["outage_urgent"].astype(float),
            "strategic_account": q["strategic_account"].astype(float),
            "relationship_years": q["relationship_years"].astype(float),
            "prior_win_rate": q["prior_win_rate"].astype(float),
            "log_prior_decided": np.log1p(q["prior_decided"].astype(float)),
            "month_of_year": pd.to_datetime(q["quote_date"]).dt.month.astype(float),
        },
        index=q.index,
    )
    # Price interactions for the logistic model: price sensitivity differs by line and competition.
    for line in LINES:
        f[f"log_ratio_x_{line}"] = f["log_ratio"] * (f["service_line"] == line)
    f["log_ratio_x_competitor"] = f["log_ratio"] * f["competitor_present"]
    f["competitor_x_log_index"] = f["competitor_present"] * f["log_competitor_index"]
    return f


@dataclass
class SegmentRate:
    rates: pd.Series
    overall: float

    @classmethod
    def fit(cls, q: pd.DataFrame) -> SegmentRate:
        return cls(q.groupby(["service_line", "region"])["won"].mean(), float(q["won"].mean()))

    def predict(self, q: pd.DataFrame, ratio=None) -> np.ndarray:
        idx = pd.MultiIndex.from_frame(q[["service_line", "region"]].astype(str))
        return self.rates.reindex(idx).fillna(self.overall).to_numpy()


PRICE_TERMS = [f"log_ratio_x_{line}" for line in LINES] + ["log_ratio_x_competitor"]


class Logistic:
    def __init__(self, C: float = 10.0):
        num = [c for c in NUMERIC if c != "log_ratio"] + ["competitor_x_log_index"]
        self.pipe = Pipeline(
            [
                (
                    "prep",
                    ColumnTransformer(
                        [
                            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
                            ("num", StandardScaler(), num),
                            ("price", StandardScaler(with_mean=False), PRICE_TERMS),
                        ]
                    ),
                ),
                ("lr", LogisticRegression(C=C, max_iter=5000)),
            ]
        )

    def fit(self, q: pd.DataFrame) -> Logistic:
        self.pipe.fit(features(q), q["won"].to_numpy())
        return self

    def predict(self, q: pd.DataFrame, ratio=None) -> np.ndarray:
        return self.pipe.predict_proba(features(q, ratio))[:, 1]

    def price_slopes(self) -> dict[str, dict[str, float]]:
        """d logit / d log(price ratio) per service line, without and with a competitor."""
        coef = self.pipe.named_steps["lr"].coef_[0][-len(PRICE_TERMS) :]
        scale = self.pipe.named_steps["prep"].named_transformers_["price"].scale_
        b = coef / scale
        return {line: {"no_competitor": float(b[i]), "competitor": float(b[i] + b[-1])} for i, line in enumerate(LINES)}


class GBM:
    def __init__(self, monotone: bool = True, seed: int = 0):
        self.monotone = monotone
        self.seed = seed
        self.cols = CATEGORICAL + NUMERIC
        self.model: lgb.LGBMClassifier | None = None
        self.categories: dict[str, list[str]] = {}

    def _frame(self, q: pd.DataFrame, ratio=None) -> pd.DataFrame:
        f = features(q, ratio)[self.cols].copy()
        for c in CATEGORICAL:
            f[c] = pd.Categorical(f[c], categories=self.categories[c])
        return f

    def fit(self, q: pd.DataFrame) -> GBM:
        f = features(q)
        self.categories = {c: sorted(f[c].unique()) for c in CATEGORICAL}
        mono = [-1 if (c == "log_ratio" and self.monotone) else 0 for c in self.cols]
        self.model = lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.03,
            num_leaves=15,
            min_child_samples=100,
            reg_lambda=5.0,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            monotone_constraints=mono,
            monotone_constraints_method="advanced",
            random_state=self.seed,
            verbose=-1,
        )
        self.model.fit(self._frame(q), q["won"].to_numpy())
        return self

    def predict(self, q: pd.DataFrame, ratio=None) -> np.ndarray:
        assert self.model is not None
        return self.model.predict_proba(self._frame(q, ratio))[:, 1]


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error over equal-count bins."""
    order = np.argsort(p)
    err = 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk):
            err += len(chunk) / len(p) * abs(y[chunk].mean() - p[chunk].mean())
    return float(err)


def scores(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "log_loss": round(float(log_loss(y, p)), 4),
        "brier": round(float(brier_score_loss(y, p)), 4),
        "auc": round(float(roc_auc_score(y, p)), 4) if len(np.unique(y)) > 1 else float("nan"),
        "ece": round(ece(y, p), 4),
    }


def split(q: pd.DataFrame, train_end, test_end) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on quotes issued and decided before `train_end`; test on quotes issued after it."""
    train_end, test_end = pd.Timestamp(train_end), pd.Timestamp(test_end)
    d = q[q["is_decided"]]
    train = d[(d["quote_date"] < train_end) & (d["decision_date"] < train_end)]
    test = d[(d["quote_date"] >= train_end) & (d["decision_date"] < test_end)]
    return train.reset_index(drop=True), test.reset_index(drop=True)
