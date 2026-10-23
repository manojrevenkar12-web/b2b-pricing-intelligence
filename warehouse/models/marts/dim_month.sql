select
    month,
    extract(year from month) as year,
    extract(quarter from month) as quarter,
    extract(month from month) as month_of_year
from (select distinct month from {{ ref('stg_fx') }})
