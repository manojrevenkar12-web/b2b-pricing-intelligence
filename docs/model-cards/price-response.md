# Model card: price response

## Purpose
How much the win probability moves when the price moves, per service line, with and without a
competitor bidding. It decides how far a recommendation may move a price.

## Data
Quotes in the randomised price test (spare parts and field service, January 2024 to June 2025): a
random change of -6%, -3%, 0, +3% or +6% on top of the rep's price, logged in the CPQ. The win
model's prediction at the rep's own price is the offset; only the random change is estimated.

## Evaluation
Effect of a 5% price rise on the win rate, test period: spare parts true -5.5 points, price test
-4.8, history-based logistic -2.3; field service true -3.9, price test -2.9, history +0.1. For
repairs, upgrades and LTSA, history gets the sign wrong (+2.6 to +4.0 points against a true -1.7 to
-3.3).

## Limits
Without a competitor, the slopes are imprecise (spare parts 95% CI -4.7 to +1.2). A line is used for
recommendations only when both slopes have a standard error of at most 2.5
(`price_tests.max_slope_se`). Repairs, upgrades and LTSA have no test and keep the rep's price.

## Owner
Global Pricing Strategy; price tests are approved with the regional sales heads.

## Retraining
After each new price test, or yearly. Extending the field service test and starting one for repairs
are the next steps (see README).

## Monitoring
The win model's monitoring covers its inputs. Recommendations' acceptance and outcome are logged per
call (out/audit) and compared with the expected margin quarterly.
