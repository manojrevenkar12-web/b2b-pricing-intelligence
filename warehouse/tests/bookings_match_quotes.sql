-- The monthly bookings mart must add up to the won quotes it is built from.
select 1
from (
    select
        (select sum(booked_eur) from {{ ref('fct_bookings_monthly') }}) as mart,
        (select sum(net_eur) from {{ ref('fct_quotes') }} where won = 1) as quotes
)
where abs(mart - quotes) > 1
