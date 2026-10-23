"""Runs the dbt project and reads the marts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

from bpi.config import REPO, paths

DBT_DIR = REPO / "warehouse"


def run_dbt(command: str = "build", db: Path | None = None, extra: list[str] | None = None) -> None:
    """`dbt build` (models and tests) against the DuckDB warehouse, in a subprocess so no
    connection stays open afterwards. Raises if any model or test fails."""
    db = db or paths().warehouse
    out = paths().root / "out" / "dbt"
    env = {**os.environ, "BPI_WAREHOUSE": str(db.resolve()), "DO_NOT_TRACK": "1"}
    args = [
        sys.executable,
        "-c",
        "from dbt.cli.main import cli; cli()",
        command,
        "--project-dir",
        str(DBT_DIR),
        "--profiles-dir",
        str(DBT_DIR),
        "--target-path",
        str(out / "target"),
        "--log-path",
        str(out / "logs"),
        *(extra or []),
    ]
    res = subprocess.run(args, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        tail = "\n".join((res.stdout + res.stderr).splitlines()[-30:])
        raise RuntimeError(f"dbt {command} failed:\n{tail}")


def connect(db: Path | None = None, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db or paths().warehouse), read_only=read_only)


def read(sql: str, params: list | None = None, db: Path | None = None) -> pd.DataFrame:
    con = connect(db)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def quotes(db: Path | None = None) -> pd.DataFrame:
    df = read("select * from marts.fct_quotes", db=db)
    for c in ("quote_date", "decision_date", "quote_month", "decision_month"):
        df[c] = pd.to_datetime(df[c])
    return df
