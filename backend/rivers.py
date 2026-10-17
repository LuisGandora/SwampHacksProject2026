"""River gauges from NOAA's National Water Prediction Service (api.water.noaa.gov/nwps, no key).

One request returns every gauge in the region with its current stage and NOAA's flood category
(action / minor / moderate / major). Sites within the flooding category's radii of a gauge at action stage
or worse get a flood alert; a gauge forecast to flood raises a warning ahead of time.
"""
import datetime as dt

import httpx

NWPS = "https://api.water.noaa.gov/nwps/v1/gauges"
FLOODING = ("action", "minor", "moderate", "major")
client = httpx.Client(timeout=60)

RAISE = """
WITH c AS (SELECT * FROM hazard_categories WHERE category = 'flooding'),
latest AS (
    SELECT DISTINCT ON (gauge_id) * FROM river_gauge_readings
     WHERE observed_at > now() - interval '6 hours' ORDER BY gauge_id, observed_at DESC
), hits AS (
    SELECT s.site_id, g.name, l.flood_category, l.forecast_flood_category, l.stage_ft,
           ST_Distance(g.geog, s.geog) / 1609.344 AS miles, c.stop_radius_mi
      FROM latest l JOIN river_gauges g USING (gauge_id) CROSS JOIN c
      JOIN sites s ON ST_DWithin(g.geog, s.geog, c.warn_radius_mi * 1609.344)
     WHERE l.flood_category = ANY (%(flooding)s) OR l.forecast_flood_category = ANY (%(flooding)s)
)
SELECT count(raise_site_alert(site_id, 'flooding',
                              CASE WHEN flood_category IN ('minor', 'moderate', 'major') AND miles <= stop_radius_mi
                                   THEN 'stop_work' ELSE 'warning' END,
                              now(), stage_ft,
                              CASE WHEN flood_category = ANY (%(flooding)s)
                                   THEN initcap(flood_category) || ' flooding at ' || name
                                   ELSE initcap(forecast_flood_category) || ' flooding forecast at ' || name END
                                  || ' (' || round(miles::numeric, 1) || ' mi away)',
                              now() + interval '2 hours'))
  FROM hits
"""


def sync(conn):
    box = conn.execute("SELECT min(ST_X(geog::geometry)) - 0.5 AS w, min(ST_Y(geog::geometry)) - 0.5 AS s, "
                       "max(ST_X(geog::geometry)) + 0.5 AS e, max(ST_Y(geog::geometry)) + 0.5 AS n FROM sites").fetchone()
    r = client.get(NWPS, params={"bbox.xmin": box["w"], "bbox.ymin": box["s"], "bbox.xmax": box["e"],
                                 "bbox.ymax": box["n"], "srid": "EPSG_4326"})
    r.raise_for_status()
    gauges, readings = [], []
    for g in r.json()["gauges"]:
        gauges.append((g["lid"], g["name"], g["longitude"], g["latitude"]))
        obs, fc = (g.get("status") or {}).get("observed") or {}, (g.get("status") or {}).get("forecast") or {}
        if obs.get("primary", -999) == -999 or obs.get("floodCategory") in ("obs_not_current", "out_of_service"):
            continue
        flow = obs.get("secondary")
        readings.append((g["lid"], dt.datetime.fromisoformat(obs["validTime"].replace("Z", "+00:00")), obs["primary"],
                         flow * 1000 if flow not in (None, -999) and obs.get("secondaryUnit") == "kcfs" else None,
                         obs.get("floodCategory"), fc.get("floodCategory"),
                         fc["primary"] if fc.get("primary", -999) != -999 else None))
    cur = conn.cursor()
    cur.executemany("INSERT INTO river_gauges (gauge_id, name, geog) VALUES (%s, %s, ST_MakePoint(%s, %s)::geography) "
                    "ON CONFLICT (gauge_id) DO UPDATE SET name = EXCLUDED.name, geog = EXCLUDED.geog", gauges)
    cur.executemany("INSERT INTO river_gauge_readings (gauge_id, observed_at, stage_ft, flow_cfs, flood_category, "
                    "forecast_flood_category, forecast_stage_ft) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING", readings)
    raised = conn.execute(RAISE, {"flooding": list(FLOODING)}).fetchone()["count"]
    flooding = sum(r[4] in FLOODING for r in readings)
    return f"{len(readings)} gauge readings ({flooding} at action stage or worse), {raised} site alerts raised or extended"
