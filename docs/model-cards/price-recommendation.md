# Model card: price recommendation

## Purpose
A recommended price and range for a quote: the price that maximises expected margin, P(win) x
(price - cost), within guardrails. Advice for the rep, not an override.

## Data
The champion win model, the validated price-test slopes, list price and standard cost of the quoted
scope, all from the warehouse as of the quote date.

## Evaluation
Re-pricing the test-period quotes and scoring with the true win probabilities: +4.0% expected margin
on validated lines (bootstrap 95% interval 3.8% to 4.2%), win rate -0.5 points, 0 guardrail
violations. The best possible within the same guardrails is +5.3%. Using history-based slopes instead
would have raised prices 8.7% on average and cost 8.9 points of win rate on the same lines.

## Limits
Recommendations exist only for lines with a validated price test. A rep's private knowledge of a
deal is not in the model, which is why the recommendation is a range around the rep's price, at most
10% away.

## Owner
Global Pricing Strategy owns the guardrails (config/pricing.yaml, CODEOWNERS).

## Retraining
Follows the win model and the price tests; no separate fitting.

## Monitoring
Every recommendation is logged with user, inputs, answer and model version. Quarterly: share of
recommendations followed, and realised win rate and margin of followed versus not followed quotes.
