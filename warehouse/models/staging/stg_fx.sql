select cast(month as date) as month, currency, eur_per_unit
from {{ source('raw', 'fx_rates') }}
