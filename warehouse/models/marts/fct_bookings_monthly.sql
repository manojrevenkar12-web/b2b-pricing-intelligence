-- Bookings (won quote value, by decision month) per region and service line.
with grid as (
    select m.month, r.region, l.service_line
    from (select distinct month from {{ ref('stg_fx') }}) m
    cross join (select distinct region from {{ ref('stg_customers') }}) r
    cross join (select distinct service_line from {{ ref('stg_products') }}) l
    where m.month <= cast('{{ var("as_of") }}' as date)
)
select
    g.month,
    g.region,
    g.service_line,
    coalesce(sum(case when q.won = 1 then q.net_eur end), 0) as booked_eur,
    count(q.quote_id) as decided_quotes,
    coalesce(sum(q.won), 0) as won_quotes,
    coalesce(sum(q.net_eur), 0) as decided_value_eur
from grid g
left join {{ ref('fct_quotes') }} q
    on q.decision_month = g.month
    and q.region = g.region
    and q.service_line = g.service_line
    and q.is_decided
group by g.month, g.region, g.service_line
