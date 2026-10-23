select
    source,
    row_key,
    reason,
    {{ json_field('row_json', 'customer_id') }} as customer_id,
    {{ json_field('row_json', 'invoice_date') }} as invoice_date
from {{ source('raw', '_quarantine') }}
