select product_id, valid_from, valid_to, list_price_eur
from {{ source('raw', 'erp_price_list') }}
