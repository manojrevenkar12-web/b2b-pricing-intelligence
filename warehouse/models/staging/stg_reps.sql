select rep_id, rep_name, region
from {{ source('raw', 'crm_users') }}
