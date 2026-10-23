"""Data contracts for every raw source, and the ingest that enforces them.

Each source has a YAML contract in `contracts/` (owner, key, typed columns with rules, references to
other sources, and SQL checks across sources). Ingest reads the export, drops exact duplicate rows,
and moves every row that breaks the contract to `raw._quarantine` with the reason, instead of
loading it. Rows that only fail because their parent was quarantined are marked as such, so the
data owner sees one root cause rather than a cascade.

Nothing downstream reads a source that has no contract: `bpi handover-check` fails if one is missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

from bpi.config import REPO

# Load order: parents before the sources that reference them.
ORDER = [
    "crm_accounts",
    "crm_users",
    "erp_products",
    "erp_price_list",
    "erp_standard_cost",
    "contracts",
    "fx_rates",
    "market_commodity_index",
    "market_competitor_intel",
    "crm_opportunities",
    "crm_opportunity_stages",
    "cpq_quotes",
    "erp_invoices",
    "erp_off_invoice",
    "erp_rebate_payments",
]


@dataclass
class Contract:
    source: str
    owner: str
    description: str
    key: list[str]
    columns: dict[str, dict]
    relations: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Contract:
        d = yaml.safe_load(path.read_text())
        return cls(
            source=d["source"],
            owner=d["owner"],
            description=d["description"],
            key=d.get("key") or [],
            columns=d["columns"],
            relations=d.get("relations") or [],
            checks=d.get("checks") or [],
        )


def load_contracts(directory: Path | None = None) -> dict[str, Contract]:
    directory = directory or REPO / "contracts"
    return {c.source: c for c in (Contract.load(p) for p in sorted(directory.glob("*.yaml")))}


@dataclass
class SourceReport:
    source: str
    rows_read: int = 0
    duplicates_dropped: int = 0
    loaded: int = 0
    quarantined: dict[str, int] = field(default_factory=dict)


def _coerce(col: pd.Series, typ: str) -> tuple[pd.Series, pd.Series]:
    """Typed values and a mask of values present but not parseable as `typ`."""
    present = col.str.strip() != ""
    if typ == "string":
        return col.where(present, None), pd.Series(False, index=col.index)
    if typ in ("int", "float"):
        num = pd.to_numeric(col.where(present), errors="coerce")
        bad = present & num.isna()
        if typ == "int":
            bad |= present & num.notna() & (num % 1 != 0)
            return num.where(~bad).astype("Int64"), bad
        return num.astype(float), bad
    if typ == "bool":
        m = col.str.strip().str.lower().map({"true": True, "false": False, "1": True, "0": False})
        return m, present & m.isna()
    if typ == "date":
        d = pd.to_datetime(col.where(present), errors="coerce", format="%Y-%m-%d")
        return d.dt.date.where(d.notna(), None), present & d.isna()
    raise ValueError(f"unknown type {typ}")


def validate(df: pd.DataFrame, contract: Contract, as_of: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (typed rows that pass, quarantined rows with a reason). Relations are checked later."""
    reasons = pd.Series("", index=df.index)

    def flag(mask: pd.Series, reason: str) -> None:
        mask = mask.fillna(False).astype(bool)
        reasons[mask & (reasons == "")] = reason

    missing_cols = [c for c in contract.columns if c not in df.columns]
    if missing_cols:
        raise ValueError(f"{contract.source}: export is missing columns {missing_cols}")
    typed = pd.DataFrame(index=df.index)
    for name, rule in contract.columns.items():
        raw = df[name].astype(str)
        val, bad = _coerce(raw, rule["type"])
        typed[name] = val
        flag(bad, f"{name}:invalid_{rule['type']}")
        present = raw.str.strip() != ""
        if rule.get("required"):
            flag(~present, f"{name}:missing")
        if "allowed" in rule:
            ok = (
                val.isin(rule["allowed"])
                if rule["type"] != "float"
                else np.isclose(val.to_numpy(dtype=float)[:, None], np.array(rule["allowed"], dtype=float)[None, :]).any(axis=1)
            )
            flag(present & ~pd.Series(ok, index=df.index), f"{name}:not_allowed")
        if "pattern" in rule:
            flag(present & ~raw.str.fullmatch(rule["pattern"]), f"{name}:pattern")
        for bound, op in (("min", "lt"), ("max", "gt"), ("min_exclusive", "le")):
            if bound not in rule:
                continue
            limit = as_of if rule[bound] == "as_of" else rule[bound]
            if rule["type"] == "date":
                limit = pd.Timestamp(limit).date() if not isinstance(limit, date) else limit
                cmp = val.map(lambda v, lim=limit, o=op: v is not None and (v > lim if o == "gt" else v < lim))
            else:
                v = pd.to_numeric(val, errors="coerce")
                cmp = {"lt": v < limit, "gt": v > limit, "le": v <= limit}[op]
            flag(present & cmp, f"{name}:{bound}")
    if contract.key:
        dupkey = typed.duplicated(contract.key, keep=False) & (reasons == "")
        flag(dupkey, "key_conflict")
    bad = reasons != ""
    q = df[bad].assign(_reason=reasons[bad])
    return typed[~bad].reset_index(drop=True), q


def _row_key(df: pd.DataFrame, contract: Contract) -> pd.Series:
    cols = contract.key or list(df.columns[:2])
    return df[cols].astype(str).agg("|".join, axis=1)


def _run(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[tuple]:
    """Execute and fetch everything, so no result is left open on the connection."""
    return con.execute(sql, params or []).fetchall()


def ingest(raw_dir: Path, db_path: Path, as_of: date, contracts: dict[str, Contract] | None = None) -> dict:
    contracts = contracts or load_contracts()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    _run(con, "create schema raw")
    _run(con, "create table raw._quarantine (source varchar, row_key varchar, reason varchar, row_json varchar)")
    reports: dict[str, SourceReport] = {}
    quarantined_keys: dict[str, set[str]] = {}
    unknown = sorted({p.stem for p in raw_dir.glob("*.csv")} - set(contracts))
    if unknown:
        raise ValueError(f"raw sources without a data contract: {unknown}")
    for source in ORDER:
        c = contracts[source]
        rep = reports[source] = SourceReport(source)
        df = pd.read_csv(raw_dir / f"{source}.csv", dtype=str, keep_default_na=False)
        rep.rows_read = len(df)
        dups = df.duplicated(keep="first")
        rep.duplicates_dropped = int(dups.sum())
        q_parts = [df[dups].assign(_reason="duplicate_row")]
        df = df[~dups].reset_index(drop=True)
        typed, q = validate(df, c, as_of)
        q_parts.append(q)
        # References to other sources: the parent row must exist and have been loaded.
        for rel in c.relations:
            parent, pcol = rel["references"].split(".")
            col = rel["column"]
            loaded = set(con.execute(f"select distinct {pcol} from raw.{parent}").fetchnumpy()[pcol].tolist())
            ok = typed[col].isin(loaded)
            if not ok.all():
                bad = typed[~ok]
                parent_q = bad[col].isin(quarantined_keys.get(parent, set()))
                reason = np.where(parent_q, f"parent_quarantined:{col}", f"unknown_reference:{col}")
                q_parts.append(bad.astype(str).assign(_reason=reason))
                typed = typed[ok].reset_index(drop=True)
        con.register("_typed", typed)
        _run(con, f"create table raw.{source} as select * from _typed")
        con.unregister("_typed")
        # Cross-source checks: rows they select are taken back out of the loaded table.
        for chk in c.checks:
            _run(con, "set search_path = 'raw'")
            keys = [r[0] for r in _run(con, chk["sql"])]
            _run(con, "set search_path = 'main'")
            if keys:
                kcol = c.key[0]
                held = typed[typed[kcol].isin(keys)]
                q_parts.append(held.astype(str).assign(_reason=chk["name"]))
                _run(con, f"delete from raw.{source} where {kcol} in (select unnest(?))", [keys])
        qall = pd.concat([p for p in q_parts if len(p)], ignore_index=True) if any(len(p) for p in q_parts) else None
        if qall is not None:
            rows = qall.drop(columns="_reason")
            keys = _row_key(rows, c)
            quarantined_keys[source] = set(rows[c.key[0]].astype(str)) if c.key else set()
            payload = pd.DataFrame(
                {
                    "source": source,
                    "row_key": keys,
                    "reason": qall["_reason"].to_numpy(),
                    "row_json": [json.dumps(r, default=str) for r in rows.to_dict(orient="records")],
                }
            )
            con.register("_q", payload)
            _run(con, "insert into raw._quarantine select * from _q")
            con.unregister("_q")
            for reason, n in payload.groupby("reason").size().items():
                rep.quarantined[str(reason)] = int(n)
        rep.loaded = int(_run(con, f"select count(*) from raw.{source}")[0][0])
    con.close()
    return {
        s: {
            "rows_read": r.rows_read,
            "duplicates_dropped": r.duplicates_dropped,
            "loaded": r.loaded,
            "quarantined": r.quarantined,
            "owner": contracts[s].owner,
        }
        for s, r in reports.items()
    }


def quarantine_summary(db_path: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(
            "select source, regexp_replace(reason, ':.*', '') as kind, reason, count(*) n "
            "from raw._quarantine group by all order by source, n desc"
        ).df()
    finally:
        con.close()
