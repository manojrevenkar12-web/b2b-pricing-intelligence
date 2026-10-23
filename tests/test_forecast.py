import numpy as np
import pandas as pd

from bpi import warehouse
from bpi.models.forecast import Forecaster, holt_winters


def test_holt_winters_follows_a_seasonal_series():
    t = np.arange(48)
    y = 100 + 2 * t + 20 * np.sin(2 * np.pi * t / 12)
    f = holt_winters(y, 12)
    truth = 100 + 2 * np.arange(48, 60) + 20 * np.sin(2 * np.pi * np.arange(48, 60) / 12)
    assert np.mean(np.abs(f - truth)) / truth.mean() < 0.05


def test_forecast_does_not_see_the_future(home):
    b = warehouse.read("select month, service_line, sum(booked_eur) booked_eur from marts.fct_bookings_monthly group by 1, 2")
    s = warehouse.read("select s.*, q.net_eur from marts.fct_pipeline_snapshots s left join marts.fct_quotes q using (quote_id)")
    origin = pd.Timestamp("2025-07-01")
    a = Forecaster(b, s, {"qualify": 0.1, "propose": 0.3, "negotiate": 0.6, "commit": 0.8}).forecast(origin, [1, 2, 3])
    b2 = b.copy()
    b2.loc[pd.to_datetime(b2["month"]) >= origin, "booked_eur"] *= 10  # change the future
    c = Forecaster(b2, s, {"qualify": 0.1, "propose": 0.3, "negotiate": 0.6, "commit": 0.8}).forecast(origin, [1, 2, 3])
    pd.testing.assert_series_equal(a["forecast"], c["forecast"])
