"""Scores the assistant on tests/assistant/questions.yaml.

An answer is correct when running the parsed question gives the same table as running the agreed
query. A policy violation is any answer to a question that must be refused, or an answer for a
region-scoped user that is not limited to their region.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from bpi import warehouse
from bpi.assistant.parser import RuleParser
from bpi.assistant.semantic import Policy, QuerySpec, Refused, SemanticModel, User, compile_sql, guard_question
from bpi.config import REPO

CASES = REPO / "tests" / "assistant" / "questions.yaml"


def _spec(d: dict) -> QuerySpec:
    return QuerySpec(
        metrics=d["metrics"],
        dimensions=d.get("dimensions", []),
        filters=d.get("filters", {}),
        start=d.get("start"),
        end=d.get("end"),
        order_by=d.get("order_by"),
        descending=d.get("descending", True),
        limit=d.get("limit"),
    )


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if a.shape != b.shape:
        return False
    for x, y in zip(a.itertuples(index=False), b.itertuples(index=False), strict=True):
        for u, v in zip(x, y, strict=True):
            if isinstance(u, float) or isinstance(v, float):
                if not (pd.isna(u) and pd.isna(v)) and not np.isclose(float(u), float(v), rtol=1e-9, atol=1e-9):
                    return False
            elif u != v:
                return False
    return True


def run(parser=None, db: Path | None = None, cases_path: Path = CASES) -> dict:
    cfg = yaml.safe_load(cases_path.read_text())
    model, policy = SemanticModel.load(), Policy.load()
    parser = parser or RuleParser(model, cfg["as_of"])
    results = []
    for c in cfg["cases"]:
        user = User(**c["user"])
        out = {"question": c["q"], "role": user.role, "refuse_expected": bool(c.get("refuse"))}
        try:
            guard_question(c["q"])
            spec, _notes = policy.apply(parser.parse(c["q"]), user)
            sql = compile_sql(spec, model)
            got = warehouse.read(sql, db=db)
            out["refused"] = False
        except Refused as e:
            out["refused"], out["reason"] = True, str(e)
            got, spec = None, None
        if c.get("refuse"):
            out["correct"] = out["refused"]
            out["policy_violation"] = not out["refused"]
        else:
            gold = warehouse.read(compile_sql(_spec(c["expect"]), model), db=db)
            out["correct"] = (not out["refused"]) and _same(got, gold)
            scoped = policy.roles[user.role]["regions"] == "own"
            out["policy_violation"] = bool(scoped and spec is not None and spec.filters.get("region") != [user.region])
            if not out["correct"]:
                out["parsed"] = None if spec is None else spec.__dict__
        results.append(out)
    df = pd.DataFrame(results)
    return {
        "questions": len(df),
        "answer_accuracy": round(float(df["correct"].mean()), 3),
        "answerable_accuracy": round(float(df.loc[~df["refuse_expected"], "correct"].mean()), 3),
        "refusals_correct": round(float(df.loc[df["refuse_expected"], "correct"].mean()), 3),
        "policy_violations": int(df["policy_violation"].sum()),
        "failures": [r for r in results if not r["correct"]],
    }
