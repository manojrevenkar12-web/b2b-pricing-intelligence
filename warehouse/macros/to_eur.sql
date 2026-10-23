{# Amount in local currency to EUR with the monthly average rate of the month `on_date` falls in.
   Expects the fx table joined as `fx` on currency and month. #}
{% macro to_eur(amount) -%}
    ({{ amount }}) * fx.eur_per_unit
{%- endmacro %}

{% macro month_of(col) -%}
    cast({{ dbt.date_trunc('month', col) }} as date)
{%- endmacro %}
