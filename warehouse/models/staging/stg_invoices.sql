select
    invoice_id,
    quote_id,
    customer_id,
    invoice_date,
    {{ month_of('invoice_date') }} as invoice_month,
    currency,
    amount_lc,
    payment_terms_days
from {{ source('raw', 'erp_invoices') }}
