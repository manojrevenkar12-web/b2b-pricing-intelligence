-- Price waterfall per invoice, all in EUR at the invoice month's rate (so exchange-rate moves
-- between quote and invoice never show up as a discount):
-- list -> (negotiated discount) -> quoted -> (billing deviation) -> invoice
--      -> (freight, expedites, warranty, early payment, extra payment terms, rebate) -> pocket
with off_invoice as (
    select
        invoice_id,
        sum(case when item_type = 'freight_absorbed' then amount_lc else 0 end) as freight_lc,
        sum(case when item_type = 'expedite_free' then amount_lc else 0 end) as expedite_lc,
        sum(case when item_type = 'extended_warranty' then amount_lc else 0 end) as warranty_lc,
        sum(case when item_type = 'early_payment_discount' then amount_lc else 0 end) as early_payment_lc
    from {{ ref('stg_off_invoice') }}
    group by invoice_id
),
inv as (
    select
        i.*,
        extract(year from i.invoice_date) as invoice_year,
        i.amount_lc * fx.eur_per_unit as invoice_eur,
        fx.eur_per_unit
    from {{ ref('stg_invoices') }} i
    join {{ ref('stg_fx') }} fx on fx.currency = i.currency and fx.month = i.invoice_month
),
yearly as (
    select customer_id, invoice_year, sum(invoice_eur) as invoiced_eur
    from inv
    group by customer_id, invoice_year
),
rebates as (
    select customer_id, year, sum(amount_eur) as paid_eur
    from {{ ref('stg_rebate_payments') }}
    group by customer_id, year
)
select
    inv.invoice_id,
    inv.quote_id,
    inv.customer_id,
    q.rep_id,
    q.product_id,
    q.service_line,
    q.product_family,
    q.region,
    q.segment,
    inv.invoice_date,
    inv.invoice_month,
    inv.invoice_year,
    inv.payment_terms_days,
    q.list_price_lc * inv.eur_per_unit as list_eur,
    q.net_price_lc * inv.eur_per_unit as quoted_eur,
    inv.invoice_eur,
    coalesce(o.freight_lc, 0) * inv.eur_per_unit as freight_eur,
    coalesce(o.expedite_lc, 0) * inv.eur_per_unit as expedite_eur,
    coalesce(o.warranty_lc, 0) * inv.eur_per_unit as warranty_eur,
    coalesce(o.early_payment_lc, 0) * inv.eur_per_unit as early_payment_eur,
    inv.invoice_eur * greatest(inv.payment_terms_days - c.standard_terms_days, 0) / 365.0
        * {{ var('cost_of_capital') }} as terms_cost_eur,
    -- Rebates are paid after the year ends: use the payment when there is one, else accrue at the
    -- contracted rate.
    case
        when r.paid_eur is not null then r.paid_eur * inv.invoice_eur / y.invoiced_eur
        else inv.invoice_eur * c.rebate_rate
    end as rebate_eur,
    q.cost_eur
from inv
join {{ ref('int_quotes_eur') }} q on q.quote_id = inv.quote_id
join {{ ref('stg_customers') }} c on c.customer_id = inv.customer_id
join yearly y on y.customer_id = inv.customer_id and y.invoice_year = inv.invoice_year
left join off_invoice o on o.invoice_id = inv.invoice_id
left join rebates r on r.customer_id = inv.customer_id and r.year = inv.invoice_year
