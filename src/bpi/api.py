"""HTTP API for sales tools (CPQ, CRM) and for the assistant.

Every call needs an API key, which maps to a role and, for sales roles, a region. Sales users can
only price and ask about customers in their region. Every recommendation is written to an audit
log with the user, the inputs, the answer and the model version, so a price can be explained
later.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from bpi import quote_context, warehouse
from bpi.assistant.semantic import User
from bpi.assistant.service import Assistant
from bpi.config import load_settings, paths
from bpi.models.recommend import recommend
from bpi.pipeline import load_current


class QuoteIn(BaseModel):
    quote_id: str | None = None
    customer_id: str
    product_id: str
    scope_qty: int = Field(1, ge=1, le=1000)
    proposed_net_price_eur: float = Field(gt=0)
    competitor_present: bool = False
    outage_urgent: bool = False
    quote_date: str | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def _keys() -> dict[str, User]:
    raw = os.environ.get("BPI_API_KEYS")
    if not raw:
        raise RuntimeError("BPI_API_KEYS is not set (JSON: {key: {role, region}})")
    return {k: User(**v) for k, v in json.loads(raw).items()}


def create_app() -> FastAPI:
    st = load_settings()
    app = FastAPI(title="B2B pricing intelligence", version="0.1.0")
    state: dict = {}
    lock = threading.Lock()
    audit = paths().root / "out" / "audit" / "recommendations.jsonl"

    def model():
        if "model" not in state:
            state["model"] = load_current()
        return state["model"]

    def assistant() -> Assistant:
        if "assistant" not in state:
            state["assistant"] = Assistant(st.as_of)
        return state["assistant"]

    def user(authorization: str = Header(default="")) -> User:
        key = authorization.removeprefix("Bearer ").strip()
        u = _keys().get(key)
        if u is None:
            raise HTTPException(401, "invalid API key")
        return u

    def _log(entry: dict) -> None:
        audit.parent.mkdir(parents=True, exist_ok=True)
        with lock, Path(audit).open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _price(df, u: User) -> list[dict]:
        if u.region and (df["region"] != u.region).any():
            raise HTTPException(403, f"you can only price customers in {u.region}")
        m = model()
        recs = [r.dict() for r in recommend(df, m.response, st.guardrails)]
        for r in recs:
            r["model_version"] = m.meta["version"]
            _log({"at": datetime.now(UTC).isoformat(), "user": u.__dict__, "recommendation": r})
        return recs

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/readyz")
    def readyz():
        try:
            m = model()
            warehouse.read("select 1")
        except Exception as e:
            raise HTTPException(503, f"not ready: {e}") from e
        return {"ok": True, "model_version": m.meta["version"]}

    @app.get("/v1/model")
    def model_info(u: User = Depends(user)):
        m = model()
        return {k: m.meta[k] for k in ("version", "champion", "validation", "price_tests", "train_window", "data_fingerprint")}

    @app.post("/v1/price-recommendations")
    def price(quotes: list[QuoteIn], u: User = Depends(user)):
        if not quotes or len(quotes) > 200:
            raise HTTPException(422, "send between 1 and 200 quotes")
        try:
            df = quote_context.build([q.model_dump() for q in quotes])
        except quote_context.UnknownReference as e:
            raise HTTPException(404, str(e)) from e
        return {"recommendations": _price(df, u)}

    @app.get("/v1/quotes/{quote_id}/recommendation")
    def existing(quote_id: str, u: User = Depends(user)):
        df = warehouse.read("select * from marts.fct_quotes where quote_id = ?", [quote_id])
        if df.empty:
            raise HTTPException(404, f"unknown quote {quote_id}")
        return _price(df, u)[0]

    @app.post("/v1/ask")
    def ask(body: AskIn, u: User = Depends(user)):
        return assistant().ask(body.question, u).dict()

    return app
