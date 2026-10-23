"""Command line: `bpi <step>`, or `bpi run` for the whole pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import time

from bpi.config import load_settings, paths


def cmd_generate(a) -> int:
    from bpi.synth.world import World, WorldConfig

    p = paths()
    World(WorldConfig(seed=a.seed)).generate().write(p.raw, p.truth)
    print(f"simulated sources written to {p.raw}")
    return 0


def cmd_ingest(a) -> int:
    from bpi.contracts import ingest

    st, p = load_settings(), paths()
    rep = ingest(p.raw, p.warehouse, st.as_of)
    p.reports.mkdir(parents=True, exist_ok=True)
    (p.reports / "ingest.json").write_text(json.dumps(rep, indent=2))
    held = {s: r["quarantined"] for s, r in rep.items() if r["quarantined"]}
    print(f"loaded {sum(r['loaded'] for r in rep.values()):,} rows; held back: {json.dumps(held)}")
    return 0


def cmd_build(a) -> int:
    from bpi.warehouse import run_dbt

    run_dbt("build")
    print("dbt build: all models built and tests passed")
    return 0


def cmd_train(a) -> int:
    from bpi.pipeline import train

    d = train(load_settings())
    meta = json.loads((d / "metadata.json").read_text())
    print(f"registered {meta['version']}: champion {meta['champion']}, validated price tests: {sorted(meta['validated_slopes'])}")
    return 0


def cmd_evaluate(a) -> int:
    from bpi import dashboard, evaluate, report

    st, p = load_settings(), paths()
    t0 = time.time()
    res = evaluate.run(st)
    ok, lines = evaluate.gate(res)
    res["gate_passed"] = ok
    res["seconds"] = round(time.time() - t0, 1)
    (p.reports / "evaluation.json").write_text(json.dumps({k: v for k, v in res.items() if k != "forecast_chart"}, indent=2, default=str))
    report.write(res, p.reports / "EVALUATION.md", lines)
    dashboard.build(st, res)
    print("\n".join(lines))
    print(f"gate {'PASSED' if ok else 'FAILED'}; report in {p.reports / 'EVALUATION.md'}")
    return 0


def cmd_gate(a) -> int:
    p = paths()
    res = json.loads((p.reports / "evaluation.json").read_text())
    from bpi.evaluate import gate

    ok, lines = gate(res)
    print("\n".join(lines))
    return 0 if ok else 1


def cmd_dashboard(a) -> int:
    from bpi import dashboard

    print(dashboard.build(load_settings()))
    return 0


def cmd_export(a) -> int:
    from bpi.powerbi import export

    print(export())
    return 0


def cmd_handover(a) -> int:
    from bpi.handover import report, run

    ok, text = report(run())
    print(text)
    return 0 if ok else 1


def cmd_ask(a) -> int:
    from bpi.assistant.semantic import User
    from bpi.assistant.service import Assistant

    ans = Assistant(load_settings().as_of).ask(a.question, User(a.role, a.region))
    if not ans.ok:
        print(f"refused: {ans.refusal}")
        return 2
    print(ans.sql, "\n")
    print("\t".join(ans.columns))
    for r in ans.rows:
        print("\t".join(f"{v:,.4f}" if isinstance(v, float) else str(v) for v in r))
    for n in ans.notes:
        print(f"({n})")
    return 0


def cmd_serve(a) -> int:
    import uvicorn

    uvicorn.run("bpi.api:create_app", factory=True, host=a.host, port=a.port, log_level="info")
    return 0


def cmd_run(a) -> int:
    for step in (cmd_generate, cmd_ingest, cmd_build, cmd_train, cmd_evaluate, cmd_export):
        rc = step(a)
        if rc:
            return rc
    return cmd_gate(a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bpi", description="B2B pricing intelligence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in [
        ("generate", cmd_generate, "simulate the CRM, CPQ, ERP and market sources"),
        ("ingest", cmd_ingest, "load raw sources through the data contracts"),
        ("build", cmd_build, "dbt build: staging, intermediate, marts and tests"),
        ("train", cmd_train, "fit and register the win models and price-test slopes"),
        ("evaluate", cmd_evaluate, "evaluate everything against the truth, write reports and dashboard"),
        ("gate", cmd_gate, "check the latest evaluation against config/objectives.yaml"),
        ("dashboard", cmd_dashboard, "rebuild reports/dashboard.html"),
        ("export-powerbi", cmd_export, "write the star schema and DAX measures to out/powerbi"),
        ("handover-check", cmd_handover, "check the solution can be owned without its authors"),
        ("run", cmd_run, "every step, then the gate"),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.set_defaults(fn=fn)
        if name in ("generate", "run"):
            p.add_argument("--seed", type=int, default=7)
    p = sub.add_parser("ask", help="ask the assistant a question")
    p.add_argument("question")
    p.add_argument("--role", default="pricing_analyst", choices=["pricing_analyst", "regional_manager", "sales_rep"])
    p.add_argument("--region", default=None)
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("serve", help="run the HTTP API")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
