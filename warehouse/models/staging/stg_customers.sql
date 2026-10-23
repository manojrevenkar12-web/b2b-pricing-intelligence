select
    a.customer_id,
    a.customer_name,
    a.region,
    a.country,
    a.segment,
    a.billing_currency,
    a.strategic_account,
    a.relationship_since,
    coalesce(c.rebate_rate, 0) as rebate_rate,
    coalesce(c.standard_terms_days, {{ var('standard_terms_days') }}) as standard_terms_days
from {{ source('raw', 'crm_accounts') }} a
left join {{ source('raw', 'contracts') }} c
    on c.customer_id = a.customer_id
    and cast('{{ var("as_of") }}' as date) between c.valid_from and c.valid_to
