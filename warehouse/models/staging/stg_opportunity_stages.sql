select opportunity_id, stage, entered_at
from {{ source('raw', 'crm_opportunity_stages') }}
