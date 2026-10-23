from datetime import date

import pandas as pd

from bpi import warehouse
from bpi.contracts import Contract, validate

CONTRACT = Contract(
    source="t",
    owner="me",
    description="",
    key=["id"],
    columns={
        "id": {"type": "string", "required": True, "pattern": r"^A\d$"},
        "amount": {"type": "float", "required": True, "min_exclusive": 0},
        "ccy": {"type": "string", "required": True, "allowed": ["EUR", "USD"]},
        "day": {"type": "date", "required": False, "max": "as_of"},
        "n": {"type": "int", "required": False, "min": 1},
    },
)


def test_validate_flags_each_rule():
    df = pd.DataFrame(
        {
            "id": ["A1", "A2", "B3", "A4", "A5", "A6", "A6"],
            "amount": ["10", "0", "5", "x", "5", "5", "6"],
            "ccy": ["EUR", "EUR", "EUR", "EUR", "EURO", "USD", "USD"],
            "day": ["2026-01-01", "", "", "2027-01-01", "", "", ""],
            "n": ["1", "", "", "", "", "0.5", ""],
        }
    )
    ok, q = validate(df, CONTRACT, date(2026, 9, 30))
    assert ok["id"].tolist() == ["A1"]
    reasons = dict(zip(q["id"], q["_reason"], strict=True))
    assert reasons["A2"] == "amount:min_exclusive"
    assert reasons["B3"] == "id:pattern"
    assert reasons["A4"] == "amount:invalid_float"
    assert reasons["A5"] == "ccy:not_allowed"
    assert q[q["id"] == "A6"]["_reason"].tolist() == ["n:invalid_int", "key_conflict"]


def test_ingest_quarantines_planted_defects_and_cascades(home):
    q = warehouse.read("select source, reason, count(*) n from raw._quarantine group by all")
    reasons = set(q["reason"])
    assert {"duplicate_row", "amount_implausible_vs_quote", "currency:not_allowed", "rep_id:missing"} <= reasons
    # Invoices of a held quote are held as a consequence, not as a separate defect.
    assert "parent_quarantined:quote_id" in reasons
    # Nothing that failed a contract reached the warehouse.
    assert (
        warehouse.read("select count(*) n from raw.cpq_quotes where net_price_lc <= 0 or currency not in ('EUR','USD','GBP','AED')")["n"][0]
        == 0
    )
