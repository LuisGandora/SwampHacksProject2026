"""NOAA Tides & Currents: live water level at coastal stations vs NOAA's own flood levels -> coastal flood alerts.

Worker job "tides", every 15 min: station details (location, mean higher high water, NOAA minor/moderate/major flood
levels) are refreshed daily; the latest water level per station goes to tide_readings, in station datum (STND), the
datum NOAA's flood levels use. Sites within the tidal_flooding radius of a station at or above its minor flood level
get a warning; moderate or worse, stop work. Keyless API: https://api.tidesandcurrents.noaa.gov
"""
import datetime as dt

import httpx

MD = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
DATA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATES = {"FL", "GA", "AL"}
client = httpx.Client(timeout=30, headers={"User-Agent": "PlanetControl/0.1 (SwampHacks 2026)"})

RAISE = """
WITH c AS (SELECT * FROM hazard_categories WHERE category = 'tidal_flooding'),
latest AS (SELECT DISTINCT ON (station_id) * FROM tide_readings WHERE observed_at > now() - interval '2 hours'
            ORDER BY station_id, observed_at DESC),
hit AS (
    SELECT DISTINCT ON (s.site_id) s.site_id, t.name, l.level_ft, t.minor_ft, t.moderate_ft, l.observed_at,
           ST_Distance(t.geog, s.geog) / 1609.344 AS miles
      FROM latest l JOIN tide_stations t USING (station_id) CROSS JOIN c
      JOIN sites s ON ST_DWithin(t.geog, s.geog, c.warn_radius_mi * 1609.344)
     WHERE t.minor_ft IS NOT NULL AND l.level_ft >= t.minor_ft
     ORDER BY s.site_id, l.level_ft - t.minor_ft DESC
)
SELECT count(raise_site_alert(site_id, 'tidal_flooding',
                              CASE WHEN level_ft >= moderate_ft THEN 'stop_work' ELSE 'warning' END, observed_at, level_ft,
                              'Tidal flooding at ' || name || ' (' || round(miles::numeric, 1) || ' mi): water '
                                  || round(level_ft - minor_ft, 1) || ' ft above NOAA''s minor flood level',
                              observed_at + interval '1 hour'))
  FROM hit
"""


def sync_stations(conn):
    """Station list, mean higher high water and flood levels, once a day."""
    if conn.execute("SELECT 1 FROM tide_stations WHERE updated_at > now() - interval '1 day' LIMIT 1").fetchone():
        return 0
    stations = [s for s in client.get(f"{MD}/stations.json", params={"type": "waterlevels"}).json()["stations"]
                if s.get("state") in STATES]
    rows = []
    for s in stations:
        try:
            fl = client.get(f"{MD}/stations/{s['id']}/floodlevels.json").json()
            datums = {d["name"]: d["value"] for d in client.get(f"{MD}/stations/{s['id']}/datums.json").json().get("datums") or []}
        except (httpx.HTTPError, ValueError):
            continue
        rows.append((s["id"], s["name"], s["state"], s["lng"], s["lat"], datums.get("MHHW"),
                     fl.get("nos_minor"), fl.get("nos_moderate"), fl.get("nos_major")))
    conn.cursor().executemany(
        "INSERT INTO tide_stations (station_id, name, state, geog, mhhw_ft, minor_ft, moderate_ft, major_ft) "
        "VALUES (%s, %s, %s, ST_MakePoint(%s, %s)::geography, %s, %s, %s, %s) ON CONFLICT (station_id) DO UPDATE SET "
        "name = EXCLUDED.name, geog = EXCLUDED.geog, mhhw_ft = EXCLUDED.mhhw_ft, minor_ft = EXCLUDED.minor_ft, "
        "moderate_ft = EXCLUDED.moderate_ft, major_ft = EXCLUDED.major_ft, updated_at = now()", rows)
    return len(rows)


def sync(conn):
    stations = sync_stations(conn)
    readings = 0
    for s in conn.execute("SELECT station_id FROM tide_stations").fetchall():
        try:
            d = client.get(DATA, params={"station": s["station_id"], "product": "water_level", "datum": "STND",
                                         "date": "latest", "units": "english", "time_zone": "gmt", "format": "json",
                                         "application": "PlanetControl"}).json().get("data") or []
        except (httpx.HTTPError, ValueError):
            continue
        if d and d[0].get("v") not in (None, ""):
            at = dt.datetime.strptime(d[0]["t"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
            conn.execute("INSERT INTO tide_readings VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                         (s["station_id"], at, float(d[0]["v"])))
            readings += 1
    conn.execute("DELETE FROM tide_readings WHERE observed_at < now() - interval '30 days'")
    alerts = conn.execute(RAISE).fetchone()["count"]
    return f"{stations} stations refreshed, {readings} water levels, {alerts} tidal flood alerts"
