{# Use the folder's schema name as is (staging, intermediate, marts), not prefixed with the target schema. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
