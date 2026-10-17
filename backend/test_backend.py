"""python -m backend.test_backend  (from the project root; no network or database needed)"""
import datetime as dt

from backend.briefings import level
from backend.lightning import file_start
from backend.nws import describe, hourly, hours_in

assert (hours_in("PT1H"), hours_in("PT6H"), hours_in("P1DT19H"), hours_in("P7D")) == (1, 6, 43, 168)

series = {"values": [{"validTime": "2026-10-17T00:00:00+00:00/PT2H", "value": 30},
                     {"validTime": "2026-10-17T02:00:00+00:00/PT6H", "value": 12.0}]}
t0 = dt.datetime(2026, 10, 17, tzinfo=dt.timezone.utc)
h = hourly(series)
assert h[t0] == h[t0 + dt.timedelta(hours=1)] == 30 and len(h) == 8, h       # values repeat over their interval
rain = hourly(series, split=True)
assert rain[t0 + dt.timedelta(hours=2)] == 2.0 and rain[t0] == 15, rain     # amounts are split across it

heat = {"warn_value": 103, "stop_value": 115, "lower_is_worse": False}
cold = {"warn_value": 32, "stop_value": 15, "lower_is_worse": True}
assert (level(100, heat), level(103, heat), level(120, heat), level(None, heat)) == (None, "warning", "stop_work", None)
assert (level(40, cold), level(30, cold), level(10, cold)) == (None, "warning", "stop_work")

assert describe([{"coverage": "slight_chance", "weather": "thunderstorms"}, {"weather": None}]) == "slight chance thunderstorms"
assert file_start("GLM-L2-LCFA/2026/290/05/OR_GLM-L2-LCFA_G19_s20262900540200_e2026290054040.nc") == \
    dt.datetime(2026, 10, 17, 5, 40, 20, tzinfo=dt.timezone.utc)
print("backend ok")

from backend.notify import chunks
from backend.scenario import forecast_hour

msgs = chunks(["a" * 1000, "b" * 1000, "c" * 10], limit=1500)
assert [len(m) for m in msgs] == [1000, 1011], [len(m) for m in msgs]   # never over the limit, nothing lost
peak = forecast_hour(15)
assert (peak["heat_index_f"], peak["thunder_chance"], forecast_hour(4)["thunder_chance"]) == (112, 70, 10), peak
print("notify + scenario ok")
