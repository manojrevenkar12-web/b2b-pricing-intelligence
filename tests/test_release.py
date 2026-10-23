"""The whole pipeline, evaluated against the truth, must pass the release gate."""

from bpi.evaluate import gate


def test_gate_passes(evaluated):
    ok, lines = gate(evaluated)
    assert ok, "\n".join(lines)


def test_dashboard_and_export(evaluated, home):
    from bpi.powerbi import export

    html = (home / "reports" / "dashboard.html").read_text()
    assert "const D = {" in html and "/*DATA*/" not in html
    counts = export()
    import pandas as pd

    f = pd.read_parquet(home / "out" / "powerbi" / "fact_quotes.parquet")
    d = pd.read_parquet(home / "out" / "powerbi" / "dim_customer.parquet")
    assert d["customer_id"].is_unique and f["customer_id"].isin(d["customer_id"]).all()
    assert counts["fact_quotes"] == len(f)


def test_handover_check_lists_every_area(evaluated):
    from bpi.handover import run

    areas = {i.area for i in run()}
    assert {"data", "warehouse", "models", "evaluation", "operations", "security"} <= areas
