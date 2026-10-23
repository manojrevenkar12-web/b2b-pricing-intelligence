"""Star schema export for Power BI (or Tableau): facts and dimensions as Parquet, plus the measures.

The measures are written in DAX from the same definitions as the semantic model, so a Power BI
report and the assistant compute "win rate" or "pocket margin" the same way. The export is checked
by tests (keys unique, every fact key found in its dimension); it has not been opened in Power BI
Desktop as part of CI.
"""

from __future__ import annotations

from pathlib import Path

from bpi import warehouse
from bpi.config import paths

TABLES = {
    "fact_quotes": "select quote_id, customer_id, rep_id, product_id, cast(quote_date as date) as quote_date, cast(quote_month as date) as month, "
    "status, is_decided, won, competitor_present, outage_urgent, approval_level, list_eur, net_eur, cost_eur, price_ratio, discount_pct "
    "from marts.fct_quotes",
    "fact_invoices": "select invoice_id, quote_id, customer_id, rep_id, product_id, invoice_date, cast(invoice_month as date) as month, "
    "list_eur, quoted_eur, invoice_eur, negotiated_discount_eur, billing_deviation_eur, freight_eur, expedite_eur, warranty_eur, "
    "early_payment_eur, terms_cost_eur, rebate_eur, off_invoice_eur, pocket_eur, cost_eur, pocket_margin_eur from marts.fct_price_waterfall",
    "dim_customer": "select customer_id, customer_name, region, country, segment, strategic_account, rebate_rate from marts.dim_customer",
    "dim_product": "select product_id, service_line, product_family, unit_type, description from marts.dim_product",
    "dim_rep": "select rep_id, rep_name, region from marts.dim_rep",
    "dim_month": "select month, year, quarter, month_of_year from marts.dim_month",
}

RELATIONSHIPS = [
    ("fact_quotes", "customer_id", "dim_customer", "customer_id"),
    ("fact_quotes", "rep_id", "dim_rep", "rep_id"),
    ("fact_quotes", "product_id", "dim_product", "product_id"),
    ("fact_quotes", "month", "dim_month", "month"),
    ("fact_invoices", "customer_id", "dim_customer", "customer_id"),
    ("fact_invoices", "rep_id", "dim_rep", "rep_id"),
    ("fact_invoices", "product_id", "dim_product", "product_id"),
    ("fact_invoices", "month", "dim_month", "month"),
]

MEASURES = """// Measures for the Gas Services pricing model. Same definitions as semantic/pricing_semantic_view.yaml.
Quotes = COUNTROWS(fact_quotes)
Win Rate = DIVIDE(CALCULATE(SUM(fact_quotes[won]), fact_quotes[is_decided] = TRUE()), CALCULATE(COUNTROWS(fact_quotes), fact_quotes[is_decided] = TRUE()))
Quoted Value EUR = SUM(fact_quotes[net_eur])
Booked Value EUR = CALCULATE(SUM(fact_quotes[net_eur]), fact_quotes[won] = 1)
Average Discount = AVERAGE(fact_quotes[discount_pct])
Quoted Margin % = DIVIDE(SUM(fact_quotes[net_eur]) - SUM(fact_quotes[cost_eur]), SUM(fact_quotes[net_eur]))
Invoiced EUR = SUM(fact_invoices[invoice_eur])
Pocket Price EUR = SUM(fact_invoices[pocket_eur])
Off-Invoice EUR = SUM(fact_invoices[off_invoice_eur])
Off-Invoice % = DIVIDE([Off-Invoice EUR], [Invoiced EUR])
Pocket to List % = DIVIDE([Pocket Price EUR], SUM(fact_invoices[list_eur]))
Pocket Margin % = DIVIDE([Pocket Price EUR] - SUM(fact_invoices[cost_eur]), [Pocket Price EUR])
"""


def export(out: Path | None = None) -> dict:
    out = out or paths().powerbi
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, sql in TABLES.items():
        df = warehouse.read(sql)
        df.to_parquet(out / f"{name}.parquet", index=False)
        counts[name] = len(df)
    (out / "measures.dax").write_text(MEASURES)
    rel = "\n".join(f"- {a}[{ac}] -> {b}[{bc}] (many to one)" for a, ac, b, bc in RELATIONSHIPS)
    (out / "MODEL.md").write_text(
        "# Power BI model\n\nLoad the Parquet files, create these relationships, then paste the measures from "
        f"measures.dax.\n\n{rel}\n\nRow counts: {counts}\n"
    )
    return counts
