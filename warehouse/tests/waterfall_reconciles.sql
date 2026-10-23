-- Every euro between list and pocket price must be in exactly one waterfall step.
select invoice_id
from {{ ref('fct_price_waterfall') }}
where abs(
    list_eur
    - negotiated_discount_eur
    - billing_deviation_eur
    - off_invoice_eur
    - pocket_eur
) > 0.01
