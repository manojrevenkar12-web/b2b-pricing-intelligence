"""Grades every part of the pipeline against the simulation's truth.

Only this module reads `data/truth`. It answers the questions a pricing lead would ask before
trusting the system: does the data layer catch bad records, are win probabilities right, is the
measured price sensitivity right, would the recommendations have made money, are the leakage
findings real, how wrong is the forecast, and does the assistant answer correctly and stay inside
its permissions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from bpi import warehouse
from bpi.assistant import evaluate as assistant_eval
from bpi.config import REPO, Settings, paths
from bpi.models import forecast as fc
from bpi.models import leakage
from bpi.models.price_response import PriceResponse, average_effect, experiment_slopes
from bpi.models.recommend import bounds, policy_ratios
from bpi.models.win import GBM, Logistic, SegmentRate, scores, split
from bpi.monitor import check as monitor_check
from bpi.pipeline import load_current
from bpi.synth.world import true_p_win

LINES = ["spare_parts", "field_service", "repairs", "upgrades", "ltsa"]


def _truth() -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    t = paths().truth
    return (
        pd.read_parquet(t / "quotes.parquet").set_index("quote_id"),
        json.loads((t / "meta.json").read_text()),
        pd.read_parquet(t / "dq_issues.parquet"),
    )


def data_quality(dq: pd.DataFrame) -> dict:
    qr = warehouse.read("select source, row_key, reason from raw._quarantine")
    planted = dq.assign(k=dq["source"] + "|" + dq["row_key"].astype(str))
    qr["k"] = qr["source"] + "|" + qr["row_key"].astype(str)
    caught = planted["k"].isin(set(qr["k"]))
    read = sum(len(pd.read_csv(p, usecols=[0])) for p in paths().raw.glob("*.csv"))
    root = qr[~qr["reason"].str.startswith("parent_quarantined")]
    false_q = root[~root["k"].isin(set(planted["k"]))]
    run_results = json.loads((paths().root / "out" / "dbt" / "target" / "run_results.json").read_text())
    tests = [r for r in run_results["results"] if r["unique_id"].startswith("test.")]
    return {
        "planted_defects": len(planted),
        "caught": int(caught.sum()),
        "caught_share": round(float(caught.mean()), 4),
        "by_issue": planted.assign(caught=caught).groupby("issue")["caught"].agg(["sum", "count"]).astype(int).to_dict("index"),
        "rows_read": int(read),
        "false_quarantine": len(false_q),
        "false_quarantine_rate": round(len(false_q) / read, 5),
        "held_because_parent_held": int(qr["reason"].str.startswith("parent_quarantined").sum()),
        "dbt_tests": len(tests),
        "dbt_tests_failed": sum(r["status"] != "pass" for r in tests),
    }


def win_models(tr: pd.DataFrame, te: pd.DataFrame, truth: pd.DataFrame, champion_name: str) -> tuple[dict, dict]:
    y = te["won"].to_numpy()
    models: dict[str, Any] = {
        "segment_rate": SegmentRate.fit(tr),
        "logistic": Logistic().fit(tr),
        "gbm": GBM().fit(tr),
        "gbm_unconstrained": GBM(monotone=False).fit(tr),
    }
    out = {name: scores(y, m.predict(te)) for name, m in models.items()}
    out["oracle (true probabilities)"] = scores(y, truth.loc[te["quote_id"], "p_win"].to_numpy())
    by_line = {}
    champ = models[champion_name]
    for line in LINES:
        m = (te["service_line"] == line).to_numpy()
        if m.sum() > 50:
            by_line[line] = scores(y[m], champ.predict(te[m]))
    return {"test_quotes": len(te), "models": out, "champion": champion_name, "champion_by_line": by_line}, models


def price_response(tr, te, truth, models, champion_name, st: Settings) -> tuple[dict, dict]:
    champ = models[champion_name]
    lr = models["logistic"]
    slopes = experiment_slopes(tr, champ.predict)
    exp = PriceResponse(champ.predict, {k: {"no_competitor": v.no_competitor, "competitor": v.competitor} for k, v in slopes.items()})

    def true_pred(q, r):
        return true_p_win(truth.loc[q["quote_id"]], r)

    obs_slopes = lr.price_slopes()
    rows = {}
    for line in LINES:
        g = te[te["service_line"] == line]
        row = {
            "true_effect_of_5pct_price_rise": round(average_effect(true_pred, g), 4),
            "observational_logistic": round(average_effect(lr.predict, g), 4),
            "observational_gbm": round(average_effect(models["gbm"].predict, g), 4),
            "observational_slope": {k: round(v, 2) for k, v in obs_slopes[line].items()},
            "true_slope": {
                "no_competitor": round(float(truth.loc[g[~g["competitor_present"]]["quote_id"], "beta"].mean()), 2)
                if (~g["competitor_present"]).any()
                else None,
                "competitor": round(float(truth.loc[g[g["competitor_present"]]["quote_id"], "beta"].mean()), 2)
                if g["competitor_present"].any()
                else None,
            },
        }
        if line in slopes:
            s = slopes[line]
            row["experiment"] = round(average_effect(exp.predict, g), 4)
            row["experiment_slope"] = {
                "no_competitor": round(s.no_competitor, 2),
                "competitor": round(s.competitor, 2),
                "ci95_no_competitor": [round(x, 2) for x in s.ci95("no_competitor")],
                "ci95_competitor": [round(x, 2) for x in s.ci95("competitor")],
                "test_quotes": s.quotes,
                "validated": s.precise(st.max_slope_se),
            }
        rows[line] = row
    return rows, {k: v for k, v in slopes.items() if v.precise(st.max_slope_se)}


def recommendations(te, truth, models, champion_name, validated, st: Settings, seed: int = 0) -> dict:
    g = st.guardrails
    champ, lr = models[champion_name], models["logistic"]
    tq = truth.loc[te["quote_id"]]
    L, C, r0 = te["list_eur"].to_numpy(), te["cost_eur"].to_numpy(), te["price_ratio"].to_numpy()

    def true_em(r):
        p = true_p_win(tq, r)
        return p * (r * L - C), p

    em0, p0 = true_em(r0)

    class Oracle:
        def __init__(self, lines):
            self.lines = lines

        def validated(self, line):
            return line in self.lines

        def predict(self, q, r):
            return true_p_win(truth.loc[q["quote_id"]], r)

    non_desk = [x for x in LINES if x not in g.deal_desk_lines]
    production = PriceResponse(
        champ.predict, {k: {"no_competitor": v.no_competitor, "competitor": v.competitor} for k, v in validated.items()}
    )
    observational = PriceResponse(lr.predict, {k: v for k, v in lr.price_slopes().items() if k in non_desk})
    policies: dict[str, Any] = {
        "production (price-test slopes, validated lines only)": production,
        "observational slopes on every line": observational,
        "oracle on validated lines (best possible)": Oracle(list(validated)),
        "oracle on every line (if every line had a test)": Oracle(non_desk),
    }
    lo, hi, _floor = bounds(te, g)
    out: dict = {"test_quotes": len(te), "policies": {}}
    rng = np.random.default_rng(seed)
    vmask = te["service_line"].isin(list(validated)).to_numpy()
    for name, pol in policies.items():
        r, status = policy_ratios(te, pol, g)
        em, p = true_em(r)
        moved = ~np.isclose(r, r0)
        violations = int((moved & ((r < lo - 1e-9) | (r > hi + 1e-9))).sum())
        row = {
            "margin_uplift_all": round(float(em.sum() / em0.sum() - 1), 4),
            "margin_uplift_validated_lines": round(float(em[vmask].sum() / em0[vmask].sum() - 1), 4),
            "expected_margin_gain_eur": round(float(em.sum() - em0.sum()), 0),
            "win_rate_change": round(float(p.mean() - p0.mean()), 4),
            "win_rate_change_validated_lines": round(float(p[vmask].mean() - p0[vmask].mean()), 4),
            "average_price_change": round(float((r / r0 - 1).mean()), 4),
            "quotes_repriced": int(moved.sum()),
            "guardrail_violations": violations,
            "by_line": {
                line: {
                    "margin_uplift": round(float(em[m].sum() / em0[m].sum() - 1), 4),
                    "win_rate_change": round(float(p[m].mean() - p0[m].mean()), 4),
                    "average_price_change": round(float((r[m] / r0[m] - 1).mean()), 4),
                }
                for line in LINES
                if (m := (te["service_line"] == line).to_numpy()).any()
            },
        }
        if name.startswith("production"):
            idx = np.flatnonzero(vmask)
            boots = []
            for _ in range(1000):
                b = rng.choice(idx, len(idx))
                boots.append(em[b].sum() / em0[b].sum() - 1)
            row["uplift_ci95_validated_lines"] = [round(float(np.quantile(boots, 0.025)), 4), round(float(np.quantile(boots, 0.975)), 4)]
            row["status_counts"] = pd.Series(status).value_counts().to_dict()
        out["policies"][name] = row
    return out


def leakage_eval(meta: dict, st: Settings) -> dict:
    w = warehouse.read("select * from marts.fct_price_waterfall")
    b = warehouse.read("select * from marts.fct_billing_exceptions")
    rb = warehouse.read("select * from marts.fct_rebate_reconciliation")
    s = leakage.summary(w, b, rb, st.leakage_z)
    loaded_inv = set(w["invoice_id"])
    planted_bill = set(meta["billing_error_invoices"]) & loaded_inv
    found_bill = set(b["invoice_id"])
    reps_true, reps_found = set(meta["leaky_reps"]), set(s["reps_flagged"])
    reb_true, reb_found = set(meta["rebate_overpaid_customers"]), set(s["rebate_overpaid_customers"])

    def pr(found, true):
        tp = len(found & true)
        return (tp / len(found) if found else 1.0), (tp / len(true) if true else 1.0)

    rp, rr = pr(reps_found, reps_true)
    bp, br = pr(found_bill, planted_bill)
    cp, cr = pr(reb_found, reb_true)
    return {
        "reps": {
            "flagged": sorted(reps_found),
            "planted": sorted(reps_true),
            "precision": round(rp, 3),
            "recall": round(rr, 3),
            "excess_concessions_eur": s["rep_excess_concessions_eur"],
        },
        "billing": {
            "found": len(found_bill),
            "planted_loaded": len(planted_bill),
            "precision": round(bp, 3),
            "recall": round(br, 3),
            "gap_found_eur": s["billing_exceptions_eur"],
            "planted_gap_eur": round(meta["billing_error_eur"], 0),
        },
        "rebates": {
            "flagged": sorted(reb_found),
            "planted": sorted(reb_true),
            "precision": round(cp, 3),
            "recall": round(cr, 3),
            "overpaid_found_eur": s["rebate_overpaid_eur"],
            "planted_overpaid_eur": round(meta["rebate_overpaid_eur"], 0),
            "incomplete_years": s["incomplete_rebate_years"],
        },
        "rep_table": s["rep_table"].head(10).round(4).to_dict("records"),
    }


def forecast_eval(meta: dict) -> tuple[dict, pd.DataFrame]:
    b = warehouse.read("select month, service_line, sum(booked_eur) booked_eur from marts.fct_bookings_monthly group by 1, 2")
    s = warehouse.read("select s.*, q.net_eur from marts.fct_pipeline_snapshots s left join marts.fct_quotes q using (quote_id)")
    f = fc.Forecaster(b, s, meta["stage_probabilities"])
    valid = list(pd.date_range("2024-01-01", "2025-04-01", freq="MS"))
    test = list(pd.date_range("2025-07-01", "2026-07-01", freq="MS"))
    bt = f.backtest(valid + test, [1, 2, 3])
    r = fc.select_and_score(bt, valid, test)
    rows = r.pop("champion_rows")
    r["validation_origins"] = [str(valid[0].date()), str(valid[-1].date())]
    r["test_origins"] = [str(test[0].date()), str(test[-1].date())]
    return r, rows


def run(st: Settings) -> dict:
    truth, meta, dq = _truth()
    q = warehouse.quotes()
    tr, te = split(q, st.train_end, st.test_end)
    loaded = load_current()
    res: dict = {"as_of": str(st.as_of), "model_version": loaded.meta["version"]}
    res["data_quality"] = data_quality(dq)
    res["win_model"], models = win_models(tr, te, truth, loaded.meta["champion"])
    res["price_response"], validated = price_response(tr, te, truth, models, loaded.meta["champion"], st)
    res["recommendations"] = recommendations(te, truth, models, loaded.meta["champion"], validated, st)
    res["leakage"] = leakage_eval(meta, st)
    res["forecast"], fc_rows = forecast_eval(meta)
    adir = REPO / "tests" / "assistant"
    curated = assistant_eval.run(cases_path=adir / "questions.yaml")
    regression = assistant_eval.run(cases_path=adir / "regression_heldout_v1.yaml")
    heldout = assistant_eval.run(cases_path=adir / "heldout.yaml") if (adir / "heldout.yaml").exists() else None
    res["assistant"] = {"curated": curated, "regression_v1": regression, "heldout": heldout}
    recent_start = pd.Timestamp(st.as_of) - pd.DateOffset(months=3)
    recent = q[q["quote_date"] > recent_start]
    recent_decided = q[q["is_decided"] & (q["decision_date"] > recent_start) & (q["quote_date"] >= pd.Timestamp(st.train_end))]
    res["monitoring"] = monitor_check(tr, recent, recent_decided, loaded.champion.predict, st.drift_psi_alert, st.calibration_ece_alert)
    res["measures"] = measures(res)
    _figures(res, te, truth, models, loaded.meta["champion"], validated, fc_rows)
    h1 = fc_rows[fc_rows["horizon"] == 1].groupby(["target", "service_line"])[["forecast", "actual", "lo", "hi"]].sum().reset_index()
    h1["target"] = h1["target"].dt.strftime("%Y-%m")
    res["forecast_chart"] = h1.round(0).to_dict("records")
    res["leakage"].pop("rep_table", None)
    return res


def measures(r: dict) -> dict:
    wm = r["win_model"]["models"]
    champ = wm[r["win_model"]["champion"]]
    prod = next(v for k, v in r["recommendations"]["policies"].items() if k.startswith("production"))
    pr = r["price_response"]["spare_parts"]
    a = r["assistant"]
    sets = [x for x in (a["curated"], a["regression_v1"], a["heldout"]) if x]
    viol = sum(x["policy_violations"] for x in sets)
    f = r["forecast"]
    return {
        "data_quality_planted_defects_caught": r["data_quality"]["caught_share"],
        "data_quality_false_quarantine_rate": r["data_quality"]["false_quarantine_rate"],
        "win_model_log_loss_vs_segment_rate": round(champ["log_loss"] - wm["segment_rate"]["log_loss"], 4),
        "win_model_ece": champ["ece"],
        "experiment_effect_error_spare_parts": round(abs(pr.get("experiment", 9) - pr["true_effect_of_5pct_price_rise"]), 4),
        "recommendation_margin_uplift_validated_lines": prod["margin_uplift_validated_lines"],
        "recommendation_uplift_ci_low": prod["uplift_ci95_validated_lines"][0],
        "recommendation_win_rate_change": prod["win_rate_change_validated_lines"],
        "guardrail_violations": sum(v["guardrail_violations"] for v in r["recommendations"]["policies"].values()),
        "leakage_rep_precision": r["leakage"]["reps"]["precision"],
        "leakage_rep_recall": r["leakage"]["reps"]["recall"],
        "billing_exceptions_recall": r["leakage"]["billing"]["recall"],
        "rebate_overpayment_precision": r["leakage"]["rebates"]["precision"],
        "forecast_wape_vs_seasonal_naive": round(
            f["total"]["test_wape_champions"] - f["models_on_test"]["seasonal_naive"]["wape_total"], 4
        ),
        "forecast_interval_coverage": f["total"]["interval_coverage"],
        "assistant_answer_accuracy": min(a["curated"]["answer_accuracy"], a["regression_v1"]["answer_accuracy"]),
        "assistant_heldout_accuracy": a["heldout"]["answer_accuracy"] if a["heldout"] else 0.0,
        "assistant_policy_violations": viol,
    }


def gate(res: dict, path: Path | None = None) -> tuple[bool, list[str]]:
    obj = yaml.safe_load((path or REPO / "config" / "objectives.yaml").read_text())["objectives"]
    lines, ok = [], True
    ops = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}
    for k, o in obj.items():
        v = res["measures"][k]
        passed = ops[o["op"]](v, o["target"])
        ok &= passed
        lines.append(f"{'PASS' if passed else 'FAIL'}  {k}: {v} (target {o['op']} {o['target']})")
    return ok, lines


def _figures(res, te, truth, models, champion_name, validated, fc_rows) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    out = paths().reports / "figures"
    out.mkdir(parents=True, exist_ok=True)
    ink, muted, accent, warn = "#1b2a41", "#8a94a6", "#0f766e", "#b45309"

    # 1. Price response: true vs observational vs price test, spare parts with a competitor.
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    champ, lr = models[champion_name], models["logistic"]
    slopes = {k: {"no_competitor": v.no_competitor, "competitor": v.competitor} for k, v in validated.items()}
    exp = PriceResponse(champ.predict, slopes)
    for ax, line in zip(axes, ["spare_parts", "field_service"], strict=True):
        g = te[(te["service_line"] == line)]
        changes = np.linspace(-0.10, 0.10, 21)
        base_t = true_p_win(truth.loc[g["quote_id"]], g["price_ratio"].to_numpy()).mean()
        tv = [true_p_win(truth.loc[g["quote_id"]], g["price_ratio"].to_numpy() * (1 + c)).mean() - base_t for c in changes]
        base_o = lr.predict(g).mean()
        ov = [lr.predict(g, g["price_ratio"].to_numpy() * (1 + c)).mean() - base_o for c in changes]
        ax.plot(changes * 100, np.array(tv) * 100, color=ink, lw=2.2, label="true response")
        ax.plot(changes * 100, np.array(ov) * 100, color=warn, lw=1.6, ls="--", label="read from history")
        if line in validated:
            base_e = exp.predict(g, g["price_ratio"].to_numpy()).mean()
            ev = [exp.predict(g, g["price_ratio"].to_numpy() * (1 + c)).mean() - base_e for c in changes]
            ax.plot(changes * 100, np.array(ev) * 100, color=accent, lw=1.8, label="measured by the price test")
        ax.axhline(0, color=muted, lw=0.6)
        ax.axvline(0, color=muted, lw=0.6)
        ax.set_title(line.replace("_", " "), color=ink, fontsize=11)
        ax.set_xlabel("price change vs the rep's quote (%)")
        ax.grid(alpha=0.2)
    axes[0].set_ylabel("change in win rate (points)")
    axes[1].legend(fontsize=8, frameon=False)
    fig.suptitle(
        "How win rates respond to price: history understates it, the randomised price test comes close to the truth", fontsize=10, color=ink
    )
    fig.tight_layout()
    fig.savefig(out / "price_response.png", dpi=150)
    plt.close(fig)

    # 2. Waterfall: list to pocket, all invoices.
    w = warehouse.read(
        "select sum(list_eur) l, sum(negotiated_discount_eur) nd, sum(billing_deviation_eur) bd, sum(freight_eur) fr, sum(expedite_eur) ex, "
        "sum(warranty_eur) wa, sum(early_payment_eur) ep, sum(terms_cost_eur) tc, sum(rebate_eur) rb, sum(pocket_eur) pk from marts.fct_price_waterfall"
    ).iloc[0]
    steps = [
        ("list price", w["l"]),
        ("negotiated\ndiscount", -w["nd"]),
        ("billing\ndeviation", -w["bd"]),
        ("freight", -w["fr"]),
        ("expedites", -w["ex"]),
        ("warranty", -w["wa"]),
        ("early\npayment", -w["ep"]),
        ("payment\nterms", -w["tc"]),
        ("rebates", -w["rb"]),
    ]
    fig, ax = plt.subplots(figsize=(10, 3.8))
    level = 0.0
    for i, (name, v) in enumerate(steps):
        if i == 0:
            ax.bar(i, v / w["l"] * 100, color=ink)
            level = v
        else:
            ax.bar(
                i,
                v / w["l"] * 100,
                bottom=level / w["l"] * 100,
                color=warn if name in ("freight", "expedites", "payment\nterms") else muted,
            )
            level += v
    ax.bar(len(steps), w["pk"] / w["l"] * 100, color=accent)
    ax.set_xticks(range(len(steps) + 1), [s for s, _ in steps] + ["pocket price"], fontsize=8)
    ax.set_ylabel("% of list price")
    ax.set_ylim(70, 101)
    ax.set_title("Price waterfall over all invoices: from list price to what the business keeps", fontsize=10, color=ink)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    fig.savefig(out / "waterfall.png", dpi=150)
    plt.close(fig)

    # 3. Forecast: champion vs actual, total over lines, horizon 1.
    h1 = fc_rows[fc_rows["horizon"] == 1].groupby("target")[["forecast", "actual", "lo", "hi"]].sum()
    fig, ax = plt.subplots(figsize=(10, 3.4))
    ax.fill_between(h1.index, h1["lo"] / 1e6, h1["hi"] / 1e6, color=accent, alpha=0.15, label="80% interval")
    ax.plot(h1.index, h1["actual"] / 1e6, color=ink, lw=2, label="actual bookings")
    ax.plot(h1.index, h1["forecast"] / 1e6, color=accent, lw=1.6, ls="--", label="forecast (made at month start)")
    ax.set_ylabel("EUR million")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.2)
    ax.set_title("Bookings forecast on test months, all service lines", fontsize=10, color=ink)
    fig.tight_layout()
    fig.savefig(out / "forecast.png", dpi=150)
    plt.close(fig)
