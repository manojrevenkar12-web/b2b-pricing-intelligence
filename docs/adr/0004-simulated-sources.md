# 0004: Simulated sources with a known truth

## Context
Real quote, invoice and pricing data is confidential, and even when available it cannot tell whether a
recommended price would have earned more, because the counterfactual is never observed.

## Decision
The pipeline runs on a simulation of the CRM, CPQ, ERP and market sources of a gas-turbine service
business, with planted defects, planted leakage, a confounded pricing process and a randomised price
test. The truth is written to a separate folder that only `bpi evaluate` reads.

## Consequences
Every claim in the README is measured against a known answer. The numbers describe how the methods
behave under these assumptions, not results on a real business; the simulation's assumptions are in
`src/bpi/synth/world.py`.
