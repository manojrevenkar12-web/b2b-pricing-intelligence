-- A quote below 30% of list or above list is a data error, not a price.
select quote_id, price_ratio
from {{ ref('fct_quotes') }}
where price_ratio <= 0.3 or price_ratio > 1.0001
