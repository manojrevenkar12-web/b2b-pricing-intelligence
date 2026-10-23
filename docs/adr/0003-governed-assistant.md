# 0003: The assistant maps questions to a query specification, never to SQL

## Context
Text-to-SQL answers are hard to govern: generated SQL can reach any table, skip row filters, or invent
a metric definition that differs from the finance one.

## Decision
A question becomes a `QuerySpec` using only names from the semantic model (written in Snowflake's
semantic view YAML shape so it can back Cortex Analyst). The access policy (role, region, cost-based
metrics) is applied to the spec; SQL is generated from the semantic model's definitions. The
deterministic parser runs in CI; a Claude tool-use parser is optional and produces the same spec.
Requests to change data are refused.

## Consequences
Wrong parses produce answers to a different question, never access the user should not have. Accuracy
is measured on question sets, including held-out sets written without access to the parser; a set
used to change the parser is retired to regression.
