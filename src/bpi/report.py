"""Writes reports/EVALUATION.md from reports/evaluation.json."""

from __future__ import annotations

from pathlib import Path

LINE_NAMES = {"spare_parts": "spare parts", "field_service": "field service", "repairs": "repairs", "upgrades": "upgrades", "ltsa": "LTSA"}


def _p(v: float, d: int = 1) -> str:
    return f"{v * 100:.{d}f}%"


def _pp(v: float) -> str:
    return f"{v * 100:+.1f} pts"


def _eur(v: float) -> str:
    return f"€{v / 1e6:,.1f}M" if abs(v) >= 1e6 else f"€{v / 1e3:,.0f}k"


def write(r: dict, out: Path, gate_lines: list[str]) -> None:
    L: list[str] = ["# Evaluation", ""]
    L += [
        f"Model version `{r['model_version']}`, data to {r['as_of']}. Everything below is measured against the simulation's "
        "truth, which the pipeline itself never reads. Regenerate with `bpi run`.",
        "",
    ]

    dq = r["data_quality"]
    L += ["## Data layer", ""]
    L += [
        f"- Planted defects caught by the data contracts: **{dq['caught']} of {dq['planted_defects']}** ({_p(dq['caught_share'])}).",
        f"- Good rows wrongly quarantined: **{dq['false_quarantine']} of {dq['rows_read']:,}**. Rows held only because their parent was held: {dq['held_because_parent_held']}.",
        f"- dbt tests: {dq['dbt_tests']} run, {dq['dbt_tests_failed']} failed.",
        "",
        "| Defect | Caught |",
        "|---|---|",
    ]
    L += [f"| {k.replace('_', ' ')} | {v['sum']} of {v['count']} |" for k, v in dq["by_issue"].items()]
    L.append("")

    wm = r["win_model"]
    L += [
        "## Win probability",
        "",
        f"{wm['test_quotes']:,} quotes issued after the training window. Champion: **{wm['champion']}** (chosen on the last six months of training data).",
        "",
    ]
    L += ["| Model | Log loss | Brier | AUC | Calibration error |", "|---|---|---|---|---|"]
    for k, v in wm["models"].items():
        L.append(f"| {k} | {v['log_loss']} | {v['brier']} | {v['auc']} | {v['ece']} |")
    L.append("")

    pr = r["price_response"]
    L += [
        "## Price sensitivity",
        "",
        "Change in win rate if every quote in the test period were priced 5% higher. History-based estimates come from "
        "models that see everything recorded; the price-test estimate uses only the randomised quotes.",
        "",
        "| Service line | True | Read from history (logistic) | Read from history (GBM) | Price test |",
        "|---|---|---|---|---|",
    ]
    for line, v in pr.items():
        L.append(
            f"| {LINE_NAMES[line]} | {_pp(v['true_effect_of_5pct_price_rise'])} | {_pp(v['observational_logistic'])} | "
            f"{_pp(v['observational_gbm'])} | {_pp(v['experiment']) if 'experiment' in v else 'no test'} |"
        )
    L += [
        "",
        "Slopes (change in log-odds of winning per unit of log price):",
        "",
        "| Service line | True, no / with competitor | History | Price test (95% CI) |",
        "|---|---|---|---|",
    ]
    for line, v in pr.items():
        ts, os_ = v["true_slope"], v["observational_slope"]
        e = v.get("experiment_slope")
        et = (
            f"{e['no_competitor']} ({e['ci95_no_competitor'][0]} to {e['ci95_no_competitor'][1]}) / {e['competitor']} ({e['ci95_competitor'][0]} to {e['ci95_competitor'][1]}); "
            f"{e['test_quotes']:,} quotes; {'validated' if e['validated'] else 'not precise enough'}"
            if e
            else "no test"
        )
        L.append(
            f"| {LINE_NAMES[line]} | {ts['no_competitor']} / {ts['competitor']} | {os_['no_competitor']} / {os_['competitor']} | {et} |"
        )
    L += ["", "![price response](figures/price_response.png)", ""]

    rec = r["recommendations"]
    L += [
        "## Price recommendations",
        "",
        f"Each policy re-prices the {rec['test_quotes']:,} test quotes inside the guardrails (margin floor, at most 10% from the rep's price, never above list, "
        "LTSA to the deal desk). Margin is the true expected margin, P(win) x (price - cost), compared with the reps' own prices.",
        "",
        "| Policy | Margin, validated lines | Margin, all lines | Win rate, validated lines | Avg price change | Guardrail violations |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in rec["policies"].items():
        L.append(
            f"| {k} | {_p(v['margin_uplift_validated_lines'], 2)} | {_p(v['margin_uplift_all'], 2)} | {_pp(v['win_rate_change_validated_lines'])} | "
            f"{_p(v['average_price_change'], 1)} | {v['guardrail_violations']} |"
        )
    prod = next(v for k, v in rec["policies"].items() if k.startswith("production"))
    ci = prod["uplift_ci95_validated_lines"]
    L += [
        "",
        f"Production policy: expected margin {_p(prod['margin_uplift_validated_lines'], 2)} on validated lines (95% bootstrap interval {_p(ci[0], 2)} to {_p(ci[1], 2)}), "
        f"{_eur(prod['expected_margin_gain_eur'])} over the test period. Status of test quotes: "
        + ", ".join(f"{k.replace('_', ' ')} {v:,}" for k, v in prod["status_counts"].items())
        + ".",
        "",
        "By service line (production policy / oracle on every line):",
        "",
        "| Service line | Production | If every line had a price test (oracle) | Observational slopes |",
        "|---|---|---|---|",
    ]
    oracle_all = next(v for k, v in rec["policies"].items() if k.startswith("oracle on every"))
    obs = next(v for k, v in rec["policies"].items() if k.startswith("observational"))
    for line in LINE_NAMES:
        if line in prod["by_line"]:
            a, b, c = prod["by_line"][line], oracle_all["by_line"][line], obs["by_line"][line]
            L.append(
                f"| {LINE_NAMES[line]} | {_p(a['margin_uplift'], 1)} margin, {_pp(a['win_rate_change'])} win | {_p(b['margin_uplift'], 1)}, {_pp(b['win_rate_change'])} | "
                f"{_p(c['margin_uplift'], 1)}, {_pp(c['win_rate_change'])} |"
            )
    L.append("")

    lk = r["leakage"]
    L += [
        "## Margin leakage",
        "",
        "| Finding | Found | Planted | Precision | Recall | Amount found |",
        "|---|---|---|---|---|---|",
        f"| Reps with excess off-invoice concessions | {len(lk['reps']['flagged'])} | {len(lk['reps']['planted'])} | {lk['reps']['precision']} | {lk['reps']['recall']} | {_eur(lk['reps']['excess_concessions_eur'])} above peers |",
        f"| Invoices billed below the approved quote | {lk['billing']['found']} | {lk['billing']['planted_loaded']} | {lk['billing']['precision']} | {lk['billing']['recall']} | {_eur(lk['billing']['gap_found_eur'])} |",
        f"| Customers paid rebates above contract | {len(lk['rebates']['flagged'])} | {len(lk['rebates']['planted'])} | {lk['rebates']['precision']} | {lk['rebates']['recall']} | {_eur(lk['rebates']['overpaid_found_eur'])} of {_eur(lk['rebates']['planted_overpaid_eur'])} planted |",
        "",
        f"Rebate years left out because invoices were held by the data contracts: {lk['rebates']['incomplete_years']}.",
        "",
        "![waterfall](figures/waterfall.png)",
        "",
    ]

    f = r["forecast"]
    L += [
        "## Bookings forecast",
        "",
        f"Champion per service line chosen on origins {f['validation_origins'][0]} to {f['validation_origins'][1]}; scored on origins {f['test_origins'][0]} to {f['test_origins'][1]}, one to three months ahead.",
        "",
        "| Model | WAPE, all lines | Bias |",
        "|---|---|---|",
    ]
    for k, v in f["models_on_test"].items():
        L.append(f"| {k} | {_p(v['wape_total'])} | {_p(v['bias_total'])} |")
    L += [
        f"| **champions** | **{_p(f['total']['test_wape_champions'])}** | |",
        "",
        "| Service line | Champion | WAPE | vs last year's month | vs CRM pipeline | 80% interval coverage |",
        "|---|---|---|---|---|---|",
    ]
    for line, v in f["by_line"].items():
        L.append(
            f"| {LINE_NAMES[line]} | {v['champion']} | {_p(v['test_wape'])} | {_p(v['test_wape_seasonal_naive'])} | {_p(v['test_wape_pipeline_crm'])} | {_p(v['interval_coverage'], 0)} |"
        )
    L += [
        "",
        f"Interval coverage over all lines: {_p(f['total']['interval_coverage'], 0)} (target 80%).",
        "",
        "![forecast](figures/forecast.png)",
        "",
    ]

    a = r["assistant"]
    L += [
        "## Assistant",
        "",
        "| Question set | Questions | Correct answers | Correct refusals | Policy violations |",
        "|---|---|---|---|---|",
    ]
    names = {
        "curated": "curated (written with the parser)",
        "regression_v1": "held-out v1, now regression (first run 25 of 30)",
        "heldout": "held-out v2 (never used to change the parser; first run 32 of 40 with 1 violation, see below)",
    }
    for k, v in a.items():
        if v:
            L.append(
                f"| {names[k]} | {v['questions']} | {_p(v['answerable_accuracy'], 0)} | {_p(v['refusals_correct'], 0)} | {v['policy_violations']} |"
            )
    if a.get("heldout") and a["heldout"]["failures"]:
        L += ["", "Held-out questions answered wrongly (kept as they are; fixing them retires this set):", ""]
        L += [
            f"- {x['question']}" + (f" (refused: {x['reason'].split('. ')[0]})" if x.get("refused") else " (answered a different question)")
            for x in a["heldout"]["failures"]
        ]
    L.append("")

    m = r["monitoring"]
    L += [
        "## Monitoring (latest quarter vs training)",
        "",
        f"Calibration error on recent decisions: {m['ece_recent']}. Alerts: {', '.join(m['alerts']) or 'none'}.",
        "",
    ]
    L += ["| Feature | PSI |", "|---|---|"] + [f"| {k} | {v} |" for k, v in m["psi"].items()]
    L += ["", "## Release gate", "", "```", *gate_lines, "```", ""]
    out.write_text("\n".join(L))
