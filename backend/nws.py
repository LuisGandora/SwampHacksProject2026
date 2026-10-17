"""National Weather Service API (api.weather.gov): site grid lookups, hourly forecasts and active alerts.

No key needed, but NWS asks every client to send a User-Agent with contact info: set NWS_USER_AGENT.
"""
import datetime as dt
import json
import os
import re
import time

import httpx

API = "https://api.weather.gov"
HEADERS = {"User-Agent": os.environ.get("NWS_USER_AGENT", "PlanIT-Control/0.1 (SwampHacks 2026)"),
           "Accept": "application/geo+json"}
FORECAST_HOURS = 48  # covers tomorrow in full when the 6 PM briefing runs
client = httpx.Client(headers=HEADERS, timeout=30)


def get(path, **params):
    for attempt in range(4):
        r = client.get(API + path, params=params)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:  # NWS gridpoints often 500 briefly
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()


def c_to_f(c):
    return None if c is None else round(c * 9 / 5 + 32, 1)


def kmh_to_mph(v):
    return None if v is None else round(v / 1.609344, 1)


def mm_to_in(v):
    return None if v is None else round(v / 25.4, 3)


def m_to_mi(v):
    return None if v is None else round(v / 1609.344, 2)


def hours_in(duration):
    """ISO-8601 duration like PT1H, PT6H, P1DT19H -> hours."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?)?", duration)
    return int(m.group(1) or 0) * 24 + int(m.group(2) or 0)


def hourly(series, split=False):
    """NWS gridpoint series -> {utc hour: value}. split=True divides amounts (rain) evenly over the interval."""
    out = {}
    for v in (series or {}).get("values", []):
        start, duration = v["validTime"].split("/")
        start, n = dt.datetime.fromisoformat(start), max(hours_in(duration), 1)
        for i in range(n):
            val = v["value"]
            out[start + dt.timedelta(hours=i)] = val / n if split and val is not None else val
    return out


def sync_points(conn):
    """Look up each site's forecast grid and alert zones once (they don't change)."""
    rows = conn.execute("SELECT site_id, ST_Y(geog::geometry) AS lat, ST_X(geog::geometry) AS lon "
                        "FROM sites WHERE nws_grid_id IS NULL").fetchall()
    done = 0
    for r in rows:
        try:
            p = get(f"/points/{r['lat']:.4f},{r['lon']:.4f}")["properties"]
        except httpx.HTTPStatusError as e:
            print(f"  points: site {r['site_id']} skipped ({e.response.status_code})")
            continue
        zone = lambda k: p[k].rsplit("/", 1)[1] if p.get(k) else None  # some coastal points have no county zone
        conn.execute("UPDATE sites SET nws_grid_id = %s, nws_grid_x = %s, nws_grid_y = %s, forecast_zone = %s, "
                     "county_ugc = %s WHERE site_id = %s",
                     (p["gridId"], p["gridX"], p["gridY"], zone("forecastZone"), zone("county"), r["site_id"]))
        done += 1
        if done % 50 == 0:
            conn.commit()
            print(f"  points: {done}/{len(rows)}")
    return f"{done} sites looked up"


def sync_forecasts(conn):
    """Next 48 h of hourly forecasts for every site, one request per NWS grid cell."""
    grids = conn.execute("SELECT nws_grid_id AS g, nws_grid_x AS x, nws_grid_y AS y, array_agg(site_id) AS sites "
                         "FROM sites WHERE nws_grid_id IS NOT NULL GROUP BY 1, 2, 3").fetchall()
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = [now + dt.timedelta(hours=i) for i in range(FORECAST_HOURS)]
    written = 0
    for n, grid in enumerate(grids, 1):
        try:
            p = get(f"/gridpoints/{grid['g']}/{grid['x']},{grid['y']}")["properties"]
        except httpx.HTTPError as e:
            print(f"  forecasts: grid {grid['g']}/{grid['x']},{grid['y']} skipped ({e})")
            continue
        temp, heat, chill = hourly(p.get("temperature")), hourly(p.get("heatIndex")), hourly(p.get("windChill"))
        gust, pop = hourly(p.get("windGust")), hourly(p.get("probabilityOfPrecipitation"))
        rain, thunder = hourly(p.get("quantitativePrecipitation"), split=True), hourly(p.get("probabilityOfThunder"))
        vis, wx = hourly(p.get("visibility")), hourly(p.get("weather"))
        rows = [(site, dt.datetime.fromisoformat(p["updateTime"]), h, c_to_f(temp.get(h)), c_to_f(heat.get(h)),
                 c_to_f(chill.get(h)), pop.get(h), mm_to_in(rain.get(h)), kmh_to_mph(gust.get(h)), thunder.get(h),
                 m_to_mi(vis.get(h)), describe(wx.get(h)))
                for site in grid["sites"] for h in hours if h in temp]
        conn.execute("DELETE FROM weather_forecasts WHERE site_id = ANY(%s) AND valid_at >= %s", (grid["sites"], now))
        conn.cursor().executemany(
            "INSERT INTO weather_forecasts (site_id, issued_at, valid_at, temp_f, heat_index_f, wind_chill_f, "
            "precip_chance, precip_in, wind_gust_mph, thunder_chance, visibility_mi, short_forecast) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", rows)
        conn.commit()
        written += len(rows)
        if n % 50 == 0:
            print(f"  forecasts: {n}/{len(grids)} grids")
    return f"{written} forecast hours for {len(grids)} grids"


def describe(weather):
    """[{'coverage': 'chance', 'weather': 'rain_showers'}, ...] -> 'chance rain showers, slight chance thunderstorms'."""
    return ", ".join(f"{(w.get('coverage') or '').replace('_', ' ')} {w['weather'].replace('_', ' ')}".strip()
                     for w in (weather or []) if w.get("weather")) or None


RAISE_FROM_ALERTS = """
SELECT count(raise_site_alert(s.site_id, a.category,
                              CASE WHEN a.event = ANY (c.nws_stop_events) THEN 'stop_work' ELSE 'warning' END,
                              now(), NULL, a.headline, a.expires_at))
  FROM nws_alerts a
  JOIN hazard_categories c ON c.category = a.category
  JOIN sites s ON CASE WHEN a.geog IS NOT NULL THEN ST_Covers(a.geog, s.geog)     -- storm-based polygon
                       ELSE a.ugc_codes && ARRAY[s.forecast_zone, s.county_ugc] END  -- zone/county alerts
 WHERE a.expires_at > now() AND (a.onset_at IS NULL OR a.onset_at <= now())
"""


def sync_alerts(conn):
    """Store active NWS alerts for the sites' states and raise site alerts for the ones in effect now."""
    states = [r["state"] for r in conn.execute("SELECT DISTINCT state FROM sites")]
    features = get("/alerts/active", area=",".join(states))["features"]
    for f in features:
        p = f["properties"]
        conn.execute(
            "INSERT INTO nws_alerts (alert_id, sent_at, event, category, severity, urgency, onset_at, expires_at, "
            "headline, ugc_codes, geog) VALUES (%s, %s, %s, (SELECT category FROM hazard_categories WHERE "
            "%s = ANY (nws_warn_events || nws_stop_events) LIMIT 1), %s, %s, %s, %s, %s, %s, "
            "ST_GeomFromGeoJSON(%s)::geography) "
            "ON CONFLICT (alert_id, sent_at) DO UPDATE SET expires_at = EXCLUDED.expires_at, headline = EXCLUDED.headline",
            (p["id"], p["sent"], p["event"], p["event"], p.get("severity"), p.get("urgency"), p.get("onset"),
             p.get("ends") or p.get("expires"), p.get("headline"), p["geocode"].get("UGC", []),
             json.dumps(f["geometry"]) if f.get("geometry") else None))
    raised = conn.execute(RAISE_FROM_ALERTS).fetchone()["count"]
    return f"{len(features)} active NWS alerts, {raised} site alerts raised or extended"
