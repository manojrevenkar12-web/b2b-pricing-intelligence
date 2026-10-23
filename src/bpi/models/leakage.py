"""Margin leakage between the price on the invoice and the money the business keeps.

Three kinds, each with an owner:

- reps whose off-invoice concessions (absorbed freight, free expedites, extended warranty, early
  payment discounts, payment terms beyond the standard) sit far above peers selling the same
  service lines in the same region: a conversation for the sales manager;
- invoices billed below the approved quote: a claim for Finance (from `fct_billing_exceptions`);
- rebates paid above the contracted rate: a recovery for contract management (from
  `fct_rebate_reconciliation`).

Rep outliers are judged against peers, not against the global average: a rep selling mostly
field service in the Middle East is compared with what field service in the Middle East usually
gives away.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CONCESSIONS = ["freight_eur", "expedite_eur", "warranty_eur", "early_payment_eur", "terms_cost_eur"]


def rep_outliers(w: pd.DataFrame, z_threshold: float = 3.0, min_invoices: int = 30, min_excess: float = 0.005) -> pd.DataFrame:
    """Robust z-score of each rep's peer-adjusted concession rate. A rep is flagged only when the
    excess is also material (at least `min_excess` of invoiced value), so a team that happens to
    be very uniform does not turn noise into findings."""
    w = w.copy()
    w["concessions"] = w[CONCESSIONS].sum(axis=1)
    w["rate"] = w["concessions"] / w["invoice_eur"]
    # Peer expectation: median rate of the service line in the region.
    w["expected"] = w.groupby(["service_line", "region"])["rate"].transform("median")
    w["excess"] = w["rate"] - w["expected"]
    reps = (
        w.groupby("rep_id")
        .agg(
            region=("region", "first"),
            invoices=("invoice_id", "count"),
            invoiced_eur=("invoice_eur", "sum"),
            concessions_eur=("concessions", "sum"),
            excess_rate=("excess", "median"),
            expected_eur=("expected", lambda s: float((s * w.loc[s.index, "invoice_eur"]).sum())),
        )
        .reset_index()
    )
    reps = reps[reps["invoices"] >= min_invoices].copy()
    med = reps["excess_rate"].median()
    mad = 1.4826 * (reps["excess_rate"] - med).abs().median()
    reps["z"] = (reps["excess_rate"] - med) / (mad if mad > 0 else np.nan)
    reps["flagged"] = (reps["z"] > z_threshold) & (reps["excess_rate"] - med >= min_excess)
    # Leakage estimate: concessions above what peers would have given on the same invoices.
    reps["excess_eur"] = (reps["concessions_eur"] - reps["expected_eur"]).clip(lower=0)
    return reps.sort_values("z", ascending=False).reset_index(drop=True)


def summary(w: pd.DataFrame, billing: pd.DataFrame, rebates: pd.DataFrame, z_threshold: float) -> dict:
    reps = rep_outliers(w, z_threshold)
    flagged = reps[reps["flagged"]]
    over = rebates[rebates["is_overpaid"]]
    total_pocket = float(w["pocket_eur"].sum())
    return {
        "reps_flagged": flagged["rep_id"].tolist(),
        "rep_excess_concessions_eur": round(float(flagged["excess_eur"].sum()), 2),
        "billing_exceptions": len(billing),
        "billing_exceptions_eur": round(float(billing["gap_eur"].sum()), 2),
        "rebate_overpaid_customers": sorted(over["customer_id"].unique().tolist()),
        "rebate_overpaid_eur": round(float(over["overpaid_eur"].sum()), 2),
        "incomplete_rebate_years": int((~rebates["is_complete"]).sum()),
        "pocket_eur": round(total_pocket, 2),
        "rep_table": reps,
    }
