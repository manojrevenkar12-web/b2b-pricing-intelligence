# Working in this repository

- `bpi run` rebuilds everything and ends with the release gate; `pytest -q` runs unit and pipeline tests.
- Only `src/bpi/evaluate.py` may read `data/truth`. Models, marts, dashboard and API never do.
- Business thresholds go in `config/`, not in code. New raw sources need a contract in `contracts/`.
- Never change the assistant parser to pass `tests/assistant/heldout.yaml`; if you must, move that set
  to a `regression_*.yaml` file and write a new held-out set without looking at the parser.
