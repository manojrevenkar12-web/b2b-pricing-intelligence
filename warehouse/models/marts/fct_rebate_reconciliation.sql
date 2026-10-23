-- Rebates paid against what the contract allows on that year's invoiced revenue. A year with
-- invoices held back by the data contracts is incomplete: its entitlement cannot be computed until
-- Finance corrects them, so it is flagged instead of reported as overpaid.
with invoiced as (
    select customer_id, invoice_year as year, sum(invoice_eur) as invoiced_eur
    from {{ ref('fct_price_waterfall') }}
    group by customer_id, invoice_year
),
paid as (
    select customer_id, year, sum(amount_eur) as paid_eur
    from {{ ref('stg_rebate_payments') }}
    group by customer_id, year
),
held as (
    select customer_id, cast(substr(invoice_date, 1, 4) as integer) as year, count(*) as held_invoices
    from {{ ref('stg_quarantine') }}
    where source = 'erp_invoices'
    group by 1, 2
)
select
    p.customer_id,
    p.year,
    c.rebate_rate,
    i.invoiced_eur,
    p.paid_eur,
    i.invoiced_eur * c.rebate_rate as entitled_eur,
    p.paid_eur - i.invoiced_eur * c.rebate_rate as overpaid_eur,
    coalesce(h.held_invoices, 0) as held_invoices,
    coalesce(h.held_invoices, 0) = 0 as is_complete,
    coalesce(h.held_invoices, 0) = 0
        and p.paid_eur - i.invoiced_eur * c.rebate_rate > greatest(500, 0.05 * i.invoiced_eur * c.rebate_rate)
        as is_overpaid
from paid p
join invoiced i on i.customer_id = p.customer_id and i.year = p.year
join {{ ref('stg_customers') }} c on c.customer_id = p.customer_id
left join held h on h.customer_id = p.customer_id and h.year = p.year
