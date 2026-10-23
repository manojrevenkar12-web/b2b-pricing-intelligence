-- The CRM re-sends rows; ingest drops exact duplicates. Keep one row per id regardless.
select
    opportunity_id,
    customer_id,
    owner_rep_id,
    service_line,
    created_at,
    expected_close_date,
    amount_estimate,
    currency,
    status,
    closed_at
from {{ source('raw', 'crm_opportunities') }}
qualify row_number() over (partition by opportunity_id order by created_at) = 1
