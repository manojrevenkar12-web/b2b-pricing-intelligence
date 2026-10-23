-- Quotes in EUR at the quote month's average rate, with product, customer and standard cost.
select
    q.quote_id,
    q.opportunity_id,
    q.customer_id,
    q.rep_id,
    q.product_id,
    p.service_line,
    p.product_family,
    p.unit_type,
    c.region,
    c.country,
    c.segment,
    c.strategic_account,
    greatest(0, extract(year from q.quote_date) - c.relationship_since) as relationship_years,
    q.quote_date,
    q.quote_month,
    q.decision_date,
    q.decision_month,
    q.status,
    q.is_decided,
    q.won,
    q.currency,
    q.scope_qty,
    q.outage_urgent,
    q.competitor_present,
    q.price_test_cell,
    q.approval_level,
    q.list_price_lc,
    q.net_price_lc,
    q.list_price_lc * fx.eur_per_unit as list_eur,
    q.net_price_lc * fx.eur_per_unit as net_eur,
    q.net_price_lc / q.list_price_lc as price_ratio,
    sc.unit_cost_eur * q.scope_qty as cost_eur,
    com.nickel_alloy_index
from {{ ref('stg_quotes') }} q
join {{ ref('stg_products') }} p on p.product_id = q.product_id
join {{ ref('stg_customers') }} c on c.customer_id = q.customer_id
join {{ ref('stg_fx') }} fx on fx.currency = q.currency and fx.month = q.quote_month
left join {{ ref('stg_standard_cost') }} sc on sc.product_id = q.product_id and sc.month = q.quote_month
left join {{ ref('stg_commodity_index') }} com on com.month = q.quote_month
