import numpy as np
import pandas as pd

from bpi.config import Guardrails
from bpi.models.price_response import PriceResponse
from bpi.models.recommend import bounds, policy_ratios, recommend

G = Guardrails(
    min_margin={"spare_parts": 0.25, "field_service": 0.2, "repairs": 0.2, "upgrades": 0.15, "ltsa": 0.1},
    max_move_vs_quote=0.10,
    max_above_list=0.0,
    deal_desk_lines=("ltsa",),
)


def quotes():
    return pd.DataFrame(
        {
            "quote_id": ["a", "b", "c", "d", "e"],
            "service_line": ["spare_parts", "spare_parts", "repairs", "ltsa", "spare_parts"],
            "price_ratio": [0.80, 0.97, 0.85, 0.90, 0.60],
            "list_eur": [100.0, 100.0, 100.0, 100.0, 100.0],
            "cost_eur": [50.0, 50.0, 50.0, 60.0, 50.0],
            "competitor_present": [False, True, False, False, False],
        }
    )


def response(slope):
    return PriceResponse(lambda g, r: np.full(len(g), 0.6), {"spare_parts": {"no_competitor": slope, "competitor": slope}})


def test_inelastic_line_is_priced_up_to_the_limit_not_beyond():
    q = quotes()
    r, status = policy_ratios(q, response(-0.5), G)
    assert np.isclose(r[0], 0.80 * 1.10)  # max move
    assert np.isclose(r[1], 1.0)  # list cap
    assert status[2] == "hold_unvalidated" and r[2] == 0.85
    assert status[3] == "deal_desk" and r[3] == 0.90


def test_elastic_line_never_goes_below_the_floor():
    q = quotes()
    r, _ = policy_ratios(q, response(-40.0), G)
    lo, _hi, fl = bounds(q, G)
    assert (r[[0, 1, 4]] >= lo[[0, 1, 4]] - 1e-12).all()
    # Rep's price below the floor: recommendation moves towards it as far as allowed.
    assert np.isclose(r[4], 0.60 * 1.10) and fl[4] > r[4]


def test_recommendation_explains_itself():
    recs = recommend(quotes(), response(-0.5), G)
    by = {r.quote_id: r for r in recs}
    assert by["c"].status == "hold_unvalidated" and "no price test" in by["c"].reasons[0]
    assert by["d"].status == "deal_desk"
    assert by["e"].status == "rep_below_floor"
    assert any("maximum move" in x for x in by["a"].reasons)
    assert by["a"].low_ratio <= by["a"].recommended_ratio <= by["a"].high_ratio
