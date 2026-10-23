"""The governed layer between a question and the warehouse.

A question never becomes free-form SQL. It becomes a `QuerySpec` (metrics, dimensions, filters,
time range, ordering) naming only things defined in the semantic model; the compiler checks the
spec against the user's role, adds the region filter the role requires, and only then writes SQL
from the definitions in the model. A parser that misreads a question can produce a wrong answer to
a different question, but it cannot reach a table, a column, a region or a metric the user may not
see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from bpi.config import REPO

SEMANTIC = REPO / "semantic" / "pricing_semantic_view.yaml"
POLICY = REPO / "semantic" / "access_policy.yaml"


class Refused(Exception):
    """The question cannot be answered for this user; the message says why."""


WRITE_INTENT = re.compile(
    r"\b(update|change|set|bump|modify|edit|overwrite|delete|drop|remove|insert|truncate|alter|correct|adjust|fix)\b"
    r"(?=.*\b(data|numbers?|quotes?|discounts?|prices?|table|records?|values?|invoices?|rows?)\b)",
    re.I,
)


def guard_question(question: str) -> None:
    """The assistant only reads. A request to change data is refused outright rather than answered
    with a read, so nobody believes a number was changed."""
    if WRITE_INTENT.search(question):
        raise Refused("I can only read the pricing data; changes go through the CPQ and its approval workflow")


@dataclass
class Table:
    name: str
    relation: str  # schema.table in the local warehouse
    time_dimension: str
    dimensions: dict[str, dict]
    metrics: dict[str, dict]


@dataclass
class SemanticModel:
    tables: dict[str, Table]
    raw: dict

    @classmethod
    def load(cls, path: Path = SEMANTIC) -> SemanticModel:
        raw = yaml.safe_load(path.read_text())
        tables = {}
        for t in raw["tables"]:
            bt = t["base_table"]
            tables[t["name"]] = Table(
                name=t["name"],
                relation=f"{bt['schema']}.{bt['table']}".lower(),
                time_dimension=t["time_dimensions"][0]["name"],
                dimensions={d["name"]: d for d in t["dimensions"]},
                metrics={m["name"]: m for m in t["metrics"]},
            )
        return cls(tables, raw)

    def table_for(self, metrics: list[str]) -> Table:
        for t in self.tables.values():
            if all(m in t.metrics for m in metrics):
                return t
        raise Refused("those measures live in different tables (quotes vs invoices); ask about them separately: " + ", ".join(metrics))


@dataclass
class User:
    role: str
    region: str | None = None


@dataclass
class QuerySpec:
    metrics: list[str]
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, list] = field(default_factory=dict)
    start: date | None = None
    end: date | None = None  # exclusive
    order_by: str | None = None
    descending: bool = True
    limit: int | None = None

    def key(self) -> tuple:
        return (
            tuple(sorted(self.metrics)),
            tuple(sorted(self.dimensions)),
            tuple(sorted((k, tuple(sorted(map(str, v)))) for k, v in self.filters.items())),
            self.start,
            self.end,
            self.order_by,
            self.descending if self.order_by else None,
            self.limit,
        )


@dataclass
class Policy:
    roles: dict[str, dict]

    @classmethod
    def load(cls, path: Path = POLICY) -> Policy:
        return cls(yaml.safe_load(path.read_text())["roles"])

    def apply(self, spec: QuerySpec, user: User) -> tuple[QuerySpec, list[str]]:
        """Returns the spec the user may run and notes on what was enforced; raises `Refused`."""
        if user.role not in self.roles:
            raise Refused(f"unknown role {user.role!r}")
        rule = self.roles[user.role]
        notes: list[str] = []
        denied_m = [m for m in spec.metrics if m in rule["denied_metrics"]]
        if denied_m:
            raise Refused(f"the role {user.role} cannot see {', '.join(denied_m)} (cost-based measures)")
        used_dims = set(spec.dimensions) | set(spec.filters)
        denied_d = sorted(used_dims & set(rule["denied_dimensions"]))
        if denied_d:
            raise Refused(f"the role {user.role} cannot break results down by {', '.join(denied_d)}")
        if rule["regions"] == "own":
            if not user.region:
                raise Refused(f"the role {user.role} needs a region")
            asked = spec.filters.get("region")
            if asked and any(r != user.region for r in asked):
                raise Refused(f"the role {user.role} can only see {user.region}")
            spec = QuerySpec(**{**spec.__dict__, "filters": {**spec.filters, "region": [user.region]}})
            notes.append(f"limited to your region ({user.region})")
        return spec, notes


def _literal(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def compile_sql(spec: QuerySpec, model: SemanticModel) -> str:
    """SQL from the definitions in the semantic model. Every name is checked first."""
    if not spec.metrics:
        raise Refused("no measure in the question")
    t = model.table_for(spec.metrics)
    for d in list(spec.dimensions) + list(spec.filters):
        if d not in t.dimensions:
            raise Refused(f"{d} is not available for {t.name}")
    if spec.order_by and spec.order_by not in t.metrics:
        raise Refused(f"cannot order by {spec.order_by}")
    select = [t.dimensions[d]["expr"] + f" as {d}" for d in spec.dimensions]
    select += [f"{t.metrics[m]['expr']} as {m}" for m in spec.metrics]
    where = []
    for d, values in spec.filters.items():
        where.append(f"{t.dimensions[d]['expr']} in ({', '.join(_literal(v) for v in values)})")
    td = t.time_dimension
    if spec.start:
        where.append(f"{td} >= date '{spec.start.isoformat()}'")
    if spec.end:
        where.append(f"{td} < date '{spec.end.isoformat()}'")
    sql = f"select {', '.join(select)} from {t.relation}"
    if where:
        sql += " where " + " and ".join(where)
    if spec.dimensions:
        sql += " group by " + ", ".join(t.dimensions[d]["expr"] for d in spec.dimensions)
    if spec.order_by:
        sql += f" order by {spec.order_by} {'desc' if spec.descending else 'asc'} nulls last"
    elif spec.dimensions:
        sql += " order by " + ", ".join(spec.dimensions)
    if spec.limit:
        sql += f" limit {int(spec.limit)}"
    return sql
