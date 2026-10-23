"""`ask()`: question in, governed answer out."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bpi import warehouse
from bpi.assistant import llm
from bpi.assistant.parser import RuleParser
from bpi.assistant.semantic import Policy, QuerySpec, Refused, SemanticModel, User, compile_sql, guard_question


@dataclass
class Answer:
    question: str
    ok: bool
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    refusal: str | None = None
    parser: str = "rules"
    ms: float = 0.0

    def dict(self) -> dict:
        return self.__dict__.copy()


class Assistant:
    def __init__(self, as_of: date, db: Path | None = None, parser=None):
        self.model = SemanticModel.load()
        self.policy = Policy.load()
        self.db = db
        self.parser = parser or (llm.ClaudeParser(self.model, as_of) if llm.enabled() else RuleParser(self.model, as_of))

    def plan(self, question: str, user: User) -> tuple[QuerySpec, str, list[str]]:
        guard_question(question)
        spec = self.parser.parse(question)
        spec, notes = self.policy.apply(spec, user)
        return spec, compile_sql(spec, self.model), notes

    def ask(self, question: str, user: User) -> Answer:
        t0 = time.perf_counter()
        name = "claude" if isinstance(self.parser, llm.ClaudeParser) else "rules"
        try:
            _spec, sql, notes = self.plan(question, user)
        except Refused as e:
            return Answer(question, False, refusal=str(e), parser=name, ms=(time.perf_counter() - t0) * 1000)
        df = warehouse.read(sql, db=self.db)
        rows = [[v.isoformat() if hasattr(v, "isoformat") else v for v in r] for r in df.itertuples(index=False)]
        return Answer(question, True, sql, list(df.columns), rows, notes, parser=name, ms=(time.perf_counter() - t0) * 1000)
