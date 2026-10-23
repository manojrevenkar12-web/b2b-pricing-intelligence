"""Builds the model inputs for a new quote from the warehouse, the way the CPQ would send it.

A sales tool only knows the customer, the product, the scope, the proposed price and whether a
competitor is bidding. Everything else (list price, standard cost, customer history, competitor
price level, alloy index) comes from the marts as of the quote date, so a recommendation for a new
quote uses exactly the inputs the model was trained on.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from bpi import warehouse


class UnknownReference(ValueError):
    pass


def build(quotes: list[dict], db=None) -> pd.DataFrame:
    con = warehouse.connect(db)
    rows = []
    try:
        for q in quotes:
            d = date.fromisoformat(str(q.get("quote_date") or date.today().isoformat()))
            month = date(d.year, d.month, 1)
            prod = con.execute(
                "select product_id, service_line, product_family, unit_type from marts.dim_product where product_id = ?",
                [q["product_id"]],
            ).fetchone()
            cust = con.execute(
                "select customer_id, region, segment, strategic_account, relationship_since from marts.dim_customer where customer_id = ?",
                [q["customer_id"]],
            ).fetchone()
            if prod is None:
                raise UnknownReference(f"unknown product {q['product_id']}")
            if cust is None:
                raise UnknownReference(f"unknown customer {q['customer_id']}")
            # Latest price list and standard cost on or before the quote date.
            lp = con.execute(
                "select list_price_eur from staging.stg_price_list where product_id = ? and valid_from <= ? order by valid_from desc limit 1",
                [q["product_id"], d],
            ).fetchone()
            cost = con.execute(
                "select unit_cost_eur from staging.stg_standard_cost where product_id = ? and month <= ? order by month desc limit 1",
                [q["product_id"], month],
            ).fetchone()
            hist = con.execute(
                "select count(*), coalesce(sum(won), 0) from marts.fct_quotes where customer_id = ? and is_decided and decision_date < ?",
                [q["customer_id"], d],
            ).fetchone()
            ci = con.execute(
                "select competitor_index from intermediate.int_competitor_index where region = ? and product_family = ? and month <= ? "
                "and competitor_index is not null order by month desc limit 1",
                [cust[1], prod[2], month],
            ).fetchone()
            nickel = con.execute(
                "select nickel_alloy_index from staging.stg_commodity_index where month <= ? order by month desc limit 1", [month]
            ).fetchone()
            scope = int(q.get("scope_qty", 1))
            list_eur = float(lp[0]) * scope
            net = float(q["proposed_net_price_eur"])
            rows.append(
                {
                    "quote_id": q.get("quote_id") or "new",
                    "customer_id": cust[0],
                    "product_id": prod[0],
                    "service_line": prod[1],
                    "product_family": prod[2],
                    "unit_type": prod[3],
                    "region": cust[1],
                    "segment": cust[2],
                    "strategic_account": bool(cust[3]),
                    "relationship_years": max(0, d.year - int(cust[4])),
                    "quote_date": pd.Timestamp(d),
                    "list_eur": list_eur,
                    "net_eur": net,
                    "cost_eur": float(cost[0]) * scope,
                    "price_ratio": net / list_eur,
                    "competitor_present": bool(q.get("competitor_present", False)),
                    "outage_urgent": bool(q.get("outage_urgent", False)),
                    "competitor_index": float(ci[0]) if ci else None,
                    "prior_decided": int(hist[0]),
                    "prior_won": int(hist[1]),
                    "prior_win_rate": (int(hist[1]) + 2.0) / (int(hist[0]) + 4.0),
                    "nickel_alloy_index": float(nickel[0]),
                }
            )
    finally:
        con.close()
    return pd.DataFrame(rows)
