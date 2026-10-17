"""Current conditions: the latest NWS observation at each site's weather station, every 10 minutes.

Raises alerts the moment a reading crosses a category's warn/stop threshold (heat index, wind chill, gusts,
visibility), without waiting for an NWS advisory, plus the flood rule from the brief: heavy rain over the
flooding category's look-back window (default 12 h overnight) raises a flood-risk alert, noting any river
gauge nearby.
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
                                  || ' h' || coalesce(' near ' || g.name, ''),
                              now() + interval '6 hours'))
  FROM total t CROSS JOIN c JOIN sites s USING (site_id)
  LEFT JOIN LATERAL (SELECT name FROM river_gauges r
                      WHERE ST_DWithin(r.geog, s.geog, c.warn_radius_mi * 1609.344)
                      ORDER BY r.geog <-> s.geog LIMIT 1) g ON true
 WHERE t.rain >= c.warn_value
"""


def raise_alerts(conn):
    return conn.execute(RAISE_FROM_READINGS).fetchone()["count"] + conn.execute(RAISE_FROM_RAIN).fetchone()["count"]


def sync(conn):
    stations = conn.execute("SELECT weather_station_id AS station, array_agg(site_id) AS sites FROM sites "
                            "WHERE weather_station_id IS NOT NULL GROUP BY 1").fetchall()
    rows = 0
    for s in stations:
        try:  # NWS station ids are the ICAO code: K + the 3-letter FAA id for the lower 48
            p = get(f"/stations/K{s['station']}/observations/latest")["properties"]
        except httpx.HTTPError as e:
            print(f"  observations: K{s['station']} skipped ({e})")
            continue
        v = lambda k: (p.get(k) or {}).get("value")
        reading = (dt.datetime.fromisoformat(p["timestamp"]), c_to_f(v("temperature")), c_to_f(v("heatIndex")),
                   c_to_f(v("windChill")), c_to_f(v("dewpoint")), v("relativeHumidity"), kmh_to_mph(v("windSpeed")),
                   kmh_to_mph(v("windGust")), mm_to_in(v("precipitationLastHour")), m_to_mi(v("visibility")),
                   f"nws:K{s['station']}")
        conn.cursor().executemany(
            "INSERT INTO weather_observations (site_id, observed_at, temp_f, heat_index_f, wind_chill_f, dewpoint_f, rh, "
            "wind_mph, wind_gust_mph, precip_1h_in, visibility_mi, source) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(site, *reading) for site in s["sites"]])
        rows += len(s["sites"])
    return f"{rows} site readings from {len(stations)} stations, {raise_alerts(conn)} site alerts raised or extended"
