# Model card: win probability

## Purpose
Probability that a quote is won at a given price, used to score candidate prices for a quote and to
weight the pipeline in the bookings forecast. It is not a price-sensitivity estimate on its own (see
[price-response](price-response.md)).

## Data
`marts.fct_quotes`: quotes issued and decided before `train_end` (config/pricing.yaml). Inputs are
known on the quote date: service line, product family, unit type, region, segment, log price ratio
(net / list), log list value, competitor present, competitor price index (median of the previous
six months of market intelligence), urgency, strategic account, relationship years, the customer's
smoothed win rate on earlier decisions, month of year.

## Evaluation
On 11,448 quotes issued after the training window: logistic regression log loss 0.660, calibration
error 0.016, AUC 0.645; the segment win rate scores 0.684; the true probabilities 0.624. The
logistic model beat LightGBM (0.663) and is the champion, chosen on the last six months of training
data. Full table in reports/EVALUATION.md.

## Limits
Most of what decides a deal is not recorded, so even the true probabilities only reach AUC 0.71.
Price coefficients of this model are confounded (reps discount deals at risk) and must not be read
as price sensitivity.

## Owner
Global Pricing Strategy, analytics lead.

## Retraining
`bpi train` after each quarter's decisions are in. The champion is re-chosen each time; a new version
is promoted only when `bpi gate` passes.

## Monitoring
`monitor.check`: PSI per input between training quotes and the latest quarter (alert above 0.2) and
calibration error on recent decisions (alert above 0.04). Both run in `bpi evaluate`.
