"""Supervisor plan: where to put attention and resources first, at three horizons.

  now     sites needing action today: active stop-work and warnings first, then the highest live scores; for each,
          why, what to do (reports.ACTIONS) and what to bring (RESOURCES)
  week    the next 7 days of NWS forecast per site: heat, storm, heavy-rain and wind days, plus what to schedule or
          stage ahead (floodplain and levee sites flagged before heavy rain)
  invest  sites ranked by crew time and money lost to weather in a typical year (5 years of stop-work days at the
          site x hours lost per hazard x crew size x the state's BLS labor rate), with a fix for the costliest
          hazard, its cost and payback. Fix costs and how much they avoid are labeled ASSUMPTION rows in
          economic_assumptions until a utility's real numbers replace them.
  economics  region-wide money view: crew-hours and dollars at risk this week (7-day forecast), yearly weather loss
          by utility, weather measured in the utilities' real sales (ml/weather_sales.py), real crew dispatches
          from outage maps (backend/crew_dispatch.py), the experiments' findings, and every assumption with the
          stop-work hours actually measured by our own alerts next to the assumed ones.

At-risk crew time for a forecast day: each flagged hazard costs hours_lost_<category> (capped at one shift) x crew
size x the state's labor rate, as if a crew were working at every flagged site that day.
"""
import datetime as dt
import json
from pathlib import Path

from backend.reports import ACTIONS

RESOURCES = {
    "lightning": "Lightning alert app or detector; hard-roof vehicles or a shelter on site",
    "extreme_heat": "Water and electrolytes, shade tents, a cooling station; start shifts early",
    "extreme_cold": "Heated warm-up shelter; de-icing for walkways and equipment",
    "flooding": "Pumps on standby; equipment moved to high ground; check access roads",
    "tidal_flooding": "Equipment off low ground before high tide; flood barriers; check tide times",
    "soil_saturation": "Crane or swamp mats, tracked equipment, gravel for access",
    "severe_storm": "Tie-downs for loose material; storm shelter plan; secure laydown yard",
    "tropical": "Storm plan: demobilize, secure the site, sandbags",
    "high_wind": "Ground aerial lifts and crane picks; anemometer on site",
    "wildfire": "Fire watch, clear evacuation route, respirators for smoke",
    "wildlife": "Wildlife spotter, snake gaiters; keep clear of water edges",
    "dense_fog": "Lighting and spotters; delay equipment moves",
}
NOW = """
WITH latest AS (SELECT DISTINCT ON (site_id) site_id, score FROM hazard_scores  -- the daily model, not the
                 WHERE horizon = 'now' AND model_version NOT LIKE 'climatology%%'   -- fixed long-term index
                 ORDER BY site_id, scored_for DESC, created_at DESC)
SELECT s.site_id, s.name, s.state, s.county, greatest(h.score, f.floor) AS score, f.reason,
       coalesce(json_agg(json_build_object('category', a.category, 'level', a.level, 'title', a.title,
                                           'message', a.message) ORDER BY a.level DESC)
                FILTER (WHERE a.alert_id IS NOT NULL), '[]') AS alerts,
       count(a.alert_id) FILTER (WHERE a.level = 'stop_work') AS stop_work
  FROM sites s
  LEFT JOIN latest h USING (site_id)
  LEFT JOIN site_live_floor f USING (site_id)
  LEFT JOIN active_site_alerts a USING (site_id)
 GROUP BY s.site_id, s.name, s.state, s.county, h.score, f.floor, f.reason
HAVING count(a.alert_id) > 0 OR greatest(h.score, f.floor) >= 5
 ORDER BY stop_work DESC, score DESC NULLS LAST
 LIMIT %s
"""
WEEK = """
WITH c AS (SELECT (SELECT warn_value FROM hazard_categories WHERE category = 'extreme_heat') AS heat,
                  (SELECT warn_value FROM hazard_categories WHERE category = 'high_wind') AS wind),
d AS (
    SELECT f.site_id, (f.valid_at AT TIME ZONE s.timezone)::date AS day,
           max(coalesce(f.heat_index_f, f.temp_f)) AS heat_f, sum(coalesce(f.precip_in, 0)) AS rain_in,
           max(f.thunder_chance) AS thunder, max(f.wind_mph) AS wind_mph
      FROM weather_forecasts f JOIN sites s USING (site_id)
     WHERE f.valid_at >= date_trunc('day', now()) AND f.valid_at < now() + interval '7 days'
     GROUP BY 1, 2
)
SELECT d.site_id, s.name, s.state, s.county, s.fema_sfha, (l.leveed_area_system IS NOT NULL) AS behind_levee,
       json_agg(json_build_object('day', d.day, 'heat', coalesce(d.heat_f >= c.heat, false),
                                  'storm', coalesce(d.thunder >= 50, false), 'rain', d.rain_in >= 1,
                                  'wind', coalesce(d.wind_mph >= c.wind, false),
                                  'heat_f', round(d.heat_f), 'rain_in', round(d.rain_in::numeric, 2),
                                  'thunder', d.thunder) ORDER BY d.day) AS days
  FROM d CROSS JOIN c JOIN sites s USING (site_id) LEFT JOIN site_levees l USING (site_id)
 GROUP BY d.site_id, s.name, s.state, s.county, s.fema_sfha, l.leveed_area_system
"""
INVEST = """
WITH span AS (SELECT greatest((max(day) - min(day)) / 365.25, 1) AS years FROM site_weather_days),
stops AS (SELECT site_id, category, count(*) FILTER (WHERE level = 'stop_work') AS stop_days
            FROM site_weather_days GROUP BY 1, 2),
clim AS (SELECT DISTINCT ON (site_id) site_id, score FROM hazard_scores WHERE model_version LIKE 'climatology%%'
          ORDER BY site_id, scored_for DESC)
SELECT s.site_id, s.name, s.state, s.county, s.utility_name, s.fema_sfha, s.fema_flood_zone, cl.score AS long_term_exposure,
       l.leveed_area_system, l.levee_accreditation, l.overtopping_chance_per_year,
       (SELECT count(*) FROM crew_reports r WHERE r.site_id = s.site_id AND r.reported_at > now() - interval '1 year') AS crew_reports_1y,
       json_object_agg(st.category, round((st.stop_days / span.years)::numeric, 1))
           FILTER (WHERE st.category IS NOT NULL AND st.stop_days > 0) AS stop_days_per_year
  FROM sites s CROSS JOIN span
  LEFT JOIN stops st USING (site_id)
  LEFT JOIN clim cl USING (site_id)
  LEFT JOIN site_levees l USING (site_id)
 GROUP BY s.site_id, s.name, s.state, s.county, s.utility_name, s.fema_sfha, s.fema_flood_zone, cl.score, l.leveed_area_system,
          l.levee_accreditation, l.overtopping_chance_per_year
"""


FLAG_CATEGORY = {"heat": "extreme_heat", "storm": "lightning", "rain": "flooding", "wind": "high_wind"}


def assumptions(conn):
    return {r["key"]: float(r["value"]) for r in conn.execute("SELECT key, value FROM economic_assumptions").fetchall()}


def rate(a, state):
    return a.get(f"loaded_cost_per_hour_{state}", a.get("loaded_cost_per_hour", 60))


def at_risk(day, state, a):
    """(crew-hours, USD) a forecast day's flagged hazards put at risk at one site."""
    hours = min(sum(a.get(f"hours_lost_{FLAG_CATEGORY[k]}", 0) for k in FLAG_CATEGORY if day.get(k)),
                a.get("shift_hours", 10)) * a.get("crew_size", 4)
    return round(hours, 1), round(hours * rate(a, state))


def now(conn, limit=30, only=None):
    out = []
    for r in conn.execute(NOW, (700,)).fetchall():
        if only and r["site_id"] not in only:
            continue
        cats = list(dict.fromkeys(a["category"] for a in r["alerts"]))
        out.append({**r, "actions": [ACTIONS[c] for c in cats if c in ACTIONS],
                    "resources": [RESOURCES[c] for c in cats if c in RESOURCES]})
    return out[:limit]


def week(conn, only=None):
    a = assumptions(conn)
    sites = [s for s in conn.execute(WEEK).fetchall() if not only or s["site_id"] in only]
    for s in sites:
        s["counts"] = {k: sum(d[k] for d in s["days"]) for k in ("heat", "storm", "rain", "wind")}
        for d in s["days"]:
            d["crew_hours"], d["usd"] = at_risk(d, s["state"], a)
        s["crew_hours"], s["usd"] = round(sum(d["crew_hours"] for d in s["days"]), 1), sum(d["usd"] for d in s["days"])
    names = lambda rows: [r["name"] for r in rows]
    hot = [s for s in sites if s["counts"]["heat"] >= 3]
    stormy = [s for s in sites if s["counts"]["storm"] >= 2]
    wet_low = [s for s in sites if s["counts"]["rain"] and (s["fema_sfha"] or s["behind_levee"])]
    windy = [s for s in sites if s["counts"]["wind"]]
    first_rain = lambda s: dt.date.fromisoformat(next(d["day"] for d in s["days"] if d["rain"]))
    suggestions = [x for x in [
        hot and {"hazard": "extreme_heat", "sites": names(hot),
                 "text": f"{len(hot)} sites have 3+ days with heat index over the warning level: start shifts early, "
                         "stage water, shade and a cooling station."},
        stormy and {"hazard": "lightning", "sites": names(stormy),
                    "text": f"{len(stormy)} sites have 2+ days with a 50%+ thunder chance: schedule outdoor work for "
                            "the morning and plan for 30-minute lightning stand-downs."},
        wet_low and {"hazard": "flooding", "sites": names(wet_low),
                     "text": f"{len(wet_low)} floodplain or levee-protected sites expect 1+ in of rain in a day: move "
                             "equipment to high ground and stage pumps the day before (first: "
                             + ", ".join(f"{s['name']} {first_rain(s):%a}" for s in wet_low[:3]) + ")."},
        windy and {"hazard": "high_wind", "sites": names(windy),
                   "text": f"{len(windy)} sites expect sustained wind over the limit: reschedule crane picks and lift work."},
    ] if x]
    days = sorted({d["day"] for s in sites for d in s["days"]})
    by_day = [{"day": day, "crew_hours": round(sum(d["crew_hours"] for s in sites for d in s["days"] if d["day"] == day), 1),
               "usd": sum(d["usd"] for s in sites for d in s["days"] if d["day"] == day)} for day in days]
    return {"sites": sites, "suggestions": suggestions, "by_day": by_day,
            "total": {"crew_hours": round(sum(s["crew_hours"] for s in sites), 1), "usd": sum(s["usd"] for s in sites),
                      "crew_size": a.get("crew_size", 4)}}


def invest(conn, limit=50, only=None):
    a = assumptions(conn)
    crew = a.get("crew_size", 4)
    out = []
    for s in conn.execute(INVEST).fetchall():
        if only and s["site_id"] not in only:
            continue
        by_cat = {c: float(d) * a.get(f"hours_lost_{c}", 0) * crew * rate(a, s["state"])
                  for c, d in (s["stop_days_per_year"] or {}).items()}
        total = sum(by_cat.values())
        if not total:
            continue
        top = max(by_cat, key=by_cat.get)
        cost, cut = a.get(f"mitigation_cost_{top}"), a.get(f"mitigation_reduction_{top}")
        savings = by_cat[top] * cut if cost and cut else None
        out.append({**{k: s[k] for k in ("site_id", "name", "state", "county", "utility_name", "fema_sfha", "fema_flood_zone",
                                         "long_term_exposure", "leveed_area_system", "levee_accreditation",
                                         "overtopping_chance_per_year", "crew_reports_1y", "stop_days_per_year")},
                    "annual_loss_usd": round(total), "loss_by_hazard_usd": {c: round(v) for c, v in by_cat.items()},
                    "top_hazard": top, "fix": RESOURCES.get(top), "fix_cost_usd": cost,
                    "annual_savings_usd": round(savings) if savings else None,
                    "payback_years": round(cost / savings, 1) if savings else None})
    out.sort(key=lambda r: r["annual_loss_usd"], reverse=True)
    return {"sites": out[:limit], "assumptions": {k: v for k, v in a.items()
                                                  if k.startswith(("mitigation_", "hours_lost_", "crew_size", "loaded_cost"))}}


MEASURED_HOURS = """
WITH ended AS (   -- our own stop-work alerts that have ended (cleared, or the all-clear passed), summed per site-day
    SELECT category, site_id, started_at::date AS day,
           sum(extract(epoch FROM coalesce(cleared_at, all_clear_at) - started_at) / 3600) AS hours
      FROM site_alerts
     WHERE level = 'stop_work' AND coalesce(cleared_at, all_clear_at) BETWEEN started_at AND now()
     GROUP BY 1, 2, 3)
SELECT category, count(*) AS site_days, round(avg(least(hours, %s))::numeric, 1) AS avg_hours FROM ended GROUP BY 1
"""
DISPATCH = """
SELECT utility, count(*) AS outages, count(*) FILTER (WHERE coalesce(on_site_at, assigned_at) IS NOT NULL) AS crews,
       count(*) FILTER (WHERE cleared_at IS NULL) AS open_now,
       round(avg(extract(epoch FROM on_site_at - first_seen) / 3600)::numeric, 1) AS hours_to_on_site,
       round(avg(extract(epoch FROM cleared_at - coalesce(on_site_at, assigned_at)) / 3600)::numeric, 1) AS repair_hours,
       mode() WITHIN GROUP (ORDER BY cause) AS top_cause, min(first_seen) AS since
  FROM crew_dispatch WHERE site_id = ANY (%s) GROUP BY 1 ORDER BY 2 DESC
"""
FINDINGS = """
SELECT (SELECT json_build_object('min', min(r), 'max', max(r), 'operators', count(*), 'min_weeks', min(n))
          FROM economic_correlations WHERE deseasonalized AND metric_x = 'stop_work_site_days_per_site'
           AND metric_y = 'outage_customer_hours' AND p_value < 0.001) AS workload,
       (SELECT json_build_object('supported', count(*) FILTER (WHERE supported), 'tests', count(*))
          FROM weather_bounceback) AS bounceback,
       (SELECT json_build_object('min', min(r2 - r2_without_weather), 'max', max(r2 - r2_without_weather),
                                 'utilities', count(*))
          FROM weather_sales_model WHERE r2 - r2_without_weather > 0.3) AS sales
"""


def findings(conn):
    """The economic experiments in plain sentences, with numbers read from their stored results."""
    f = conn.execute(FINDINGS).fetchone()
    w, b, sl = f["workload"], f["bounceback"], f["sales"]
    path = Path(__file__).resolve().parent.parent / "ml" / "models" / "contract_disruption.json"
    c = json.loads(path.read_text()) if path.exists() else None
    out = []
    if w and w["operators"]:
        out.append({"title": "Weather drives crew workload", "verdict": "supported",
                    "text": f"Weeks with more stop-work weather have more customer-hours without power at "
                            f"{w['operators']} grid operators (p < 0.001; r = {float(w['min']):.2f} to {float(w['max']):.2f}, "
                            f"seasons removed, {w['min_weeks']}+ weeks each)."})
    if sl and sl["utilities"]:
        out.append({"title": "Weather moves real sales", "verdict": "supported",
                    "text": f"Degree days explain {float(sl['min']):.0%} to {float(sl['max']):.0%} more of the month-to-month "
                            f"swing in {sl['utilities']} utilities' reported sales (EIA-861M) than trend alone."})
    if c:
        t = c["team_rule_check"]
        out.append({"title": "Contract failures are rarely weather's fault", "verdict": "not supported",
                    "text": f"Of {t['disrupted_with_tornado']} disrupted contracts with a tornado nearby, only "
                            f"{t['of_those_documented_weather']:.1%} say weather caused it. With length and type "
                            f"controlled, weather doesn't predict failure (AUC {c['cv_auc_with_weather']} with weather, "
                            f"{c['cv_auc_controls_only']} without). {c['attributed_to_weather']} of {c['disrupted']} "
                            "disruptions are weather by their own records."})
    if b and b["tests"]:
        out.append({"title": "No restoration boom after storms",
                    "verdict": "supported" if b["supported"] else "not supported",
                    "text": f"{b['supported']} of {b['tests']} tests found business above normal in the months after a "
                            "storm (field contracts, crew contractors' revenue, utilities' revenue)."})
    return out


def blank_utility(name):
    return {"utility": name, "sites": 0, "annual_loss_usd": 0, "week_usd": 0, "week_crew_hours": 0}


def economics(conn, only=None):
    a = assumptions(conn)
    wk = week(conn, only)
    inv = invest(conn, 700, only)["sites"]
    util_of = {r["site_id"]: r["utility_name"] or "Unknown"
               for r in conn.execute("SELECT site_id, utility_name FROM sites").fetchall()}
    ids = list(only) if only else list(util_of)
    by_util = {}
    for s in inv:
        u = by_util.setdefault(util_of[s["site_id"]], blank_utility(util_of[s["site_id"]]))
        u["sites"] += 1
        u["annual_loss_usd"] += s["annual_loss_usd"]
    for s in wk["sites"]:
        u = by_util.setdefault(util_of[s["site_id"]], blank_utility(util_of[s["site_id"]]))
        u["week_usd"] += s["usd"]
        u["week_crew_hours"] = round(u["week_crew_hours"] + s["crew_hours"], 1)
    sales = conn.execute("""
        SELECT m.utility_name, m.state, m.r2, m.r2_without_weather, m.mwh_per_cdd, m.mwh_per_hdd, m.months,
               (SELECT json_agg(json_build_object('month', e.month, 'weather_revenue_usd', e.weather_revenue_usd,
                                                  'weather_mwh', e.weather_mwh) ORDER BY e.month)
                  FROM weather_sales_effect e WHERE e.utility_id = m.utility_id AND e.state = m.state
                   AND e.month > (SELECT max(month) FROM weather_sales_effect) - interval '12 months') AS last_12
          FROM weather_sales_model m
         WHERE m.utility_name = ANY (%s) ORDER BY m.r2 - m.r2_without_weather DESC""",
                         (list({util_of[i] for i in ids}),)).fetchall()
    for r in sales:
        r["last_12_usd"] = round(sum(float(x["weather_revenue_usd"]) for x in r["last_12"] or []))
    measured = {r["category"]: r for r in conn.execute(MEASURED_HOURS, (a.get("shift_hours", 10),)).fetchall()}
    rows = conn.execute("SELECT key, value, unit, note FROM economic_assumptions ORDER BY key").fetchall()
    for r in rows:
        if r["key"].startswith("hours_lost_"):
            r["measured"] = measured.get(r["key"].removeprefix("hours_lost_"))
    trips = conn.execute("SELECT count(*) AS n, count(*) FILTER (WHERE source = 'company') AS company, "
                         "count(*) FILTER (WHERE source = 'outage_map') AS outage_map "
                         "FROM crew_trips WHERE site_id = ANY (%s)", (ids,)).fetchone()
    return {"week": {"total": wk["total"], "by_day": wk["by_day"]},
            "utilities": sorted(by_util.values(), key=lambda u: -u["annual_loss_usd"]),
            "annual_loss_usd": sum(u["annual_loss_usd"] for u in by_util.values()),
            "weather_in_sales": sales, "crew_dispatch": conn.execute(DISPATCH, (ids,)).fetchall(),
            "crew_trips": trips, "findings": findings(conn), "assumptions": rows}
