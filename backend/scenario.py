"""Demo scenario: put one site into the brief's opening story, whatever the real weather is.

August morning in the swamp: heat index 108°F now and 112°F tomorrow, thunderstorms building (a strike 4 mi
west -> stop work with the 30-min all-clear), a week of soaking rain, and a gator reported next to the jobsite.
Everything goes through the same tables and alert functions as live data. stop() removes it all again.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from backend import briefings, lightning, observations

MILE = 1609.344


def forecast_hour(local_hour):
    """Hot, humid day with afternoon storms (one row of the fake forecast)."""
    temp = 80 + max(0, 14 - abs(local_hour - 15) * 2)
    storms = 14 <= local_hour <= 19
    return {"temp_f": temp, "heat_index_f": temp + (18 if 11 <= local_hour <= 18 else 6),
            "thunder_chance": 70 if storms else 10, "precip_chance": 70 if storms else 10,
            "precip_in": 0.4 if 15 <= local_hour <= 17 else 0, "wind_gust_mph": 35 if local_hour == 16 else 12,
            "wind_mph": 22 if local_hour == 16 else 8,
            "short_forecast": "demo: chance thunderstorms" if storms else "demo: mostly sunny"}


def start(conn, site_id):
    site = conn.execute("SELECT site_id, name, timezone FROM sites WHERE site_id = %s", (site_id,)).fetchone()
    if not site:
        return None
    stop(conn, site_id)  # start clean if a previous demo is still there
    now = conn.execute("SELECT now() AS t").fetchone()["t"]
    tz = ZoneInfo(site["timezone"])

    # 1. Right now: heat index 108 F at the site
    conn.execute("INSERT INTO weather_observations (site_id, observed_at, temp_f, heat_index_f, dewpoint_f, rh, wind_mph, "
                 "wind_gust_mph, precip_1h_in, visibility_mi, source) VALUES (%s, %s, 94, 108, 78, 62, 8, 18, 0, 10, "
                 "'demo')", (site_id, now))
    observations.raise_alerts(conn)

    # 2. Lightning 4 mi west -> stop work, 30-minute all-clear
    conn.execute("INSERT INTO lightning_strikes (observed_at, geog, source) "
                 "SELECT %s, ST_Project(geog, 4 * %s, radians(270)), 'demo' FROM sites WHERE site_id = %s",
                 (now, MILE, site_id))
    lightning.raise_alerts(conn, now - dt.timedelta(seconds=1))

    # 3. A week of rain -> soft ground
    conn.execute("SELECT raise_site_alert(%s, 'soil_saturation', 'warning', %s, 6.2, "
                 "'[demo] 6.2 in of rain in the last 7 days: soft ground, vehicles may get stuck', %s)",
                 (site_id, now, now + dt.timedelta(days=1)))

    # 4. Gator reported by the crew next to the jobsite (photo from a real recent iNaturalist sighting, if any)
    photo = conn.execute("SELECT photo_url FROM wildlife_sightings WHERE species = 'Alligator mississippiensis' "
                         "AND photo_url IS NOT NULL ORDER BY observed_at DESC LIMIT 1").fetchone()
    photo = photo["photo_url"] if photo else None
    conn.execute("INSERT INTO crew_reports (site_id, category, description, photo_url, geog, reported_by, severity) "
                 "SELECT %s, 'wildlife', '[demo] Alligator in the swamp next to the jobsite', %s, "
                 "ST_Project(geog, 0.15 * %s, radians(90)), 'demo', 3 FROM sites WHERE site_id = %s",
                 (site_id, photo, MILE, site_id))
    conn.execute("SELECT raise_site_alert(%s, 'wildlife', 'warning', %s, NULL, "
                 "'[demo] Crew report: alligator in the swamp next to the jobsite', %s, %s)",
                 (site_id, now, now + dt.timedelta(hours=12), photo))

    # 5. Tomorrow: heat index up to 112 F, afternoon thunderstorms
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    rows = []
    for i in range(48):
        h = hour0 + dt.timedelta(hours=i)
        f = forecast_hour(h.astimezone(tz).hour)
        rows.append((site_id, now, h, f["temp_f"], f["heat_index_f"], f["precip_chance"], f["precip_in"],
                     f["wind_gust_mph"], f["thunder_chance"], 10, f["short_forecast"], f["wind_mph"]))
    conn.execute("DELETE FROM weather_forecasts WHERE site_id = %s AND valid_at >= %s", (site_id, hour0))
    conn.cursor().executemany(
        "INSERT INTO weather_forecasts (site_id, issued_at, valid_at, temp_f, heat_index_f, precip_chance, precip_in, "
        "wind_gust_mph, thunder_chance, visibility_mi, short_forecast, wind_mph) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        rows)

    # 6. The 6 PM briefing for tomorrow, written now
    day = now.astimezone(tz).date() + dt.timedelta(days=1)
    content, summary = briefings.build(conn, site_id, day)
    briefings.save(conn, site_id, day, {**content, "demo": True}, summary)
    return {"site": site["name"], "briefing_date": day, "summary": summary}


def stop(conn, site_id):
    started = conn.execute("SELECT min(observed_at) AS t FROM weather_observations WHERE site_id = %s AND source = 'demo'",
                           (site_id,)).fetchone()["t"]
    conn.execute("DELETE FROM weather_observations WHERE site_id = %s AND source = 'demo'", (site_id,))
    conn.execute("DELETE FROM lightning_strikes WHERE source IN ('demo', 'dev')")  # dev = fake strikes from the demo
    conn.execute("DELETE FROM weather_forecasts WHERE site_id = %s AND short_forecast LIKE 'demo:%%'", (site_id,))
    conn.execute("DELETE FROM crew_reports WHERE site_id = %s AND reported_by = 'demo'", (site_id,))
    conn.execute("DELETE FROM daily_briefings WHERE site_id = %s AND content ->> 'demo' = 'true'", (site_id,))
    if started:  # alerts the demo raised for this site, and the lightning alerts its strikes raised at neighbors
        conn.execute("DELETE FROM site_alerts WHERE site_id = %s AND started_at >= %s", (site_id, started))
        conn.execute("DELETE FROM site_alerts a USING sites n, sites d WHERE a.site_id = n.site_id AND d.site_id = %s "
                     "AND a.category = 'lightning' AND a.started_at >= %s AND ST_DWithin(n.geog, d.geog, 25 * %s)",
                     (site_id, started, MILE))  # real strikes re-raise theirs on the next lightning run
    # real alerts the demo extended now carry demo text; drop them and the live jobs re-raise the real ones
    conn.execute("DELETE FROM site_alerts WHERE site_id = %s AND message LIKE '[demo]%%'", (site_id,))
    return {"removed": True}
