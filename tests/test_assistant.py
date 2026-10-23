from datetime import date
from types import SimpleNamespace

import pytest

from bpi.assistant.llm import ClaudeParser, tool_schema
from bpi.assistant.parser import RuleParser, parse_time
from bpi.assistant.semantic import Policy, QuerySpec, Refused, SemanticModel, User, compile_sql, guard_question

AS_OF = date(2026, 9, 30)
MODEL = SemanticModel.load()
POLICY = Policy.load()


def test_relative_periods():
    assert parse_time(" last quarter ", AS_OF)[:2] == (date(2026, 7, 1), date(2026, 10, 1))
    assert parse_time(" last 6 months ", AS_OF)[:2] == (date(2026, 4, 1), date(2026, 10, 1))
    assert parse_time(" q1 2025 ", AS_OF)[:2] == (date(2025, 1, 1), date(2025, 4, 1))
    assert parse_time(" ytd ", AS_OF)[:2] == (date(2026, 1, 1), date(2026, 10, 1))


def test_region_scoped_roles_are_filtered_and_fenced():
    spec, notes = POLICY.apply(QuerySpec(metrics=["win_rate"]), User("sales_rep", "APAC"))
    assert spec.filters["region"] == ["APAC"] and notes
    with pytest.raises(Refused):
        POLICY.apply(QuerySpec(metrics=["win_rate"], filters={"region": ["EMEA"]}), User("sales_rep", "APAC"))
    with pytest.raises(Refused):
        POLICY.apply(QuerySpec(metrics=["pocket_margin_pct"]), User("sales_rep", "APAC"))
    with pytest.raises(Refused):
        POLICY.apply(QuerySpec(metrics=["win_rate"], dimensions=["rep_name"]), User("sales_rep", "APAC"))


def test_compiler_only_uses_names_from_the_model_and_quotes_values():
    with pytest.raises(Refused):
        compile_sql(QuerySpec(metrics=["win_rate"], dimensions=["cost_eur"]), MODEL)
    with pytest.raises(Refused):
        compile_sql(QuerySpec(metrics=["drop table x"]), MODEL)
    sql = compile_sql(QuerySpec(metrics=["win_rate"], filters={"region": ["EMEA'; drop table marts.fct_quotes; --"]}), MODEL)
    assert "'EMEA''; drop table marts.fct_quotes; --'" in sql


def test_write_requests_are_refused():
    with pytest.raises(Refused):
        guard_question("please update the discounts on the 2025 quotes to 10%")
    guard_question("what was the average discount on 2025 quotes?")


def test_rule_parser_reads_a_ranked_question():
    spec = RuleParser(MODEL, AS_OF).parse("Top 3 customers by bookings in EMEA last year")
    assert spec.metrics == ["booked_value_eur"] and spec.dimensions == ["customer_name"]
    assert spec.filters == {"region": ["EMEA"]} and spec.limit == 3 and spec.descending


def test_claude_parser_output_goes_through_the_same_compiler():
    block = SimpleNamespace(
        type="tool_use", input={"metrics": ["win_rate"], "dimensions": ["region"], "start": "2025-01-01", "end": "2026-01-01"}
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[block])))
    spec = ClaudeParser(MODEL, AS_OF, client=client).parse("win rate by region in 2025")
    assert "group by region" in compile_sql(spec, MODEL)
    schema = tool_schema(MODEL)["input_schema"]
    assert "win_rate" in schema["properties"]["metrics"]["items"]["enum"]


def test_assistant_scores_on_its_question_sets(home):
    from bpi.assistant.evaluate import run
    from bpi.config import REPO

    for f in ("questions.yaml", "regression_heldout_v1.yaml"):
        r = run(cases_path=REPO / "tests" / "assistant" / f)
        assert r["answer_accuracy"] == 1.0 and r["policy_violations"] == 0, r["failures"]
