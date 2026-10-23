import json
import os

import pytest
from fastapi.testclient import TestClient

KEYS = {"pk": {"role": "pricing_analyst"}, "rk": {"role": "sales_rep", "region": "EMEA"}}


@pytest.fixture(scope="module")
def client(home):
    os.environ["BPI_API_KEYS"] = json.dumps(KEYS)
    from bpi.api import create_app

    return TestClient(create_app())


def h(k):
    return {"Authorization": f"Bearer {k}"}


def test_auth_and_health(client):
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/readyz").status_code == 200
    assert client.get("/v1/model").status_code == 401
    assert client.get("/v1/model", headers=h("pk")).json()["champion"] in ("logistic", "gbm")


def test_recommendation_for_existing_quote(client):
    from bpi import warehouse

    qid = warehouse.read("select quote_id from marts.fct_quotes where service_line = 'spare_parts' and status = 'open' limit 1")[
        "quote_id"
    ][0]
    r = client.get(f"/v1/quotes/{qid}/recommendation", headers=h("pk")).json()
    assert r["status"] in ("recommended", "rep_below_floor")
    assert r["low_ratio"] <= r["recommended_ratio"] <= r["high_ratio"] <= 1.0
    assert r["model_version"]


def test_new_quote_is_enriched_and_fenced_by_region(client, home):
    from bpi import warehouse

    c = warehouse.read("select customer_id, region from marts.dim_customer where region = 'EMEA' limit 1").iloc[0]
    other = warehouse.read("select customer_id from marts.dim_customer where region = 'APAC' limit 1")["customer_id"][0]
    p = warehouse.read("select product_id from marts.dim_product where service_line = 'field_service' limit 1")["product_id"][0]
    body = [
        {
            "customer_id": c["customer_id"],
            "product_id": p,
            "proposed_net_price_eur": 120000,
            "competitor_present": True,
            "quote_date": "2026-09-15",
        }
    ]
    r = client.post("/v1/price-recommendations", headers=h("rk"), json=body)
    assert r.status_code == 200, r.text
    rec = r.json()["recommendations"][0]
    assert rec["status"] in ("recommended", "rep_below_floor")
    body[0]["customer_id"] = other
    assert client.post("/v1/price-recommendations", headers=h("rk"), json=body).status_code == 403
    body[0]["customer_id"] = "C9999"
    assert client.post("/v1/price-recommendations", headers=h("pk"), json=body).status_code == 404
    audit = (home / "out" / "audit" / "recommendations.jsonl").read_text().splitlines()
    assert json.loads(audit[-1])["user"]["role"] == "sales_rep"


def test_ask_respects_role(client):
    ok = client.post("/v1/ask", headers=h("rk"), json={"question": "win rate by service line in 2025"}).json()
    assert ok["ok"] and "region in ('EMEA')" in ok["sql"]
    no = client.post("/v1/ask", headers=h("rk"), json={"question": "pocket margin by service line"}).json()
    assert not no["ok"]
