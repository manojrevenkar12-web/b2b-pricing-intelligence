"""One generated world for the whole test session, built in a temporary BPI_HOME."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def home(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("bpi")
    os.environ["BPI_HOME"] = str(root)
    from bpi.cli import cmd_build, cmd_generate, cmd_ingest, cmd_train

    class A:
        seed = 7

    for step in (cmd_generate, cmd_ingest, cmd_build, cmd_train):
        assert step(A()) == 0
    return root


@pytest.fixture(scope="session")
def evaluated(home) -> dict:
    import json

    from bpi.cli import cmd_evaluate

    assert cmd_evaluate(None) == 0
    return json.loads((home / "reports" / "evaluation.json").read_text())


@pytest.fixture(scope="session")
def settings():
    from bpi.config import load_settings

    return load_settings()
