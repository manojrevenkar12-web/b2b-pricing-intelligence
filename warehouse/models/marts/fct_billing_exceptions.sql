-- Invoices billed below the approved quote. Nobody approved these discounts; each one is a claim
-- to check with Finance.
select
    invoice_id,
    quote_id,
    customer_id,
    rep_id,
    region,
    service_line,
    invoice_date,
    quoted_eur,
    invoice_eur,
    quoted_eur - invoice_eur as gap_eur,
    (quoted_eur - invoice_eur) / quoted_eur as gap_pct
from {{ ref('fct_price_waterfall') }}
where invoice_eur < quoted_eur * 0.995
