from bpi import warehouse


def test_marts_are_built(home):
    for t in ("fct_quotes", "fct_price_waterfall", "fct_bookings_monthly", "fct_pipeline_snapshots", "dim_customer"):
        assert warehouse.read(f"select count(*) n from marts.{t}")["n"][0] > 0


def test_competitor_index_uses_only_earlier_observations(home):
    # Recompute one cell by hand: observations from the six months before the month, not during it.
    row = warehouse.read(
        "select month, region, product_family, competitor_index from intermediate.int_competitor_index "
        "where competitor_index is not null order by month desc limit 1"
    ).iloc[0]
    expected = warehouse.read(
        "select median(price_index_vs_list) m from staging.stg_competitor_intel where region = ? and product_family = ? "
        "and observed_at >= ?::date - interval 6 month and observed_at < ?::date",
        [row["region"], row["product_family"], row["month"], row["month"]],
    )["m"][0]
    assert abs(expected - row["competitor_index"]) < 1e-12


def test_waterfall_pocket_never_exceeds_invoice(home):
    bad = warehouse.read("select count(*) n from marts.fct_price_waterfall where pocket_eur > invoice_eur + 0.01")["n"][0]
    assert bad == 0


def test_pipeline_snapshot_shows_only_opportunities_open_at_the_time(home):
    bad = warehouse.read(
        "select count(*) n from marts.fct_pipeline_snapshots where closed_month is not null and closed_month < snapshot_month"
    )["n"][0]
    assert bad == 0
