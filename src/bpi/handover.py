"""Handover readiness: can the internal team own this solution without the people who built it?

When an externally developed analytics solution is transferred to an internal team, ownership
fails in predictable places: a source nobody owns, a model nobody can retrain, a threshold that
lives in someone's head, a test suite that only the vendor can run. This check makes those
explicit. It inspects the repository and the latest run and prints a checklist; `bpi handover-check`
fails if a required item is missing. docs/HANDOVER.md is the process around it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import yaml

from bpi.config import REPO, paths
from bpi.contracts import load_contracts

MODEL_CARD_SECTIONS = ["Purpose", "Data", "Evaluation", "Limits", "Owner", "Retraining", "Monitoring"]
SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9-]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (?:RSA )?PRIVATE KEY-----",
    r"(?i)password\s*[:=]\s*['\"][^'\"$]{6,}['\"]",
]


@dataclass
class Item:
    area: str
    check: str
    ok: bool
    detail: str = ""
    required: bool = True


def _model_cards() -> list[Item]:
    items = []
    for name in ("win-probability", "price-response", "price-recommendation", "bookings-forecast", "leakage"):
        p = REPO / "docs" / "model-cards" / f"{name}.md"
        if not p.exists():
            items.append(Item("models", f"model card {name}", False, "missing"))
            continue
        text = p.read_text()
        missing = [s for s in MODEL_CARD_SECTIONS if not re.search(rf"^## {s}\b", text, re.M)]
        items.append(Item("models", f"model card {name}", not missing, f"missing sections: {missing}" if missing else ""))
    return items


def _dbt_docs() -> list[Item]:
    undocumented, untested = [], []
    schema = yaml.safe_load((REPO / "warehouse" / "models" / "marts" / "schema.yml").read_text())
    for f in (REPO / "warehouse" / "models" / "marts").glob("*.sql"):
        name = f.stem
        model = next((m for m in schema["models"] if m["name"] == name), None)
        if model is None or not model.get("description"):
            undocumented.append(name)
        elif name.startswith(("fct_quotes", "fct_price_waterfall", "dim_")) and not any(c.get("tests") for c in model.get("columns", [])):
            untested.append(name)
    return [
        Item("warehouse", "every mart has a description", not undocumented, f"undocumented: {undocumented}" if undocumented else ""),
        Item("warehouse", "core marts have key tests", not untested, f"untested: {untested}" if untested else ""),
    ]


def run() -> list[Item]:
    items: list[Item] = []
    contracts = load_contracts()
    raw = {p.stem for p in paths().raw.glob("*.csv")}
    missing = sorted(raw - set(contracts))
    items.append(
        Item(
            "data",
            "every raw source has a data contract",
            not missing,
            f"no contract: {missing}" if missing else f"{len(contracts)} contracts",
        )
    )
    no_owner = [c.source for c in contracts.values() if not c.owner]
    items.append(Item("data", "every contract names an owner", not no_owner, str(no_owner) if no_owner else ""))
    items += _dbt_docs()
    items += _model_cards()
    reg = paths().registry / "current.json"
    items.append(Item("models", "a current model version is registered", reg.exists()))
    if reg.exists():
        v = json.loads(reg.read_text())["version"]
        meta = json.loads((paths().registry / v / "metadata.json").read_text())
        items.append(
            Item(
                "models",
                "the registered model records its data fingerprint and code version",
                bool(meta.get("data_fingerprint")) and bool(meta.get("code_version")),
            )
        )
    ev = paths().reports / "evaluation.json"
    items.append(Item("evaluation", "an evaluation of the current version exists", ev.exists()))
    if ev.exists() and reg.exists():
        e = json.loads(ev.read_text())
        items.append(
            Item(
                "evaluation",
                "the evaluation is of the registered version",
                e.get("model_version") == json.loads(reg.read_text())["version"],
            )
        )
        items.append(
            Item(
                "evaluation",
                "the release gate passed",
                bool(e.get("gate_passed")),
                "" if e.get("gate_passed") else "see reports/EVALUATION.md",
            )
        )
    for doc, what in [("RUNBOOK.md", "runbook"), ("HANDOVER.md", "handover process"), ("ARCHITECTURE.md", "architecture")]:
        items.append(Item("operations", f"{what} documented", (REPO / "docs" / doc).exists()))
    items.append(Item("operations", "code owners assigned", (REPO / ".github" / "CODEOWNERS").exists()))
    items.append(
        Item(
            "operations",
            "business thresholds live in reviewed config, not in code",
            (REPO / "config" / "pricing.yaml").exists() and (REPO / "config" / "objectives.yaml").exists(),
        )
    )
    items.append(Item("operations", "CI runs the full pipeline and the gate", (REPO / ".github" / "workflows" / "ci.yml").exists()))
    leaks = []
    for p in REPO.rglob("*"):
        if (
            p.is_file()
            and p.suffix in {".py", ".yml", ".yaml", ".md", ".json", ".toml", ".sh", ".sql"}
            and not any(part in {".git", "out", "data", ".venv", "node_modules"} for part in p.parts)
        ):
            text = p.read_text(errors="ignore")
            if any(re.search(pat, text) for pat in SECRET_PATTERNS):
                leaks.append(str(p.relative_to(REPO)))
    items.append(Item("security", "no secrets committed", not leaks, f"check: {leaks}" if leaks else ""))
    return items


def report(items: list[Item]) -> tuple[bool, str]:
    ok = all(i.ok for i in items if i.required)
    lines = [f"{'OK  ' if i.ok else 'MISS'}  [{i.area}] {i.check}" + (f" - {i.detail}" if i.detail else "") for i in items]
    return ok, "\n".join(lines)
