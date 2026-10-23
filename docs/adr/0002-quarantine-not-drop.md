# 0002: Quarantine rows that break a contract, do not drop or fix them

## Context
Fixing data silently in the pipeline hides the problem from the system that produced it; dropping it
loses money (an invoice amount sent in cents is still an invoice).

## Decision
Ingest moves rows that break their contract to `raw._quarantine` with the rule they broke and the full
row. Exact duplicate re-sends are dropped and logged. Dependent rows of a held row are held with the
reason `parent_quarantined`. Analyses that would be wrong without held rows say so (rebate years with
held invoices are reported as incomplete, not as overpaid).

## Consequences
The owners named in each contract get a list to fix. The evaluation counts false quarantines: zero on
346,481 rows.
