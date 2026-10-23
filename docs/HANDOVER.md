# Taking over an externally built solution

How a solution built by an outside partner becomes something the internal team owns. `bpi
handover-check` automates the parts that can be checked in the repository; the rest is the process
below.

## 1. Inventory (before the contract ends)
- Every data source, its owner on our side and its contract (`contracts/`). A source without an owner
  is a blocker.
- Every model: what it decides, its card (`docs/model-cards/`), how it was evaluated, how to retrain.
- Every business threshold and where it lives. Thresholds in code or in someone's head move to
  reviewed config (`config/`).
- Access: who can change prices, guardrails, models; secrets in the secret store, none in the repo.

## 2. Reproduce
The internal team rebuilds everything from a clean checkout without the partner's help: `bpi run`
must pass the gate. Anything that only works on the partner's machines is found here, not after
go-live.

## 3. Parallel run
Run the partner's and the internal build side by side on the same inputs for a period agreed with the
business (four weeks is typical for a weekly cycle). Compare outputs (recommendations, forecasts,
leakage lists); differences above the agreed tolerance are explained before cutover.

## 4. Acceptance
Signed off by the business owner against written criteria:
- `bpi handover-check` passes;
- the release gate passes on the internal build;
- the parallel run's differences are explained;
- the runbook has been used by the internal team at least once for a real alert or a drill.

## 5. Cutover and hypercare
Switch consumers (CPQ, CRM, dashboards) to the internal service; keep the partner on call for an
agreed hypercare period; close it when two regular cycles ran without the partner.

## 6. Long-term ownership
Named owners per area in CODEOWNERS; quarterly review of model cards, guardrails and objectives;
price tests planned yearly with sales.
