-- Competitor price level per region, product family and month: the median observation of the
-- previous six months. Only observations made before the month starts count, so a quote never sees
-- intelligence gathered after it was priced.
with months as (
    select distinct month from {{ ref('stg_fx') }}
),
cells as (
    select distinct region, product_family from {{ ref('stg_competitor_intel') }}
)
select
    m.month,
    c.region,
    c.product_family,
    median(i.price_index_vs_list) as competitor_index,
    count(i.price_index_vs_list) as observations
from months m
cross join cells c
left join {{ ref('stg_competitor_intel') }} i
    on i.region = c.region
    and i.product_family = c.product_family
    and i.observed_at >= {{ dbt.dateadd('month', -6, 'm.month') }}
    and i.observed_at < m.month
group by m.month, c.region, c.product_family
