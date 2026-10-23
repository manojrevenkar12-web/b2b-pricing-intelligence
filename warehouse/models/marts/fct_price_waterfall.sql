select
    w.*,
    c.customer_name,
    r.rep_name,
    w.list_eur - w.quoted_eur as negotiated_discount_eur,
    w.quoted_eur - w.invoice_eur as billing_deviation_eur,
    w.freight_eur + w.expedite_eur + w.warranty_eur + w.early_payment_eur + w.terms_cost_eur + w.rebate_eur
        as off_invoice_eur,
    w.invoice_eur - (w.freight_eur + w.expedite_eur + w.warranty_eur + w.early_payment_eur
        + w.terms_cost_eur + w.rebate_eur) as pocket_eur,
    w.invoice_eur - (w.freight_eur + w.expedite_eur + w.warranty_eur + w.early_payment_eur
        + w.terms_cost_eur + w.rebate_eur) - w.cost_eur as pocket_margin_eur
from {{ ref('int_invoice_waterfall') }} w
join {{ ref('stg_customers') }} c on c.customer_id = w.customer_id
join {{ ref('stg_reps') }} r on r.rep_id = w.rep_id
