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

from backend.reports import audit, template

facts_json = '{"score": 7.4, "gust": 35, "site": "Plant A"}'
ok = {"headline": "Caution at Plant A: score 7.4", "summary": "Gusts to 35 mph [S3]", "uncertainty": "",
      "actions": [], "hazards": [{"explanation": "see S1 and S2"}]}
bad = {**ok, "summary": "Gusts to 35 mph, 60% chance of storms"}
assert audit(ok, facts_json) == [] and audit(bad, facts_json) == ["60"], audit(bad, facts_json)  # invented number caught

facts = {"site": {"name": "Plant A"}, "recommendation": {"value": "no_go", "rule": "r"}, "assumptions": ["a"],
         "S2_active_alerts": [{"category": "lightning", "level": "stop_work", "message": "Lightning 3.0 mi away",
                               "title": "STOP WORK: Lightning"}],
         "S1_hazard_scores": {"next_day": {"score": 8.2, "category_scores": {
             "severe_storm": {"score": 7.9, "drivers": [{"label": "rain that day"}]}, "_meta": {}}}}}
r = template(facts)
assert r["headline"] == "NO-GO: Plant A, hazard index 8.2/10" and len(r["hazards"]) == 2, r  # no "tomorrow": the index can be climatology
assert any("30 minutes" in a for a in r["actions"]), r["actions"]  # lightning policy action included
print("reports ok")

import datetime as _dt
import numpy as _np
import pandas as _pd
from ml.hazard_model import combine, nws_event_group, nws_history_days

assert [nws_event_group(e) for e in ("Heat Advisory", "Extreme Heat Warning", "Wind Chill Advisory", "Wind Advisory",
        "Flood Watch", "Tropical Storm Warning", "Red Flag Warning", "Tornado Watch", "Rip Current Statement",
        "Special Weather Statement")] == ["heat", "heat", "cold", "wind", "flood", "tropical", "fire", "convective",
                                          None, None]
# severity weighting: a severe chance moves the index more than the same ordinary chance; zero stays zero
assert combine(_np.array([0.0]), _np.array([0.0]))[0] == 0
assert combine(_np.array([0.2]), _np.array([0.05]))[0] > combine(_np.array([0.25]), _np.array([0.0]))[0]

# an alert counts for day D only if issued before 6 PM local on D-1
import tempfile, os, ml.hazard_model as hm
tmp = tempfile.TemporaryDirectory()
_pd.DataFrame([{"group": "heat", "phenomena": "HT", "significance": "Y", "ugc": "FLZ045",
                "issued_utc": "2024-07-10 20:00", "expires_utc": "2024-07-12 23:00"},   # 4 PM EDT Jul 10
               {"group": "wind", "phenomena": "WI", "significance": "Y", "ugc": "FLZ045",
                "issued_utc": "2024-07-11 00:00", "expires_utc": "2024-07-11 23:00"}]  # 8 PM EDT Jul 10: too late
              ).to_csv(os.path.join(tmp.name, "nws_warning_history.csv"), index=False)
old, hm.DATA = hm.DATA, __import__("pathlib").Path(tmp.name)
days = nws_history_days(_pd.DataFrame([{"site_id": 1, "forecast_zone": "FLZ045", "county_ugc": None,
                                        "timezone": "America/New_York"}]))
hm.DATA = old
assert days == {(1, _dt.date(2024, 7, 11)): {"heat"}, (1, _dt.date(2024, 7, 12)): {"heat"}}, days
print("model logic ok")

# sign-in: only tokens signed by the tenant's key, for our audience, unexpired, are accepted
import time as _time
import jwt as _jwt
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
from fastapi import HTTPException as _HTTPException
import backend.auth as auth

os.environ.update(AUTH0_DOMAIN="test.auth0.com", AUTH0_AUDIENCE="https://api.test")
_key, _other = (_rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))
auth._jwks["test.auth0.com"] = type("FakeJwks", (), {"get_signing_key_from_jwt": lambda self, t:
                                                     type("K", (), {"key": _key.public_key()})()})()
def _token(key=_key, **over):
    claims = {"sub": "auth0|1", "iss": "https://test.auth0.com/", "aud": "https://api.test",
              "iat": int(_time.time()), "exp": int(_time.time()) + 600, **over}
    return _jwt.encode(claims, key, algorithm="RS256")
assert auth.verify(_token())["sub"] == "auth0|1"
for bad in (_token(key=_other), _token(aud="https://other"), _token(exp=int(_time.time()) - 120),
            _token(iss="https://evil.auth0.com/"), _jwt.encode({"sub": "x"}, "s" * 32, algorithm="HS256")):
    try:
        auth.verify(bad)
        raise AssertionError("accepted a bad token")
    except _HTTPException as e:
        assert e.status_code == 401
print("auth ok")

from backend.notify import webhook_kind, wants
assert webhook_kind("https://discord.com/api/webhooks/1/abc") == "discord"
assert webhook_kind("https://hooks.slack.com/services/T/B/x") == "slack"
assert not any(webhook_kind(u) for u in ("http://discord.com/api/webhooks/1/abc", "https://discord.com.evil.io/api/webhooks/",
                                         "https://169.254.169.254/latest", "https://hooks.slack.com/services/a b", None))
ch = {"site_ids": [7], "levels": ["stop_work"], "briefings": False, "crew_reports": True}
assert [wants(ch, i) for i in [("a", 7, "alert", "stop_work", ""), ("a", 7, "alert", "warning", ""),
                               ("a", 8, "alert", "stop_work", ""), ("r", 7, "report", None, ""),
                               ("b", 7, "briefing", None, "")]] == [True, False, False, True, False]
print("notify channels ok")

# contract evidence: storm words must match whole words ("notice" isn't ice, "drainage" isn't rain, "window" isn't wind)
import re as _re
from ml.contract_disruption import CITED_KINDS
_kinds = lambda t: set().union(*[k for pat, k in CITED_KINDS if _re.search(pat, t)])
assert _kinds("hurricane ian impacts") == {"tropical"} and "flood" in _kinds("rea 0002 - flooding delays 2021")
assert _kinds("notice to proceed: drainage, window and training") == set()
print("contract evidence ok")

# bounceback: a county-specific September spike (fiscal year end) must not survive the seasonal adjustment
from ml.bounceback import seasonal_residual
_months = _pd.period_range("2020-01", "2022-12", freq="M")
_idx = _pd.MultiIndex.from_product([["A", "B"], _months], names=["county_fips", "month"])
_y = _pd.Series([(5.0 if c == "A" else 1.0) * (m.month == 9) for c, m in _idx], index=_idx)  # A spikes 5x harder
assert seasonal_residual(_y).abs().max() < 1e-9
print("bounceback seasonal adjustment ok")
