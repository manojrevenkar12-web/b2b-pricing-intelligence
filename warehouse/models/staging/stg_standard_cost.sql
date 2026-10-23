select product_id, cast(month as date) as month, unit_cost_eur
from {{ source('raw', 'erp_standard_cost') }}
