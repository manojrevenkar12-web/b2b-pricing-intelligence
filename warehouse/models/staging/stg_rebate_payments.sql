select payment_id, customer_id, year, amount_eur
from {{ source('raw', 'erp_rebate_payments') }}
