# Runbook

## Weekly run
```bash
bpi ingest            # contracts; read the held rows per source in reports/ingest.json
bpi build             # dbt build; any failed test stops here
bpi evaluate          # monitoring, reports, dashboard (on real data: drop the truth-based sections)
```

## Releasing a model version
1. `bpi train` registers a version in `out/registry` with its data fingerprint and code version.
2. `bpi evaluate && bpi gate`. The gate's objectives are in `config/objectives.yaml`.
3. Point `out/registry/current.json` at the version only when the gate passes. CI does this check on
   every change.

## Alerts and what to do

| Alert | First check | Then |
|---|---|---|
| Ingest holds many rows from one source | `select reason, count(*) from raw._quarantine where source = ... group by 1` | send the list to the contract owner; do not loosen the contract to make it pass |
| dbt test fails | `out/dbt/target/run_results.json`, the failing test's SQL | fix the model or the source; never skip the test |
| PSI above 0.2 on an input | which input, and whether the market changed (price list, competitor moves) | retrain; if a price list changed, re-check guardrails |
| Calibration error above 0.04 | calibration by service line in the evaluation | retrain; pause recommendations for the affected line until fixed |
| Gate fails | the failed objective in `reports/EVALUATION.md` | keep the current version |
| API not ready (`/readyz` 503) | registry `current.json`, warehouse file | restore the last good version |

## Pausing recommendations for a line
Remove the line from the validated slopes by raising its precision bar or deleting its entry in the
registry metadata; the API then returns `hold_unvalidated` with the reason. No code change needed.

## Rotating API keys
`BPI_API_KEYS` (JSON key -> role, region) comes from the secret store. Restart the API after rotating.
