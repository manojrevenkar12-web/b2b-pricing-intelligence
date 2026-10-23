select invoice_id, item_type, amount_lc
from {{ source('raw', 'erp_off_invoice') }}
