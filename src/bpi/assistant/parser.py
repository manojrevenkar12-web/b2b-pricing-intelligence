"""Turns a question into a QuerySpec using the semantic model's names and synonyms.

This is the deterministic parser: no model call, same answer every time, and it runs in CI. It
covers the questions sales and pricing teams ask most (a measure, broken down by something,
filtered to a region, line, segment or period, optionally top or bottom N). `llm.py` is the
alternative for free-form phrasing; both produce the same QuerySpec and go through the same
compiler and policy.
"""

from __future__ import annotations

import re
from datetime import date

from bpi.assistant.semantic import QuerySpec, Refused, SemanticModel

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    )
}

# Words that name a dimension value, mapped to (dimension, value).
VALUES: dict[str, tuple[str, object]] = {
    "emea": ("region", "EMEA"),
    "europe": ("region", "EMEA"),
    "americas": ("region", "AMERICAS"),
    "apac": ("region", "APAC"),
    "asia pacific": ("region", "APAC"),
    "middle east": ("region", "MIDDLE_EAST"),
    "spare parts": ("service_line", "spare_parts"),
    "parts": ("service_line", "spare_parts"),
    "field service": ("service_line", "field_service"),
    "repairs": ("service_line", "repairs"),
    "repair": ("service_line", "repairs"),
    "upgrades": ("service_line", "upgrades"),
    "modernisation": ("service_line", "upgrades"),
    "ltsa": ("service_line", "ltsa"),
    "ltsas": ("service_line", "ltsa"),
    "long-term service agreements": ("service_line", "ltsa"),
    "service agreements": ("service_line", "ltsa"),
    "utilities": ("segment", "utility"),
    "utility": ("segment", "utility"),
    "ipps": ("segment", "ipp"),
    "independent power producers": ("segment", "ipp"),
    "oil and gas": ("segment", "oil_and_gas"),
    "industrial": ("segment", "industrial"),
    "with a competitor": ("competitor_present", True),
    "with competitors": ("competitor_present", True),
    "against a competitor": ("competitor_present", True),
    "against competitors": ("competitor_present", True),
    "competitive": ("competitor_present", True),
    "without a competitor": ("competitor_present", False),
    "without competitors": ("competitor_present", False),
    "no competitor": ("competitor_present", False),
    "uncontested": ("competitor_present", False),
    "urgent": ("outage_urgent", True),
    "where a competitor": ("competitor_present", True),
    "when a competitor": ("competitor_present", True),
    "competitor involved": ("competitor_present", True),
    "during an outage": ("outage_urgent", True),
}


def _quarter(y: int, q: int) -> tuple[date, date]:
    start = date(y, 3 * (q - 1) + 1, 1)
    end = date(y + (q == 4), (3 * q) % 12 + 1, 1)
    return start, end


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def parse_time(text: str, as_of: date) -> tuple[date | None, date | None, str]:
    """Time range named in the question (end exclusive) and the text with it removed."""
    t = text
    nxt = date.fromordinal(as_of.toordinal() + 1)
    patterns = [
        (r"\bq([1-4])\s*(20\d\d)\b", lambda m: _quarter(int(m[2]), int(m[1]))),
        (
            r"\b(" + "|".join(MONTHS) + r")\s+(20\d\d)\b",
            lambda m: (date(int(m[2]), MONTHS[m[1]], 1), _add_months(date(int(m[2]), MONTHS[m[1]], 1), 1)),
        ),
        (
            r"\b(?:last|past|previous)\s+(\d+)\s+months\b",
            lambda m: (_add_months(date(nxt.year, nxt.month, 1), -int(m[1])), date(nxt.year, nxt.month, 1)),
        ),
        (r"\b(?:last|past|previous)\s+quarter\b", lambda m: _last_quarter(as_of)),
        (r"\b(?:this year|year to date|ytd)\b", lambda m: (date(as_of.year, 1, 1), nxt)),
        (r"\blast year\b", lambda m: (date(as_of.year - 1, 1, 1), date(as_of.year, 1, 1))),
        (r"\bsince\s+(20\d\d)\b", lambda m: (date(int(m[1]), 1, 1), nxt)),
        (r"\b(?:in|for|during|of)\s+(20\d\d)\b", lambda m: (date(int(m[1]), 1, 1), date(int(m[1]) + 1, 1, 1))),
        (r"\b(20\d\d)\b", lambda m: (date(int(m[1]), 1, 1), date(int(m[1]) + 1, 1, 1))),
    ]
    for pat, f in patterns:
        m = re.search(pat, t)
        if m:
            s, e = f(m)
            return s, e, t[: m.start()] + " " + t[m.end() :]
    return None, None, t


def _last_quarter(as_of: date) -> tuple[date, date]:
    q = (as_of.month - 1) // 3 + 1
    s, e = _quarter(as_of.year, q)
    if e > date.fromordinal(as_of.toordinal() + 1):  # current quarter not complete
        y, q = (as_of.year, q - 1) if q > 1 else (as_of.year - 1, 4)
        s, e = _quarter(y, q)
    return s, e


def _phrases(entries: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(phrase, name) pairs, longest phrase first so 'customer segment' wins over 'customer'."""
    out = []
    for name, syns in entries.items():
        for p in [name.replace("_", " "), *syns]:
            out.append((p.lower(), name))
    return sorted(set(out), key=lambda x: -len(x[0]))


def _take(text: str, phrase: str, mark: str = "#") -> tuple[bool, str, int]:
    """Find `phrase` as whole words, replace it with a marker; a phrase right after a filter value
    ("urgent quotes", "oil and gas customers") qualifies the value and is not taken."""
    for m in re.finditer(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", text):
        if mark == "#" and text[: m.start()].rstrip().endswith("#v"):
            continue
        return True, text[: m.start()] + f" {mark} " + text[m.end() :], m.start()
    return False, text, -1


class RuleParser:
    def __init__(self, model: SemanticModel, as_of: date):
        self.model, self.as_of = model, as_of
        metrics: dict[str, list[str]] = {}
        dims: dict[str, list[str]] = {}
        for t in model.tables.values():
            for name, m in t.metrics.items():
                metrics[name] = m.get("synonyms", [])
            for name, d in t.dimensions.items():
                dims.setdefault(name, d.get("synonyms", []))
        self.metric_phrases = _phrases(metrics)
        self.dim_phrases = _phrases(dims)

    def parse(self, question: str) -> QuerySpec:
        text = " " + question.lower().replace("?", " ").replace(",", " ") + " "
        start, end, text = parse_time(text, self.as_of)

        # Top / bottom N.
        limit, descending, ranked = None, True, False
        m = re.search(r"\b(top|bottom|highest|lowest|best|worst|most|least)\s+(\d+)?", text)
        if m:
            ranked = True
            descending = m[1] in ("top", "highest", "best", "most")
            limit = int(m[2]) if m[2] else 10
        elif re.search(r"\bwhich\b.*\b(highest|lowest|most|least)\b", text):
            ranked = True

        # Explicit breakdowns first ("split by outage urgency", "by region and segment"), so a
        # breakdown word is not read as a filter value.
        dims_at: list[tuple[int, str]] = []
        lead = r"(?:by|per|split by|broken down by|for each|across)\s+(?:(?:[a-z_-]+\s+)?and\s+)?"
        for phrase, name in self.dim_phrases:
            m3 = re.search(r"\b" + lead + "(" + re.escape(phrase) + r")(?![\w-])", text)
            if not m3:
                m3 = re.search(r"\b(?:by|per|split by|broken down by)\s+[a-z_ ]+?\s+and\s+(" + re.escape(phrase) + r")(?![\w-])", text)
            if m3 and name not in [n for _, n in dims_at]:
                dims_at.append((m3.start(1), name))
                text = text[: m3.start(1)] + " # " + text[m3.end(1) :]

        # Filter values (longest first), then measures, then other breakdowns.
        filters: dict[str, list] = {}
        m2 = re.search(r"\bwith and without (?:a )?competitors?\b|\bwith or without (?:a )?competitors?\b", text)
        if m2:
            filters["competitor_present"] = [True, False]
            text = text[: m2.start()] + " #v " + text[m2.end() :]
        for phrase, (dim, val) in sorted(VALUES.items(), key=lambda x: -len(x[0])):
            found, text, _ = _take(text, phrase, mark="#v")
            if found and val not in filters.get(dim, []):
                filters.setdefault(dim, []).append(val)

        metrics: list[tuple[int, str]] = []
        for phrase, name in self.metric_phrases:
            found, text, pos = _take(text, phrase)
            if found and name not in [n for _, n in metrics]:
                metrics.append((pos, name))
        if not metrics:
            raise Refused(
                "I could not find a measure in the question. I can answer about: "
                + ", ".join(sorted({n for t in self.model.tables.values() for n in t.metrics}))
            )
        metric_names = [n for _, n in sorted(metrics)]

        # A breakdown asked for explicitly wins over a single value of the same dimension
        # ("when the outage is urgent ... split by outage urgency").
        for _, name in dims_at:
            if len(filters.get(name, [])) == 1:
                del filters[name]
        for phrase, name in self.dim_phrases:
            found, text, pos = _take(text, phrase)
            if found and name not in [n for _, n in dims_at] and name not in filters:
                dims_at.append((pos, name))
        dims = [n for _, n in sorted(dims_at)]

        # Several values of one dimension ("EMEA and APAC", "with and without a competitor") are a
        # comparison: break the answer down by that dimension as well.
        for dim, vals in filters.items():
            if len(vals) > 1 and dim not in dims:
                dims.append(dim)

        spec = QuerySpec(metrics=metric_names, dimensions=dims, filters=filters, start=start, end=end)
        if ranked:
            spec.order_by = metric_names[0]
            spec.descending = descending
            spec.limit = limit
        return spec
