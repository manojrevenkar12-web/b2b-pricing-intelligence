select product_id, service_line, product_family, unit_type, description
from {{ source('raw', 'erp_products') }}
