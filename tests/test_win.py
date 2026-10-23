import numpy as np

from bpi import warehouse
from bpi.models.win import GBM, Logistic, features, split


def test_repricing_changes_only_price_inputs(home):
    q = warehouse.quotes().head(200)
    a, b = features(q), features(q, q["price_ratio"].to_numpy() * 1.05)
    changed = [c for c in a.columns if not a[c].equals(b[c])]
    assert all("log_ratio" in c for c in changed)


def test_split_is_strictly_by_time(home, settings):
    tr, te = split(warehouse.quotes(), settings.train_end, settings.test_end)
    assert tr["decision_date"].max() < te["quote_date"].min() or tr["quote_date"].max() < te["quote_date"].min()
    assert set(tr["quote_id"]).isdisjoint(te["quote_id"])


def test_gbm_never_rewards_a_higher_price(home, settings):
    tr, te = split(warehouse.quotes(), settings.train_end, settings.test_end)
    m = GBM().fit(tr.sample(8000, random_state=0))
    s = te.sample(300, random_state=1)
    grid = [m.predict(s, s["price_ratio"].to_numpy() * k) for k in (0.9, 0.95, 1.0, 1.05)]
    assert all((grid[i + 1] <= grid[i] + 1e-12).all() for i in range(3))


def test_logistic_reports_slopes_per_line(home, settings):
    tr, _ = split(warehouse.quotes(), settings.train_end, settings.test_end)
    s = Logistic().fit(tr).price_slopes()
    assert set(s) == {"spare_parts", "field_service", "repairs", "upgrades", "ltsa"}
    assert all(np.isfinite(v["competitor"]) for v in s.values())
