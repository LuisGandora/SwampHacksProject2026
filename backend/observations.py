"""Current conditions: the latest NWS observation at each site's weather station, every 10 minutes.

Raises alerts the moment a reading crosses a category's warn/stop threshold (heat index, wind chill, gusts,
visibility), without waiting for an NWS advisory, plus the flood rule from the brief: heavy rain over the
flooding category's look-back window (default 12 h overnight) raises a flood-risk alert, noting any river
gauge nearby. Sites inside a FEMA Special Flood Hazard Area (scraper/fema_flood_zones.py) are warned at 75% of the
rain threshold, and the alert names their flood zone.
"""
import datetime as dt

import httpx

from backend.nws import c_to_f, get, kmh_to_mph, m_to_mi, mm_to_in

READING_VALID = "60 minutes"  # an alert from a single reading lasts this long unless a new reading renews it

RAISE_FROM_READINGS = f"""
WITH latest AS (
    SELECT DISTINCT ON (site_id) * FROM weather_observations
     WHERE observed_at > now() - interval '90 minutes' ORDER BY site_id, observed_at DESC
), v AS (
    SELECT site_id, observed_at, 'extreme_heat'::text AS category, heat_index_f AS value FROM latest
    UNION ALL SELECT site_id, observed_at, 'extreme_cold', coalesce(wind_chill_f, temp_f) FROM latest
    UNION ALL SELECT site_id, observed_at, 'high_wind', wind_mph FROM latest   -- sustained
    UNION ALL SELECT site_id, observed_at, 'dense_fog', visibility_mi FROM latest
), lv AS (
    SELECT v.*, c.label, c.unit,
           CASE WHEN (NOT c.lower_is_worse AND v.value >= c.stop_value) OR (c.lower_is_worse AND v.value <= c.stop_value)
                THEN 'stop_work'
                WHEN (NOT c.lower_is_worse AND v.value >= c.warn_value) OR (c.lower_is_worse AND v.value <= c.warn_value)
                THEN 'warning' END AS level
      FROM v JOIN hazard_categories c USING (category)
)
SELECT count(raise_site_alert(site_id, category, level, observed_at, value,
                              label || ' now ' || round(value) || ' ' || unit, observed_at + interval '{READING_VALID}'))
  FROM lv WHERE level IS NOT NULL
"""

# Rain over the flooding window: one reading per hour (stations repeat the last-hour total between reports).
RAISE_FROM_RAIN = """
WITH c AS (SELECT * FROM hazard_categories WHERE category = 'flooding'),
hourly AS (
    SELECT site_id, date_trunc('hour', observed_at) AS hour, max(precip_1h_in) AS rain
      FROM weather_observations, c
     WHERE observed_at > now() - make_interval(mins => c.window_minutes) AND precip_1h_in IS NOT NULL
     GROUP BY 1, 2
), total AS (SELECT site_id, sum(rain) AS rain FROM hourly GROUP BY site_id)
SELECT count(raise_site_alert(t.site_id, 'flooding',
                              CASE WHEN t.rain >= c.stop_value THEN 'stop_work' ELSE 'warning' END, now(), t.rain,
                              'Flood risk: ' || round(t.rain, 1) || ' in of rain in the last ' || c.window_minutes / 60
                                  || ' h' || coalesce(' near ' || g.name, '')
                                  || CASE WHEN s.fema_sfha THEN '; site is in FEMA flood zone ' || s.fema_flood_zone
                                          || ' (1%-a-year floodplain)' ELSE '' END,
                              now() + interval '6 hours'))
  FROM total t CROSS JOIN c JOIN sites s USING (site_id)
  LEFT JOIN LATERAL (SELECT name FROM river_gauges r
                      WHERE ST_DWithin(r.geog, s.geog, c.warn_radius_mi * 1609.344)
                      ORDER BY r.geog <-> s.geog LIMIT 1) g ON true
 WHERE t.rain >= c.warn_value * CASE WHEN s.fema_sfha THEN 0.75 ELSE 1 END  -- floodplain sites: warned sooner
"""


# Soil saturation: a week of soaking rain (soft ground, stuck vehicles, unstable trench walls). Same hourly rain as the
# flood rule, over the soil_saturation window (7 days), thresholds from hazard_categories; open for 24 h, re-raised
# while it stays wet.
RAISE_FROM_WEEK_RAIN = """
WITH c AS (SELECT * FROM hazard_categories WHERE category = 'soil_saturation'),
hourly AS (
    SELECT site_id, date_trunc('hour', observed_at) AS hour, max(precip_1h_in) AS rain
      FROM weather_observations, c
     WHERE observed_at > now() - make_interval(mins => c.window_minutes) AND precip_1h_in IS NOT NULL
     GROUP BY 1, 2
), total AS (SELECT site_id, sum(rain) AS rain FROM hourly GROUP BY site_id)
SELECT count(raise_site_alert(t.site_id, 'soil_saturation',
                              CASE WHEN t.rain >= c.stop_value THEN 'stop_work' ELSE 'warning' END, now(), t.rain,
                              round(t.rain, 1) || ' in of rain in the last 7 days: soft ground, vehicles may get stuck, '
                                  || 'check trench walls', now() + interval '24 hours'))
  FROM total t CROSS JOIN c
 WHERE t.rain >= c.warn_value
"""


def raise_alerts(conn):
    return sum(conn.execute(q).fetchone()["count"] for q in (RAISE_FROM_READINGS, RAISE_FROM_RAIN, RAISE_FROM_WEEK_RAIN))


def reading(p, station):
    v = lambda k: (p.get(k) or {}).get("value")
    return (dt.datetime.fromisoformat(p["timestamp"]), c_to_f(v("temperature")), c_to_f(v("heatIndex")),
            c_to_f(v("windChill")), c_to_f(v("dewpoint")), v("relativeHumidity"), kmh_to_mph(v("windSpeed")),
            kmh_to_mph(v("windGust")), mm_to_in(v("precipitationLastHour")), m_to_mi(v("visibility")), f"nws:K{station}")


# Sites whose own station hasn't reported for 2 hours (e.g. Navy airfields NSE/NFJ close overnight), with the 5 nearest
# other stations within 60 mi to fall back to.
SILENT = """
SELECT s.site_id, s.weather_station_id AS own,
       ARRAY(SELECT w.station_id FROM weather_stations w
              WHERE w.station_id <> s.weather_station_id AND ST_DWithin(w.geog, s.geog, 60 * 1609.344)
              ORDER BY w.geog <-> s.geog LIMIT 5) AS nearby
  FROM sites s
 WHERE s.weather_station_id IS NOT NULL AND (%(ids)s::bigint[] IS NULL OR s.site_id = ANY (%(ids)s))
   AND NOT EXISTS (SELECT 1 FROM weather_observations o
                    WHERE o.site_id = s.site_id AND o.observed_at > now() - interval '2 hours')
"""
INSERT = ("INSERT INTO weather_observations (site_id, observed_at, temp_f, heat_index_f, wind_chill_f, dewpoint_f, rh, "
          "wind_mph, wind_gust_mph, precip_1h_in, visibility_mi, source) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING")


def fallback(conn, site_ids, latest):
    """Readings for sites whose station has gone quiet, from the nearest station still reporting. The source says
    which station it came from, e.g. "nws:KPNS (KNSE not reporting)". Returns how many sites were covered."""
    def fresh(station):
        if station not in latest:
            try:
                latest[station] = get(f"/stations/K{station}/observations/latest")["properties"]
            except httpx.HTTPError:
                latest[station] = None
        p = latest[station]
        return p if p and p.get("timestamp") and \
            dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(p["timestamp"]) < dt.timedelta(hours=2) else None
    covered = 0
    for site in conn.execute(SILENT, {"ids": site_ids}).fetchall():
        alt = next((st for st in site["nearby"] if fresh(st)), None)
        if alt:
            r = reading(latest[alt], alt)
            conn.execute(INSERT, (site["site_id"], *r[:-1], f"nws:K{alt} (K{site['own']} not reporting)"))
            covered += 1
    return covered


def sync(conn, site_ids=None, history_days=0):
    """Latest reading at each site's station (or just site_ids). history_days > 0 also back-fills that many days
    (NWS keeps about 7), so a site opened after the worker was off still shows its week."""
    stations = conn.execute("SELECT weather_station_id AS station, array_agg(site_id) AS sites FROM sites "
                            "WHERE weather_station_id IS NOT NULL AND (%s::bigint[] IS NULL OR site_id = ANY(%s)) "
                            "GROUP BY 1", (site_ids, site_ids)).fetchall()
    rows, latest = 0, {}
    for s in stations:
        try:  # NWS station ids are the ICAO code: K + the 3-letter FAA id for the lower 48
            if history_days:  # NWS returns at most 500 readings per call (~40 h at busy stations): one call per day
                now = dt.datetime.now(dt.timezone.utc)
                props = [f["properties"] for d in range(history_days, 0, -1)
                         for f in get(f"/stations/K{s['station']}/observations",
                                      start=(now - dt.timedelta(days=d)).isoformat(timespec="seconds"),
                                      end=(now - dt.timedelta(days=d - 1)).isoformat(timespec="seconds"))["features"]]
            else:
                props = [get(f"/stations/K{s['station']}/observations/latest")["properties"]]
                latest[s["station"]] = props[0]
        except httpx.HTTPError as e:
            print(f"  observations: K{s['station']} skipped ({e})")
            continue
        readings = [reading(p, s["station"]) for p in props if p.get("timestamp")]
        conn.cursor().executemany(INSERT, [(site, *r) for site in s["sites"] for r in readings])
        rows += len(s["sites"]) * len(readings)
    covered = 0 if history_days else fallback(conn, site_ids, latest)
    return (f"{rows} site readings from {len(stations)} stations, {covered} sites from a nearby station (theirs not "
            f"reporting), {raise_alerts(conn)} site alerts raised or extended")
