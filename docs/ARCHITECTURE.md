# Architecture

```
 CRM (accounts, reps,        CPQ (quotes,           ERP (products, price list,     Market intelligence
 opportunities, stages)      price-test cells)      standard cost, invoices,       (competitor prices,
        |                        |                  off-invoice, rebates)          alloy index), FX
        +------------------------+------------------------+-------------------------------+
                                             |
                          bpi ingest: data contracts (contracts/*.yaml)
                          typed columns, rules, references, cross-source checks
                          -> raw.*  (passed)        -> raw._quarantine (held, with reason)
                                             |
                          dbt build (warehouse/): staging -> intermediate -> marts, 52 tests
                          fct_quotes, fct_price_waterfall, fct_billing_exceptions,
                          fct_rebate_reconciliation, fct_bookings_monthly,
                          fct_pipeline_snapshots, dim_*
                                             |
          +-------------------+--------------+---------------+-------------------+
          |                   |                              |                   |
   bpi train            leakage analysis              bookings forecast     semantic model
   win models,          reps vs peers, billing,       ETS, pipeline,        (Snowflake semantic
   price-test slopes    rebates                       blend, champions      view YAML) + policy
   -> registry                                                                    |
          |                                                                 assistant: question
   recommender (guardrails)                                                 -> QuerySpec -> policy
          |                                                                 -> SQL from definitions
          +--------------------+------------------------------------+-------------+
                               |                                    |
                      FastAPI (bpi serve)                  dashboard.html, Power BI
                      recommendations, ask,                star schema + DAX measures
                      audit log
```

## Decisions

- **One warehouse model for everything.** Models, dashboard, Power BI export and assistant all read
  the dbt marts; nothing reads raw tables. A metric means the same thing everywhere (the DAX measures
  and the semantic model are written from the same definitions).
- **Bad rows are held, not dropped.** The data contracts move rows that break them to a quarantine
  table with the rule they broke, so the source owner can fix them. Rows held only because their
  parent was held are marked as such ([ADR 0002](adr/0002-quarantine-not-drop.md)).
- **Portable SQL.** The dbt models use dbt's cross-database macros and functions that DuckDB and
  Snowflake share (`qualify`, `median`, `date_trunc`); the one difference (reading JSON) is an adapter
  dispatch macro. A `snowflake` target is in `profiles.yml`; it has not been run against an account.
- **Price sensitivity only from experiments** ([ADR 0001](adr/0001-price-response-from-experiments.md)).
- **The assistant never writes SQL** ([ADR 0003](adr/0003-governed-assistant.md)).
- **Business thresholds in reviewed config**: guardrails, price-test precision, leakage threshold,
  monitoring alerts in `config/pricing.yaml`; release objectives in `config/objectives.yaml`; both
  owned by Global Pricing Strategy in CODEOWNERS.
- **Simulated sources with a known truth** ([ADR 0004](adr/0004-simulated-sources.md)).

## Failure modes and what happens

| Failure | Effect | Guard |
|---|---|---|
| Source export malformed or missing a column | ingest stops with the source named | contract column check |
| Rows break a contract | rows held in `raw._quarantine`, rest loads | contracts, `bpi ingest` report |
| A mart breaks an invariant (waterfall does not reconcile, lookahead in a feature) | dbt build fails, nothing downstream runs | dbt tests |
| Model worse than the objectives | version not promoted | `bpi gate` in CI |
| Inputs drift or calibration slips | alert in the evaluation and monitoring report | `monitor.check` |
| A user asks for data outside their role | refused before any SQL runs | `Policy.apply`, tests |
| A user asks to change data | refused | `guard_question` |
