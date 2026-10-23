select quote_id
from {{ ref('fct_quotes') }}
where decision_date > cast('{{ var("as_of") }}' as date)
