select observed_at, region, product_family, competitor, price_index_vs_list, source
from {{ source('raw', 'market_competitor_intel') }}
