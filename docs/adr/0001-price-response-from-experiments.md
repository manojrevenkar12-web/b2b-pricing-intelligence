# 0001: Price sensitivity only from randomised price tests

## Context
Reps give bigger discounts on deals they sense are at risk. In history, lower prices therefore go
with lower win rates, and any model fitted on history understates price sensitivity, or gets its sign
wrong. Controlling for every recorded variable does not help, because the rep's read of the deal is
not recorded. In the evaluation, history-based slopes had the wrong sign for repairs, upgrades and LTSA.

## Decision
A service line's price response may drive recommendations only after a randomised price test has
measured it with a slope standard error of at most 2.5. The win model provides the level at the rep's
price; the test provides the slope. Lines without a test keep the rep's price, and the API says why.

## Consequences
Recommendations cover spare parts and field service only, for now. The evaluation shows what testing
repairs and upgrades would be worth (oracle +5.7% and +11.6% expected margin), which is the business
case for the next tests.
