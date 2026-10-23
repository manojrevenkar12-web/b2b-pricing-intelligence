# Model card: bookings forecast

## Purpose
Bookings per service line one to three months ahead, made at the start of each month, for sales
operations and finance.

## Data
`marts.fct_bookings_monthly` and `marts.fct_pipeline_snapshots` (the open pipeline as the CRM showed
it at each month start).

## Evaluation
Champion per line chosen on origins January 2024 to April 2025; scored on July 2025 to July 2026:
15.4% WAPE over all lines against 19.0% for last year's same month and 155% for the CRM-weighted
pipeline (stale expected close dates pile into the current month). 80% intervals covered 80% of
outcomes.

## Limits
LTSA bookings are a few large deals a month; their error (25% WAPE) and interval coverage (67%), with field service coverage at 64%, are
the weakest. Intervals come from validation errors and assume the next year looks like the last.

## Owner
Global Sales Operations, with Global Pricing Strategy.

## Retraining
Monthly; the champion per line is re-chosen quarterly.

## Monitoring
Forecast versus actual each month; a line whose WAPE over the last six months exceeds its validation
WAPE by half is reviewed.
