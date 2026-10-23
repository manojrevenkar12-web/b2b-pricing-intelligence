-- The customer history feature may only count decisions made before the quote date.
select q.quote_id
from {{ ref('fct_quotes') }} q
join {{ ref('int_customer_history') }} h using (quote_id)
where h.prior_decided > (
    select count(*) from {{ ref('stg_quotes') }} x
    where x.customer_id = q.customer_id and x.is_decided and x.decision_date < q.quote_date
)
