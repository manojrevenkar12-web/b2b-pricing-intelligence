{# Read a string field from a JSON text column, on DuckDB and Snowflake. #}
{% macro json_field(col, field) -%}
    {{ return(adapter.dispatch('json_field')(col, field)) }}
{%- endmacro %}

{% macro default__json_field(col, field) -%}
    json_extract_string({{ col }}, '$.{{ field }}')
{%- endmacro %}

{% macro snowflake__json_field(col, field) -%}
    parse_json({{ col }}):{{ field }}::string
{%- endmacro %}
