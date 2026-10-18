"""Next-day safety briefing, generated at 6 PM local time for each site.

Compares tomorrow's NWS forecast with each hazard category's thresholds, lists NWS alerts that overlap
tomorrow, and attaches the latest next-day hazard score when the model has written one. The structured
`content` is what the AI partner's API can turn into friendlier text; `summary` is a plain fallback.
"""
import datetime as dt
import decimal
import json

from psycopg.types.json import Jsonb

from backend import nws, observations

BRIEFING_TIME = dt.time(18, 0)
# The most recent 6 PM in a time zone (SQL, for a timezone column or parameter). AI reports and briefings written
# since then are replayed as they are; the first request after the next 6 PM writes fresh ones.
LAST_6PM = "(((now() AT TIME ZONE {tz})::date - ((now() AT TIME ZONE {tz})::time < '18:00')::int) + time '18:00') AT TIME ZONE {tz}"
WINDOW = dt.timedelta(minutes=15)  # the worker runs every 15 min, so each site is caught once
LIGHTNING_LIKELY_PCT = 50          # chance of thunder at which tomorrow gets a lightning flag

DUE = """
SELECT s.site_id, (now() AT TIME ZONE s.timezone)::date + 1 AS day
  FROM sites s
 WHERE (now() AT TIME ZONE s.timezone)::time >= %(start)s AND (now() AT TIME ZONE s.timezone)::time < %(end)s
   AND NOT EXISTS (SELECT 1 FROM daily_briefings b
                    WHERE b.site_id = s.site_id AND b.briefing_date = (now() AT TIME ZONE s.timezone)::date + 1)
"""

FORECAST = """
SELECT max(heat_index_f) AS extreme_heat, min(coalesce(wind_chill_f, temp_f)) AS extreme_cold,
       sum(precip_in) AS flooding, max(wind_mph) AS high_wind, max(wind_gust_mph) AS max_gust_mph, min(visibility_mi) AS dense_fog,
       max(thunder_chance) AS thunder_chance, max(precip_chance) AS precip_chance,
       max(temp_f) AS high_f, min(temp_f) AS low_f, count(*) AS hours,
       (array_agg(short_forecast ORDER BY valid_at) FILTER (WHERE extract(hour FROM valid_at AT TIME ZONE s.timezone) = 14))[1]
           AS afternoon
  FROM weather_forecasts f JOIN sites s USING (site_id)
 WHERE f.site_id = %(site)s
   AND f.valid_at >= %(day)s::timestamp AT TIME ZONE s.timezone
   AND f.valid_at < (%(day)s::date + 1)::timestamp AT TIME ZONE s.timezone
"""

ALERTS = """
SELECT a.event, c.category, a.headline, a.onset_at, a.expires_at,
       CASE WHEN a.event = ANY (c.nws_stop_events) THEN 'stop_work' ELSE 'warning' END AS level
  FROM nws_alerts a JOIN hazard_categories c ON c.category = a.category, sites s
 WHERE s.site_id = %(site)s
   AND a.expires_at > %(day)s::timestamp AT TIME ZONE s.timezone
   AND coalesce(a.onset_at, a.sent_at) < (%(day)s::date + 1)::timestamp AT TIME ZONE s.timezone
   AND CASE WHEN a.geog IS NOT NULL THEN ST_Covers(a.geog, s.geog)
            ELSE a.ugc_codes && ARRAY[s.forecast_zone, s.county_ugc] END
"""


def level(value, rule):
    """'stop_work', 'warning' or None for a forecast value against a hazard_categories row."""
    if value is None or rule["warn_value"] is None:
        return None
    worse = (lambda v, t: v <= t) if rule["lower_is_worse"] else (lambda v, t: v >= t)
    if rule["stop_value"] is not None and worse(value, rule["stop_value"]):
        return "stop_work"
    return "warning" if worse(value, rule["warn_value"]) else None


def build(conn, site_id, day):
    site = conn.execute("SELECT site_id, name, state, timezone FROM sites WHERE site_id = %s", (site_id,)).fetchone()
    fc = conn.execute(FORECAST, {"site": site_id, "day": day}).fetchone()
    rules = {r["category"]: r for r in conn.execute("SELECT * FROM hazard_categories WHERE metric IS NOT NULL")}
    flags = []
    for category in ("extreme_heat", "extreme_cold", "flooding", "high_wind", "dense_fog"):
        lvl = level(fc[category], rules[category])
        if lvl:
            flags.append({"category": category, "level": lvl, "value": fc[category], "unit": rules[category]["unit"],
                          "label": rules[category]["label"]})
    if (fc["thunder_chance"] or 0) >= LIGHTNING_LIKELY_PCT:
        flags.append({"category": "lightning", "level": "warning", "value": fc["thunder_chance"], "unit": "%",
                      "label": "Lightning likely: expect stop-work pauses (any strike within 10 mi)"})
    alerts = conn.execute(ALERTS, {"site": site_id, "day": day}).fetchall()
    score = conn.execute("SELECT score, category_scores, model_version FROM hazard_scores WHERE site_id = %s "
                         "AND horizon = 'next_day' AND (scored_for AT TIME ZONE %s)::date = %s "
                         "ORDER BY created_at DESC LIMIT 1", (site_id, site["timezone"], day)).fetchone()
    content = {"site": site, "day": day, "forecast": fc, "flags": flags, "nws_alerts": alerts, "hazard_score": score}
    return content, summarize(site, day, fc, flags, alerts)


def summarize(site, day, fc, flags, alerts):
    if not fc["hours"]:
        return f"{site['name']}: no forecast available for {day:%A %b %d} yet."
    parts = [f"{site['name']}, {day:%A %b %d}: high {fc['high_f']:.0f}°F, low {fc['low_f']:.0f}°F"]
    if fc["afternoon"]:
        parts.append(f"afternoon {fc['afternoon']}")
    for f in flags:
        tag = "STOP WORK" if f["level"] == "stop_work" else "Caution"
        parts.append(f"{tag}: {f['label'].lower()} ({f['value']:.0f} {f['unit']})")
    for a in alerts:
        parts.append(f"NWS {a['event']}")
    if not flags and not alerts:
        parts.append("no hazards above thresholds")
    return ". ".join(parts) + "."


def save(conn, site_id, day, content, summary):
    score = content["hazard_score"]["score"] if content["hazard_score"] else None
    conn.execute(
        "INSERT INTO daily_briefings (site_id, briefing_date, send_at, sent_at, hazard_score, summary, content) "
        "SELECT %s, %s, (%s::date - 1 + %s::time)::timestamp AT TIME ZONE timezone, now(), %s, %s, %s FROM sites "
        "WHERE site_id = %s ON CONFLICT (site_id, briefing_date) DO UPDATE SET sent_at = now(), "
        "hazard_score = EXCLUDED.hazard_score, summary = EXCLUDED.summary, content = EXCLUDED.content",
        (site_id, day, day, BRIEFING_TIME, score, summary, Jsonb(content, dumps=to_json), site_id))


def to_json(obj):
    return json.dumps(obj, default=lambda o: float(o) if isinstance(o, decimal.Decimal) else str(o))


# Saved locations take the time zone of their nearest plant (all of FL/GA/AL is Eastern or Central).
LOCATIONS_DUE = """
SELECT l.location_id, (now() AT TIME ZONE tz.timezone)::date + 1 AS day
  FROM saved_locations l
  CROSS JOIN LATERAL (SELECT timezone FROM sites ORDER BY geog <-> l.geog LIMIT 1) tz
 WHERE (now() AT TIME ZONE tz.timezone)::time >= %(start)s AND (now() AT TIME ZONE tz.timezone)::time < %(end)s
   AND NOT EXISTS (SELECT 1 FROM location_briefings b
                    WHERE b.location_id = l.location_id AND b.briefing_date = (now() AT TIME ZONE tz.timezone)::date + 1)
"""
IN_RANGE = """
SELECT s.site_id, s.name, round((ST_Distance(s.geog, l.geog) / 1609.344)::numeric, 1) AS miles,
       h.score, h.category_scores,
       (SELECT count(*) FROM active_site_alerts a WHERE a.site_id = s.site_id AND a.level = 'stop_work') AS stop_work
  FROM saved_locations l JOIN sites s ON ST_DWithin(s.geog, l.geog, l.radius_mi * 1609.344)
  LEFT JOIN LATERAL (SELECT score, category_scores FROM hazard_scores
                      WHERE site_id = s.site_id AND horizon = 'next_day' ORDER BY scored_for DESC, created_at DESC
                      LIMIT 1) h ON true
 WHERE l.location_id = %s
 ORDER BY h.score DESC NULLS LAST
"""


def location_summary(name, radius, day, plants):
    """One-paragraph briefing for a saved location from its in-range plants (highest score first)."""
    if not plants:
        return f"{name}, {day:%a %b %d}: no power plants within {radius:g} mi."
    def top_hazard(p):
        cs = {k: v for k, v in (p["category_scores"] or {}).items() if k != "_meta"}
        k = max(cs, key=lambda k: cs[k]["score"]) if cs else None
        return f", mostly {k.replace('_', ' ')}" if k and cs[k]["score"] >= 1 else ""
    worst = [f"{p['name']} {float(p['score']):.1f}/10{top_hazard(p)}" for p in plants[:3] if p["score"] is not None]
    stops = [p["name"] for p in plants if p["stop_work"]]
    return (f"{name}, {day:%a %b %d}: {len(plants)} plants within {radius:g} mi. "
            + (f"Highest hazard tomorrow: {'; '.join(worst)}. " if worst else "")
            + (f"Stop work now at {', '.join(stops[:3])}{' and more' if len(stops) > 3 else ''}." if stops
               else "No stop-work alerts right now."))


def save_location_briefing(conn, location_id, day):
    loc = conn.execute("SELECT name, radius_mi FROM saved_locations WHERE location_id = %s", (location_id,)).fetchone()
    plants = conn.execute(IN_RANGE, (location_id,)).fetchall()
    summary = location_summary(loc["name"], float(loc["radius_mi"]), day, plants)
    content = {"plants": [{k: p[k] for k in ("site_id", "name", "miles", "score", "stop_work")} for p in plants[:20]]}
    conn.execute("INSERT INTO location_briefings (location_id, briefing_date, summary, content) VALUES (%s, %s, %s, %s) "
                 "ON CONFLICT (location_id, briefing_date) DO UPDATE SET summary = EXCLUDED.summary, "
                 "content = EXCLUDED.content, sent_at = now()",
                 (location_id, day, summary, Jsonb(json.loads(json.dumps(content, default=to_json)))))
    return summary


def run_due(conn):
    """Briefings for every site where it's between 6:00 and 6:15 PM local and tomorrow's isn't written yet."""
    end = (dt.datetime.combine(dt.date.today(), BRIEFING_TIME) + WINDOW).time()
    due = conn.execute(DUE, {"start": BRIEFING_TIME, "end": end}).fetchall()
    if due:  # the weather scanner's 6 PM update: fresh forecast and readings for exactly these sites
        ids = [d["site_id"] for d in due]
        nws.sync_forecasts(conn, ids)
        observations.sync(conn, ids)
    for d in due:
        save(conn, d["site_id"], d["day"], *build(conn, d["site_id"], d["day"]))
    locs = conn.execute(LOCATIONS_DUE, {"start": BRIEFING_TIME, "end": end}).fetchall()
    for l in locs:  # after the sites, so the plants' scores and alerts are fresh
        save_location_briefing(conn, l["location_id"], l["day"])
    return f"{len(due)} site briefings and {len(locs)} saved-location briefings written"
