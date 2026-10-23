"""Question to QuerySpec with Claude, for phrasing the rule parser does not cover.

The model is given the semantic model (names, descriptions, synonyms, sample values) and must call
one tool whose input schema only admits names from it. It never writes SQL and never sees data or
the user's permissions; its output goes through the same compiler and access policy as the rule
parser's. Off unless `BPI_ASSISTANT_LLM=1` and `ANTHROPIC_API_KEY` are set.
"""

from __future__ import annotations

import os
from datetime import date

from bpi.assistant.semantic import QuerySpec, Refused, SemanticModel

DEFAULT_MODEL = os.environ.get("BPI_LLM_MODEL", "claude-sonnet-5-5")


def tool_schema(model: SemanticModel) -> dict:
    metrics = sorted({m for t in model.tables.values() for m in t.metrics})
    dims = sorted({d for t in model.tables.values() for d in t.dimensions})
    return {
        "name": "query_spec",
        "description": "The measures, breakdowns, filters and period that answer the question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string", "enum": metrics}, "minItems": 1},
                "dimensions": {"type": "array", "items": {"type": "string", "enum": dims}},
                "filters": {
                    "type": "object",
                    "properties": {d: {"type": "array", "items": {"type": ["string", "boolean"]}} for d in dims},
                    "additionalProperties": False,
                },
                "start": {"type": "string", "format": "date", "description": "inclusive"},
                "end": {"type": "string", "format": "date", "description": "exclusive"},
                "order_by": {"type": "string", "enum": metrics},
                "descending": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cannot_answer": {"type": "string", "description": "why, if no measure fits"},
            },
            "required": ["metrics"],
        },
    }


def system_prompt(model: SemanticModel, as_of: date) -> str:
    lines = [
        "You translate questions about Gas Services pricing into a query specification.",
        f"The data runs to {as_of.isoformat()}; resolve relative periods against that date.",
        "Use only names from the tool schema. Filter values must match the sample values exactly.",
        "If no measure fits, set cannot_answer instead of guessing.",
        "",
    ]
    for t in model.raw["tables"]:
        lines.append(f"Table {t['name']}: {t['description']}")
        for m in t["metrics"]:
            lines.append(f"  measure {m['name']}: also called {', '.join(m.get('synonyms', []))}")
        for d in t["dimensions"]:
            sv = f"; values {d['sample_values']}" if d.get("sample_values") else ""
            lines.append(f"  breakdown {d['name']}: also called {', '.join(d.get('synonyms', []))}{sv}")
    return "\n".join(lines)


class ClaudeParser:
    def __init__(self, model: SemanticModel, as_of: date, client=None, model_name: str = DEFAULT_MODEL):
        self.model, self.as_of, self.model_name = model, as_of, model_name
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def parse(self, question: str) -> QuerySpec:
        resp = self.client.messages.create(
            model=self.model_name,
            max_tokens=600,
            system=system_prompt(self.model, self.as_of),
            tools=[tool_schema(self.model)],
            tool_choice={"type": "tool", "name": "query_spec"},
            messages=[{"role": "user", "content": question}],
        )
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            raise Refused("the assistant did not return a query")
        x = dict(block.input)
        if x.get("cannot_answer"):
            raise Refused(str(x["cannot_answer"]))
        return QuerySpec(
            metrics=list(x["metrics"]),
            dimensions=list(x.get("dimensions") or []),
            filters={k: list(v) for k, v in (x.get("filters") or {}).items()},
            start=date.fromisoformat(x["start"]) if x.get("start") else None,
            end=date.fromisoformat(x["end"]) if x.get("end") else None,
            order_by=x.get("order_by"),
            descending=bool(x.get("descending", True)),
            limit=x.get("limit"),
        )


def enabled() -> bool:
    return os.environ.get("BPI_ASSISTANT_LLM") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))
