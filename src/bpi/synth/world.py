"""A simulated Gas Services commercial world with a known truth.

Real quote and invoice data is confidential, and it never tells you the true price response, so a
pricing model built on it cannot be graded. This module simulates the systems a pricing team reads
(CRM, CPQ, ERP, market intelligence) for a service business around gas turbines, steam turbines
and generators, and keeps the truth that generated them:

- each quote's true win probability at *any* price, so a recommended price can be scored;
- an unrecorded deal risk that reps see and answer with bigger discounts. Deals in trouble get
  lower prices *and* lose more often, which is exactly why reading price sensitivity off history is
  biased;
- a randomised price test the pricing team ran on spare parts and field service for 18 months;
- planted margin leakage (reps who give away freight, expedites and payment terms; invoices billed
  below the approved quote; rebates paid above contract) and planted data-quality defects.

The raw files look like system exports: local currencies, codes, duplicates and defects included.
The truth goes to a separate folder that only the evaluation reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2022-01-01")
AS_OF = pd.Timestamp("2026-09-30")

REGIONS = ["EMEA", "AMERICAS", "APAC", "MIDDLE_EAST"]
REGION_CCY = {
    "EMEA": [("EUR", 0.75), ("GBP", 0.25)],
    "AMERICAS": [("USD", 1.0)],
    "APAC": [("USD", 1.0)],
    "MIDDLE_EAST": [("USD", 0.6), ("AED", 0.4)],
}
REGION_COUNTRIES = {
    "EMEA": ["DE", "IT", "ES", "NL", "GB", "PL", "TR"],
    "AMERICAS": ["US", "MX", "BR", "CA"],
    "APAC": ["KR", "TW", "TH", "AU", "MY"],
    "MIDDLE_EAST": ["AE", "SA", "QA", "OM"],
}
SEGMENTS = ["utility", "ipp", "oil_and_gas", "industrial"]
UNIT_TYPES = ["HD_GT", "IND_GT", "AERO_GT", "ST", "GEN"]

# service line -> (families, share of quotes, median list price EUR, cost/list, decision lag days)
LINES: dict[str, dict] = {
    "spare_parts": {
        "families": ["hot_gas_path_parts", "combustion_parts", "consumables", "controls_parts"],
        "share": 0.55,
        "median_list": 40_000,
        "cost_ratio": 0.52,
        "lag": (5, 25),
        "alpha": -0.66,
        "beta": -4.0,
        "base_discount": 0.10,
        "commodity": 0.6,
    },
    "field_service": {
        "families": ["inspection", "minor_outage", "major_outage", "troubleshooting"],
        "share": 0.22,
        "median_list": 150_000,
        "cost_ratio": 0.58,
        "lag": (10, 45),
        "alpha": -0.69,
        "beta": -3.0,
        "base_discount": 0.08,
        "commodity": 0.0,
    },
    "repairs": {
        "families": ["blade_repair", "vane_repair", "rotor_repair"],
        "share": 0.12,
        "median_list": 320_000,
        "cost_ratio": 0.55,
        "lag": (15, 60),
        "alpha": -0.81,
        "beta": -2.5,
        "base_discount": 0.08,
        "commodity": 0.4,
    },
    "upgrades": {
        "families": ["efficiency_upgrade", "emissions_upgrade", "controls_modernisation", "lifetime_extension"],
        "share": 0.08,
        "median_list": 3_000_000,
        "cost_ratio": 0.60,
        "lag": (60, 200),
        "alpha": -1.35,
        "beta": -2.0,
        "base_discount": 0.10,
        "commodity": 0.2,
    },
    "ltsa": {
        "families": ["ltsa_renewal", "ltsa_new"],
        "share": 0.03,
        "median_list": 20_000_000,
        "cost_ratio": 0.70,
        "lag": (90, 300),
        "alpha": -1.05,
        "beta": -1.5,
        "base_discount": 0.06,
        "commodity": 0.0,
    },
}
COMPETITOR_RATE = {"spare_parts": 0.55, "field_service": 0.45, "repairs": 0.50, "upgrades": 0.35, "ltsa": 0.25}
SEGMENT_EFFECT = {"utility": 0.10, "ipp": -0.15, "oil_and_gas": 0.05, "industrial": 0.0}
# CRM stage names and the default probabilities sales operations assigns to them.
STAGES = [("qualify", 0.10), ("propose", 0.30), ("negotiate", 0.60), ("commit", 0.80)]
TEST_CELLS = [-0.06, -0.03, 0.0, 0.03, 0.06]
OFF_INVOICE_TYPES = ["freight_absorbed", "expedite_free", "extended_warranty", "early_payment_discount"]


@dataclass(frozen=True)
class WorldConfig:
    seed: int = 7
    customers: int = 600
    reps_per_region: int = 15
    quotes_per_year: int = 9000
    leaky_reps: int = 5
    billing_error_rate: float = 0.008
    rebate_overpaid_customers: int = 6
    price_test: tuple[str, str] = ("2024-01-01", "2025-06-30")
    price_test_share: float = 0.35


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _syllable_name(rng: np.random.Generator, n: int, suffixes: list[str]) -> list[str]:
    a = ["Nor", "Sud", "Ost", "West", "Rhe", "Alt", "Vel", "Kar", "Mar", "Tor", "Lin", "Bre", "Sol", "Hal"]
    b = ["dan", "wer", "ka", "ven", "mar", "lis", "tor", "berg", "feld", "stad", "ria", "vik"]
    out = []
    for i in range(n):
        out.append(f"{rng.choice(a)}{rng.choice(b)} {rng.choice(suffixes)} {i:03d}")
    return out


class World:
    """Builds every table. Call `generate()` once, then `write(root)`."""

    def __init__(self, cfg: WorldConfig | None = None):
        self.cfg = cfg or WorldConfig()
        self.rng = np.random.default_rng(self.cfg.seed)
        self.tables: dict[str, pd.DataFrame] = {}
        self.truth: dict[str, pd.DataFrame] = {}
        self.truth_meta: dict = {}

    # ---- reference data ------------------------------------------------------------------------

    def _months(self) -> pd.DatetimeIndex:
        return pd.date_range(START, AS_OF, freq="MS")

    def _fx(self) -> pd.DataFrame:
        rng, months = self.rng, self._months()
        usd = 0.92 * np.exp(np.cumsum(rng.normal(0, 0.012, len(months))))
        gbp = 1.16 * np.exp(np.cumsum(rng.normal(0, 0.008, len(months))))
        aed = usd / 3.6725  # pegged to the dollar
        rows = []
        for i, m in enumerate(months):
            rows += [
                (m.date(), "EUR", 1.0),
                (m.date(), "USD", round(usd[i], 6)),
                (m.date(), "GBP", round(gbp[i], 6)),
                (m.date(), "AED", round(aed[i], 6)),
            ]
        return pd.DataFrame(rows, columns=["month", "currency", "eur_per_unit"])

    def _commodity(self) -> pd.DataFrame:
        months = self._months()
        t = np.arange(len(months))
        spike = 25 * np.exp(-0.5 * ((t - 4) / 3.0) ** 2)  # 2022 nickel spike
        idx = 100 + spike + np.cumsum(self.rng.normal(0.15, 1.6, len(months)))
        return pd.DataFrame({"month": [m.date() for m in months], "nickel_alloy_index": idx.round(2)})

    def _customers(self) -> pd.DataFrame:
        rng, n = self.rng, self.cfg.customers
        region = rng.choice(REGIONS, n, p=[0.38, 0.27, 0.20, 0.15])
        seg = rng.choice(SEGMENTS, n, p=[0.40, 0.25, 0.15, 0.20])
        country = [rng.choice(REGION_COUNTRIES[r]) for r in region]
        ccy = []
        for r in region:
            opts = REGION_CCY[r]
            ccy.append(rng.choice([c for c, _ in opts], p=[w for _, w in opts]))
        size = rng.lognormal(0, 1.0, n)  # relative fleet size drives quote volume
        strategic = size >= np.quantile(size, 0.95)
        start = rng.integers(1995, 2022, n)
        names = _syllable_name(rng, n, ["Energie", "Power", "Kraftwerke", "Generation", "Utilities", "Industrie"])
        return pd.DataFrame(
            {
                "customer_id": [f"C{i:04d}" for i in range(n)],
                "customer_name": names,
                "region": region,
                "country": country,
                "segment": seg,
                "billing_currency": ccy,
                "strategic_account": strategic,
                "relationship_since": start,
                "_size": size,
            }
        )

    def _reps(self) -> pd.DataFrame:
        rng, rows = self.rng, []
        for r in REGIONS:
            for i in range(self.cfg.reps_per_region):
                rows.append((f"R{r[:2]}{i:02d}", f"Sales rep {r[:2]}-{i:02d}", r))
        reps = pd.DataFrame(rows, columns=["rep_id", "rep_name", "region"])
        reps["_generosity"] = rng.normal(0, 0.03, len(reps))
        reps["_off_invoice_rate"] = rng.uniform(0.004, 0.010, len(reps))
        reps["_terms_extra_p"] = rng.uniform(0.03, 0.10, len(reps))
        leaky = rng.choice(len(reps), self.cfg.leaky_reps, replace=False)
        reps["_leaky"] = False
        reps.loc[leaky, "_leaky"] = True
        reps.loc[leaky, "_off_invoice_rate"] *= 2.0
        reps.loc[leaky, "_terms_extra_p"] = 0.30
        return reps

    def _products(self) -> pd.DataFrame:
        rng, rows = self.rng, []
        for li, (line, spec) in enumerate(LINES.items()):
            for fi, fam in enumerate(spec["families"]):
                for k in range(6):
                    unit = rng.choice(UNIT_TYPES)
                    lp = spec["median_list"] * rng.lognormal(0, 0.55)
                    rows.append(
                        (
                            f"P{li}{fi}{k:02d}-{fam[:4].upper()}",
                            line,
                            fam,
                            unit,
                            f"{fam.replace('_', ' ')} for {unit}",
                            lp,
                            spec["cost_ratio"] * rng.uniform(0.9, 1.1),
                        )
                    )
        return pd.DataFrame(
            rows,
            columns=["product_id", "service_line", "product_family", "unit_type", "description", "_list0", "_cost_ratio"],
        )

    def _price_list(self, products: pd.DataFrame) -> pd.DataFrame:
        """Yearly price list (valid January to December), raised 3-6% each year."""
        rows = []
        for _, p in products.iterrows():
            lp = p["_list0"]
            for y in range(START.year, AS_OF.year + 1):
                rows.append((p["product_id"], date(y, 1, 1), date(y, 12, 31), round(lp, 2)))
                lp *= 1 + self.rng.uniform(0.03, 0.06)
        return pd.DataFrame(rows, columns=["product_id", "valid_from", "valid_to", "list_price_eur"])

    def _competitor_levels(self) -> pd.DataFrame:
        """Latent competitor price level (ratio to our list) per region and family, drifting monthly."""
        rows, months = [], self._months()
        for r in REGIONS:
            for spec in LINES.values():
                for fam in spec["families"]:
                    lvl = np.log(self.rng.uniform(0.80, 0.95)) + np.cumsum(self.rng.normal(0, 0.012, len(months)))
                    for m, v in zip(months, lvl, strict=True):
                        rows.append((r, fam, m, float(np.exp(v))))
        return pd.DataFrame(rows, columns=["region", "product_family", "month", "level"])

    # ---- quotes and outcomes ----------------------------------------------------------------------

    def _quote_dates(self, n: int) -> pd.Series:
        """Seasonal arrivals (spring and autumn outage seasons) with ~6% yearly growth."""
        days = pd.date_range(START, AS_OF, freq="D")
        doy = days.dayofyear.to_numpy()
        years = (days - START).days.to_numpy() / 365.25
        w = (1 + 0.25 * np.cos(2 * np.pi * (doy - 90) / 182.6)) * (1.06**years)
        w[days.dayofweek.to_numpy() >= 5] *= 0.15
        idx = self.rng.choice(len(days), n, p=w / w.sum())
        return pd.Series(np.sort(days[idx]))

    def generate(self) -> World:
        rng, cfg = self.rng, self.cfg
        fx, commodity = self._fx(), self._commodity()
        customers, reps, products = self._customers(), self._reps(), self._products()
        price_list = self._price_list(products)
        comp_levels = self._competitor_levels()

        years = (AS_OF - START).days / 365.25
        n = int(cfg.quotes_per_year * years)
        qdate = self._quote_dates(n)

        line = rng.choice(list(LINES), n, p=[s["share"] for s in LINES.values()])
        # Customers quote in proportion to fleet size; reps cover their own region.
        cust_idx = rng.choice(len(customers), n, p=(customers["_size"] / customers["_size"].sum()).to_numpy())
        cust = customers.iloc[cust_idx].reset_index(drop=True)
        reps_by_region = {r: reps.index[reps["region"] == r].to_numpy() for r in REGIONS}
        # Each customer has one account owner and occasionally another rep from the region.
        owner = {c: rng.choice(reps_by_region[r]) for c, r in zip(customers["customer_id"], customers["region"], strict=True)}
        rep_idx = np.array(
            [
                owner[c] if rng.random() < 0.85 else rng.choice(reps_by_region[r])
                for c, r in zip(cust["customer_id"], cust["region"], strict=True)
            ]
        )
        rep = reps.iloc[rep_idx].reset_index(drop=True)

        prod_by_line = {ln: products.index[products["service_line"] == ln].to_numpy() for ln in LINES}
        prod_idx = np.array([rng.choice(prod_by_line[ln]) for ln in line])
        prod = products.iloc[prod_idx].reset_index(drop=True)

        year = qdate.dt.year.to_numpy()
        pl = price_list.assign(year=pd.to_datetime(price_list["valid_from"]).dt.year).set_index(["product_id", "year"])
        list_eur = pl.loc[list(zip(prod["product_id"], year, strict=True)), "list_price_eur"].to_numpy()
        # Larger units and scopes: the quoted quantity/scope scales the line value.
        scope = np.where(np.isin(line, ["spare_parts"]), rng.integers(1, 6, n), 1)
        list_eur = list_eur * scope

        spec = pd.DataFrame([LINES[ln] for ln in line])
        comp_p = np.array([COMPETITOR_RATE[ln] for ln in line]) + np.where(cust["region"] == "APAC", 0.1, 0.0)
        competitor = rng.random(n) < comp_p
        urgent = (rng.random(n) < 0.20) & np.isin(line, ["spare_parts", "field_service", "repairs"])
        hidden_risk = rng.normal(0, 1, n)
        rel_years = (qdate.dt.year.to_numpy() - cust["relationship_since"].to_numpy()).clip(0, 30)
        month_start = qdate.dt.to_period("M").dt.to_timestamp()
        lvl = comp_levels.set_index(["region", "product_family", "month"])["level"]
        comp_level = lvl.loc[list(zip(cust["region"], prod["product_family"], month_start, strict=True))].to_numpy()

        # Rep pricing: bigger discounts against competitors, for strategic accounts and for deals the
        # rep senses are at risk (hidden_risk, never recorded); smaller ones when the customer is in
        # an outage.
        discount = (
            spec["base_discount"].to_numpy()
            + 0.04 * competitor
            + 0.012 * np.clip(hidden_risk, -2, 2)
            + rep["_generosity"].to_numpy()
            + 0.02 * cust["strategic_account"].to_numpy()
            - 0.03 * urgent
            + rng.normal(0, 0.03, n)
        ).clip(0.0, 0.35)
        ratio = 1 - discount

        # The pricing team's randomised test: a share of spare parts and field service quotes got a
        # random price adjustment on top of the rep's price, logged in the CPQ.
        t0, t1 = pd.Timestamp(cfg.price_test[0]), pd.Timestamp(cfg.price_test[1])
        in_test = (
            (qdate >= t0).to_numpy()
            & (qdate <= t1).to_numpy()
            & np.isin(line, ["spare_parts", "field_service"])
            & (rng.random(n) < cfg.price_test_share)
        )
        cell = np.where(in_test, rng.choice(TEST_CELLS, n), np.nan)
        ratio = np.where(in_test, ratio * (1 + np.nan_to_num(cell)), ratio).clip(0.55, 1.0)

        beta = spec["beta"].to_numpy() * (1 + 0.5 * competitor)
        base_logit = (
            spec["alpha"].to_numpy()
            + 0.9 * urgent
            - 0.45 * competitor
            + 3.0 * competitor * np.log(comp_level)
            + 0.02 * rel_years
            + np.array([SEGMENT_EFFECT[s] for s in cust["segment"]])
            + 0.3 * cust["strategic_account"].to_numpy()
            - 0.6 * hidden_risk
        )
        p_win = _sigmoid(base_logit + beta * np.log(ratio))
        won = rng.random(n) < p_win

        lag_lo = spec["lag"].map(lambda x: x[0]).to_numpy()
        lag_hi = spec["lag"].map(lambda x: x[1]).to_numpy()
        decision = qdate + pd.to_timedelta(rng.integers(lag_lo, lag_hi + 1), unit="D")
        open_ = (decision > AS_OF).to_numpy()
        status = np.where(open_, "open", np.where(won, "won", "lost"))

        ccy = cust["billing_currency"].to_numpy()
        fx_m = fx.assign(month=pd.to_datetime(fx["month"])).set_index(["month", "currency"])["eur_per_unit"]
        rate = fx_m.loc[list(zip(month_start, ccy, strict=True))].to_numpy()
        list_lc = list_eur / rate
        net_lc = list_lc * ratio
        approval = np.select([discount > 0.25, discount > 0.15], ["vp", "deal_desk"], "rep")

        # CRM opportunity per quote: created before the quote, with an optimistic expected close.
        created = qdate - pd.to_timedelta(rng.integers(1, 30, n) + (lag_lo // 2), unit="D")
        lifetime = (decision - created).dt.days.to_numpy()
        expected_close = created + pd.to_timedelta((lifetime * rng.uniform(0.6, 1.0, n)).astype(int), unit="D")

        quotes = pd.DataFrame(
            {
                "quote_id": [f"Q{i:06d}" for i in range(n)],
                "opportunity_id": [f"O{i:06d}" for i in range(n)],
                "customer_id": cust["customer_id"],
                "rep_id": rep["rep_id"],
                "product_id": prod["product_id"],
                "quote_date": qdate.dt.date,
                "decision_date": np.where(open_, None, decision.dt.date),
                "currency": ccy,
                "list_price_lc": list_lc.round(2),
                "net_price_lc": net_lc.round(2),
                "scope_qty": scope,
                "outage_urgent": urgent,
                "competitor_present": competitor,
                "price_test_cell": cell,
                "approval_level": approval,
                "status": status,
            }
        )
        truth_q = pd.DataFrame(
            {
                "quote_id": quotes["quote_id"],
                "base_logit": base_logit,
                "beta": beta,
                "ratio": ratio,
                "p_win": p_win,
                "hidden_risk": hidden_risk,
                "list_eur": list_eur,
                "decision": decision.dt.date,
            }
        )

        # Unit cost: product cost ratio, moved by the alloy index for metal-heavy lines.
        com = commodity.assign(month=pd.to_datetime(commodity["month"])).set_index("month")["nickel_alloy_index"]
        # The ERP's standard cost per product and month; a quote's cost is unit cost x scope.
        cost_rows = []
        for _, pr in products.iterrows():
            k = LINES[pr["service_line"]]["commodity"]
            for mth, idx in com.items():
                lp_y = pl.loc[(pr["product_id"], mth.year), "list_price_eur"]
                cost_rows.append((pr["product_id"], mth.date(), round(lp_y * pr["_cost_ratio"] * (1 + k * (idx / 100 - 1)), 2)))
        std_cost = pd.DataFrame(cost_rows, columns=["product_id", "month", "unit_cost_eur"])
        sc = std_cost.assign(month=pd.to_datetime(std_cost["month"])).set_index(["product_id", "month"])["unit_cost_eur"]
        truth_q["cost_eur"] = sc.loc[list(zip(prod["product_id"], month_start, strict=True))].to_numpy() * scope

        opps = pd.DataFrame(
            {
                "opportunity_id": quotes["opportunity_id"],
                "customer_id": quotes["customer_id"],
                "owner_rep_id": quotes["rep_id"],
                "service_line": line,
                "created_at": created.dt.date,
                "expected_close_date": expected_close.dt.date,
                "amount_estimate": (net_lc * rng.uniform(0.9, 1.25, n)).round(0),
                "currency": ccy,
                "status": np.where(open_, "open", np.where(won, "closed_won", "closed_lost")),
                "closed_at": np.where(open_, None, decision.dt.date),
            }
        )
        span = (decision - created).dt.days.to_numpy()
        parts = []
        for k, (st, _) in enumerate(STAGES):
            entered = created + pd.to_timedelta((span * k / len(STAGES)).astype(int), unit="D")
            keep = (entered <= AS_OF).to_numpy()
            parts.append(pd.DataFrame({"opportunity_id": opps["opportunity_id"][keep], "stage": st, "entered_at": entered[keep].dt.date}))
        parts.append(
            pd.DataFrame({"opportunity_id": opps["opportunity_id"][~open_], "stage": "closed", "entered_at": decision[~open_].dt.date})
        )
        stage_hist = pd.concat(parts).sort_values(["opportunity_id", "entered_at"], kind="stable").reset_index(drop=True)

        # ---- invoices and off-invoice concessions for won quotes ----
        w = quotes[quotes["status"] == "won"].copy()
        wi = w.index.to_numpy()
        m = len(w)
        inv_date = pd.to_datetime(w["decision_date"]) + pd.to_timedelta(rng.integers(10, 90, m), unit="D")
        keep = (inv_date <= AS_OF).to_numpy()
        w, wi, inv_date = w[keep], wi[keep], inv_date[keep]
        m = len(w)
        billing_error = rng.random(m) < cfg.billing_error_rate
        err_size = rng.uniform(0.03, 0.15, m)
        amount = w["net_price_lc"].to_numpy() * np.where(billing_error, 1 - err_size, 1.0)
        rep_w = reps.set_index("rep_id").loc[w["rep_id"]]
        terms = np.where(rng.random(m) < rep_w["_terms_extra_p"].to_numpy(), rng.choice([60, 90, 120], m), 30)
        invoices = pd.DataFrame(
            {
                "invoice_id": [f"INV{i:07d}" for i in range(m)],
                "quote_id": w["quote_id"].to_numpy(),
                "customer_id": w["customer_id"].to_numpy(),
                "invoice_date": inv_date.dt.date.to_numpy(),
                "currency": w["currency"].to_numpy(),
                "amount_lc": amount.round(2),
                "payment_terms_days": terms,
            }
        )
        # Off-invoice items: each invoice gets a few, sized by the rep's habits. Leaky reps give
        # away freight and expedites far more often and in larger amounts.
        rates = rep_w["_off_invoice_rate"].to_numpy()
        leaky = rep_w["_leaky"].to_numpy()
        base_p = {"freight_absorbed": 0.35, "expedite_free": 0.25, "extended_warranty": 0.18, "early_payment_discount": 0.14}
        parts = []
        for t, p0 in base_p.items():
            physical = t in ("freight_absorbed", "expedite_free")
            hit = rng.random(m) < p0 + np.where(leaky & physical, 0.12, 0.0)
            share = rates * rng.uniform(0.5, 1.5, m) * (2.0 if physical else 1.0)
            parts.append(
                pd.DataFrame({"invoice_id": invoices["invoice_id"][hit], "item_type": t, "amount_lc": (amount * share)[hit].round(2)})
            )
        off_invoice = pd.concat(parts).sort_values(["invoice_id", "item_type"]).reset_index(drop=True)

        # Contracts and yearly rebate payments (EUR), some paid above the contracted rate.
        contracts = pd.DataFrame(
            {
                "customer_id": customers["customer_id"],
                "rebate_rate": np.where(customers["strategic_account"], 0.03, rng.choice([0.0, 0.0, 0.01, 0.02], len(customers))),
                "standard_terms_days": 30,
                "valid_from": date(2020, 1, 1),
                "valid_to": date(2027, 12, 31),
            }
        )
        inv_eur = invoices.assign(
            month=pd.to_datetime(invoices["invoice_date"]).dt.to_period("M").dt.to_timestamp(),
            year=pd.to_datetime(invoices["invoice_date"]).dt.year,
        )
        inv_rate = fx_m.loc[list(zip(inv_eur["month"], inv_eur["currency"], strict=True))].to_numpy()
        inv_eur["eur"] = inv_eur["amount_lc"] * inv_rate
        annual = inv_eur[inv_eur["year"] < AS_OF.year].groupby(["customer_id", "year"])["eur"].sum().reset_index()
        annual = annual.merge(contracts[["customer_id", "rebate_rate"]], on="customer_id")
        annual = annual[annual["rebate_rate"] > 0].reset_index(drop=True)
        eligible = annual["customer_id"].unique()
        over = rng.choice(eligible, min(cfg.rebate_overpaid_customers, len(eligible)), replace=False)
        factor = np.where(annual["customer_id"].isin(over), rng.uniform(1.6, 2.4, len(annual)), 1.0)
        annual["amount_eur"] = (annual["eur"] * annual["rebate_rate"] * factor).round(2)
        rebates = annual.assign(payment_id=[f"RB{i:05d}" for i in range(len(annual))])[["payment_id", "customer_id", "year", "amount_eur"]]
        overpaid = (annual["amount_eur"] - annual["eur"] * annual["rebate_rate"])[annual["customer_id"].isin(over)]

        # Market intelligence: noisy observations of competitor price levels.
        k = 2500
        pick = comp_levels.sample(k, random_state=int(rng.integers(1_000_000))).reset_index(drop=True)
        intel = pd.DataFrame(
            {
                "observed_at": (pick["month"] + pd.to_timedelta(rng.integers(0, 28, k), unit="D")).dt.date,
                "region": pick["region"],
                "product_family": pick["product_family"],
                "competitor": rng.choice(["Competitor A", "Competitor B", "Independent shop"], k),
                "price_index_vs_list": (pick["level"] * np.exp(rng.normal(0, 0.05, k))).round(3),
                "source": rng.choice(["lost-deal debrief", "tender result", "distributor feedback"], k),
            }
        )

        self.tables = {
            "crm_accounts": customers.drop(columns=["_size"]),
            "crm_users": reps[["rep_id", "rep_name", "region"]],
            "crm_opportunities": opps,
            "crm_opportunity_stages": stage_hist,
            "cpq_quotes": quotes,
            "erp_products": products[["product_id", "service_line", "product_family", "unit_type", "description"]],
            "erp_price_list": price_list,
            "erp_standard_cost": std_cost,
            "erp_invoices": invoices,
            "erp_off_invoice": off_invoice,
            "erp_rebate_payments": rebates,
            "contracts": contracts,
            "market_competitor_intel": intel,
            "market_commodity_index": commodity,
            "fx_rates": fx,
        }
        self.truth = {"quotes": truth_q}
        self.truth_meta = {
            "leaky_reps": sorted(reps.loc[reps["_leaky"], "rep_id"].tolist()),
            "billing_error_invoices": sorted(invoices.loc[billing_error, "invoice_id"].tolist()),
            "billing_error_eur": float(((w["net_price_lc"].to_numpy() - amount) * inv_rate)[billing_error].sum()),
            "rebate_overpaid_customers": sorted(map(str, over)),
            "rebate_overpaid_eur": float(overpaid.sum()),
            "price_test": list(cfg.price_test),
            "stage_probabilities": dict(STAGES),
            "lines": {k: {"beta": v["beta"], "alpha": v["alpha"]} for k, v in LINES.items()},
        }
        self._plant_defects()
        return self

    # ---- data-quality defects, as real exports have them ------------------------------------------

    def _plant_defects(self) -> None:
        rng, issues = self.rng, []
        q = self.tables["cpq_quotes"]
        idx = rng.choice(len(q), 160, replace=False)
        kinds = np.array(["bad_currency", "non_positive_price", "future_date", "missing_rep"])[np.arange(160) % 4]
        q = q.copy()
        for i, kind in zip(idx, kinds, strict=True):
            if kind == "bad_currency":
                q.loc[i, "currency"] = rng.choice(["EURO", "", "usd "])
            elif kind == "non_positive_price":
                q.loc[i, "net_price_lc"] = rng.choice([0.0, -q.loc[i, "net_price_lc"]])
            elif kind == "future_date":
                q.loc[i, "quote_date"] = date(2027, rng.integers(1, 12), 15)
            else:
                q.loc[i, "rep_id"] = None
            issues.append(("cpq_quotes", q.loc[i, "quote_id"], kind))
        self.tables["cpq_quotes"] = q

        # The CRM export re-sends some opportunities: exact duplicates of the same row.
        o = self.tables["crm_opportunities"]
        dup = o.sample(250, random_state=int(rng.integers(1_000_000)))
        self.tables["crm_opportunities"] = pd.concat([o, dup]).sort_values("opportunity_id").reset_index(drop=True)
        issues += [("crm_opportunities", i, "duplicate_row") for i in dup["opportunity_id"]]

        # One ERP interface sent amounts in cents for two weeks.
        inv = self.tables["erp_invoices"].copy()
        d = pd.to_datetime(inv["invoice_date"])
        cents = (d >= "2025-03-03") & (d <= "2025-03-16") & (inv["currency"] == "USD")
        inv.loc[cents, "amount_lc"] = (inv.loc[cents, "amount_lc"] * 100).round(2)
        self.tables["erp_invoices"] = inv
        issues += [("erp_invoices", i, "amount_in_cents") for i in inv.loc[cents, "invoice_id"]]

        # Rebate payments booked against customer ids that do not exist.
        rb = self.tables["erp_rebate_payments"].copy()
        bad = rb.sample(4, random_state=int(rng.integers(1_000_000))).index
        rb.loc[bad, "customer_id"] = [f"C9{int(x):03d}" for x in rng.integers(0, 999, len(bad))]
        self.tables["erp_rebate_payments"] = rb
        issues += [("erp_rebate_payments", rb.loc[i, "payment_id"], "unknown_customer") for i in bad]

        self.truth["dq_issues"] = pd.DataFrame(issues, columns=["source", "row_key", "issue"])

    # ---- output -------------------------------------------------------------------------------------

    def write(self, raw: Path, truth: Path) -> None:
        raw.mkdir(parents=True, exist_ok=True)
        truth.mkdir(parents=True, exist_ok=True)
        for name, df in self.tables.items():
            df.to_csv(raw / f"{name}.csv", index=False)
        for name, df in self.truth.items():
            df.to_parquet(truth / f"{name}.parquet", index=False)
        (truth / "meta.json").write_text(json.dumps(self.truth_meta, indent=2, default=str))


def true_p_win(truth_quotes: pd.DataFrame, ratio: np.ndarray | float) -> np.ndarray:
    """True win probability of each quote if it had been priced at `ratio` of list."""
    return _sigmoid(truth_quotes["base_logit"].to_numpy() + truth_quotes["beta"].to_numpy() * np.log(ratio))
