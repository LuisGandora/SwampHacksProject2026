"""Active fires from NASA FIRMS (VIIRS satellites, a few hours' latency). Needs a free key: set FIRMS_MAP_KEY
(request one at https://firms.modaps.eosdis.nasa.gov/api/area/). Without it this job skips itself.

Detections in the wildfire category's window (24 h) within its warn radius raise a warning; within its stop
radius, stop work.
"""
import csv
import datetime as dt
import io
import os

import httpx

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
client = httpx.Client(timeout=60)

RAISE = """
WITH c AS (SELECT * FROM hazard_categories WHERE category = 'wildfire'),
near AS (
    SELECT DISTINCT ON (s.site_id) s.site_id, f.detected_at, ST_Distance(f.geog, s.geog) / 1609.344 AS miles,
           c.stop_radius_mi, c.window_minutes
      FROM fire_detections f CROSS JOIN c
      JOIN sites s ON ST_DWithin(f.geog, s.geog, c.warn_radius_mi * 1609.344)
     WHERE f.detected_at > now() - make_interval(mins => c.window_minutes)
     ORDER BY s.site_id, ST_Distance(f.geog, s.geog)
)
SELECT count(raise_site_alert(site_id, 'wildfire', CASE WHEN miles <= stop_radius_mi THEN 'stop_work' ELSE 'warning' END,
                              detected_at, round(miles::numeric, 1),
                              'Satellite fire detection ' || round(miles::numeric, 1) || ' mi away',
                              detected_at + make_interval(mins => window_minutes)))
  FROM near
"""


def sync(conn):
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        return "skipped (set FIRMS_MAP_KEY)"
    box = conn.execute("SELECT min(ST_X(geog::geometry)) - 0.3 AS w, min(ST_Y(geog::geometry)) - 0.3 AS s, "
                       "max(ST_X(geog::geometry)) + 0.3 AS e, max(ST_Y(geog::geometry)) + 0.3 AS n FROM sites").fetchone()
    area = f"{box['w']:.3f},{box['s']:.3f},{box['e']:.3f},{box['n']:.3f}"
    since = conn.execute("SELECT coalesce(max(detected_at), now() - interval '2 days') AS t FROM fire_detections"
                         ).fetchone()["t"]
    rows = []
    for source in SOURCES:
        r = client.get(f"{API}/{key}/{source}/{area}/1")
        r.raise_for_status()
        for f in csv.DictReader(io.StringIO(r.text)):
            when = dt.datetime.strptime(f["acq_date"] + f["acq_time"].zfill(4), "%Y-%m-%d%H%M").replace(
                tzinfo=dt.timezone.utc)
            if when > since and f.get("confidence") != "l":  # VIIRS confidence: l(ow) / n(ominal) / h(igh)
                rows.append((when, f"SRID=4326;POINT({f['longitude']} {f['latitude']})", f.get("satellite"),
                             f.get("confidence"), f.get("frp") or None))
    conn.cursor().executemany("INSERT INTO fire_detections (detected_at, geog, satellite, confidence, frp_mw) "
                              "VALUES (%s, %s, %s, %s, %s)", rows)
    return f"{len(rows)} new detections, {conn.execute(RAISE).fetchone()['count']} site alerts raised or extended"
