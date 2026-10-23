-- The open pipeline as it looked at the start of each month: stage, estimate and expected close as
-- the CRM showed them then. Outcome columns are for backtesting forecasts only.
with snapshots as (
    select distinct month as snapshot_month from {{ ref('stg_fx') }}
),
open_opps as (
    select s.snapshot_month, o.*
    from snapshots s
    join {{ ref('stg_opportunities') }} o
        on o.created_at < s.snapshot_month
        and (o.closed_at is null or o.closed_at >= s.snapshot_month)
),
stage_at as (
    select
        oo.snapshot_month,
        oo.opportunity_id,
        st.stage
    from open_opps oo
    join {{ ref('stg_opportunity_stages') }} st
        on st.opportunity_id = oo.opportunity_id
        and st.entered_at < oo.snapshot_month
    qualify row_number() over (
        partition by oo.snapshot_month, oo.opportunity_id order by st.entered_at desc
    ) = 1
)
select
    oo.snapshot_month,
    oo.opportunity_id,
    oo.customer_id,
    c.region,
    oo.service_line,
    coalesce(sa.stage, 'qualify') as stage,
    oo.amount_estimate * fx.eur_per_unit as amount_eur,
    {{ month_of('oo.expected_close_date') }} as expected_close_month,
    q.quote_id,
    {{ month_of('oo.closed_at') }} as closed_month,
    oo.status as final_status
from open_opps oo
join {{ ref('stg_customers') }} c on c.customer_id = oo.customer_id
join {{ ref('stg_fx') }} fx on fx.currency = oo.currency and fx.month = oo.snapshot_month
left join stage_at sa on sa.snapshot_month = oo.snapshot_month and sa.opportunity_id = oo.opportunity_id
left join {{ ref('stg_quotes') }} q on q.opportunity_id = oo.opportunity_id
