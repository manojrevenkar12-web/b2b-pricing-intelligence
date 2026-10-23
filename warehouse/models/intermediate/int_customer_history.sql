-- For each quote: how the customer's earlier quotes went, counting only decisions made before the
-- quote date (no look-ahead).
select
    q.quote_id,
    count(h.quote_id) as prior_decided,
    coalesce(sum(h.won), 0) as prior_won
from {{ ref('stg_quotes') }} q
left join {{ ref('stg_quotes') }} h
    on h.customer_id = q.customer_id
    and h.is_decided
    and h.decision_date < q.quote_date
group by q.quote_id
