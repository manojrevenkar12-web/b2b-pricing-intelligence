#!/usr/bin/env bash
# Regenerate the simulated sources, ingest them through the contracts and build the warehouse.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m bpi.cli generate
python3 -m bpi.cli ingest
python3 -m bpi.cli build
