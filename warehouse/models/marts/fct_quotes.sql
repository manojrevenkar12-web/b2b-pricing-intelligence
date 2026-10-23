-- One row per quote: what was offered, to whom, against whom, and what happened.
-- The features a pricing model may use are all known on the quote date.
select
    q.quote_id,
    q.opportunity_id,
    q.customer_id,
    c.customer_name,
    q.rep_id,
    r.rep_name,
    q.product_id,
    q.service_line,
    q.product_family,
    q.unit_type,
    q.region,
    q.country,
    q.segment,
    q.strategic_account,
    q.relationship_years,
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
    q.list_eur,
    q.net_eur,
    q.cost_eur,
    q.price_ratio,
    1 - q.price_ratio as discount_pct,
    (q.net_eur - q.cost_eur) / q.net_eur as quoted_margin_pct,
    q.nickel_alloy_index,
    ci.competitor_index,
    ci.observations as competitor_observations,
    h.prior_decided,
    h.prior_won,
    -- Smoothed towards the overall rate so customers with few decisions are not extreme.
    (h.prior_won + 2.0) / (h.prior_decided + 4.0) as prior_win_rate,
    {{ dbt.datediff('q.quote_date', 'q.decision_date', 'day') }} as days_to_decision
from {{ ref('int_quotes_eur') }} q
join {{ ref('int_customer_history') }} h on h.quote_id = q.quote_id
join {{ ref('stg_customers') }} c on c.customer_id = q.customer_id
join {{ ref('stg_reps') }} r on r.rep_id = q.rep_id
left join {{ ref('int_competitor_index') }} ci
    on ci.month = q.quote_month
    and ci.region = q.region
    and ci.product_family = q.product_family
