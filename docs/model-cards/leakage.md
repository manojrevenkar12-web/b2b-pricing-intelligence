# Model card: margin leakage

## Purpose
Find margin lost between the invoice and the pocket: reps whose off-invoice concessions sit far
above peers, invoices billed below the approved quote, rebates paid above contract.

## Data
`marts.fct_price_waterfall`, `marts.fct_billing_exceptions`, `marts.fct_rebate_reconciliation`.

## Evaluation
Against planted leakage: reps flagged 4 of 5 with no false flags; billing exceptions 173 of 173;
rebate overpayments 6 of 6 customers, €3.1M of €3.9M (years with invoices held by the data contracts
are left out until Finance corrects them).

## Limits
A rep is compared with the median of the same service line and region; a whole team that leaks is
not an outlier. One planted rep (z 2.9) stays below the threshold.

## Owner
Sales managers (reps), Finance (billing), contract management (rebates).

## Retraining
No fitted parameters; thresholds in config/pricing.yaml (`leakage.rep_z`).

## Monitoring
Monthly list to the owners; findings closed or explained are tracked by the owners, not here.
