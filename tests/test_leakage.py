import numpy as np
import pandas as pd

from bpi.models.leakage import rep_outliers


def waterfall(rng):
    rows = []
    for rep in range(30):
        region = "MIDDLE_EAST" if rep < 6 else "EMEA"
        base = 0.04 if region == "MIDDLE_EAST" else 0.015  # a region that simply gives more
        extra = 0.03 if rep == 10 else 0.0
        for i in range(80):
            inv = 100_000.0
            rows.append(
                {
                    "invoice_id": f"{rep}-{i}",
                    "rep_id": f"R{rep}",
                    "region": region,
                    "service_line": "spare_parts",
                    "invoice_eur": inv,
                    "freight_eur": inv * (base + extra) * rng.uniform(0.7, 1.3),
                    "expedite_eur": 0.0,
                    "warranty_eur": 0.0,
                    "early_payment_eur": 0.0,
                    "terms_cost_eur": 0.0,
                }
            )
    return pd.DataFrame(rows)


def test_flags_the_outlier_not_the_generous_region():
    reps = rep_outliers(waterfall(np.random.default_rng(0)), z_threshold=3.0)
    assert reps[reps["flagged"]]["rep_id"].tolist() == ["R10"]


def test_reps_with_few_invoices_are_not_judged():
    w = waterfall(np.random.default_rng(1))
    w = w[~((w["rep_id"] == "R10") & (w.index % 80 >= 10))]
    assert "R10" not in rep_outliers(w)["rep_id"].tolist()
