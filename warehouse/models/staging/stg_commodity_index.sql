select cast(month as date) as month, nickel_alloy_index
from {{ source('raw', 'market_commodity_index') }}
