"""PlanIT Control API.

    uvicorn backend.app:app --reload        # from the project root; docs at http://127.0.0.1:8000/docs

Reads are public; every write needs an Auth0 sign-in (backend/auth.py) and is rate limited per user.
Set PLANIT_DEV=1 to enable the /dev endpoints (demo scenario, fake lightning strike, run a job, briefing now). They
write to the shared database, so keep PLANIT_DEV off on the public URL and run demos from a separate instance
(docs/DEPLOY.md); with Auth0 on, only supervisors (app_users.role) can call them.
"""
import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db.setup_db import load_env
load_env()  # read .env (GEMINI_API_KEY, GEMINI_MODEL, ...) into os.environ before reports/briefings read them

from backend import briefings, compliance, lightning, notify, nws, observations, plan, reports, scenario, team_briefing
from backend.auth import current_user, rate_limit, require_supervisor
from backend.db import connection

app = FastAPI(title="PlanIT Control API")
# Only the dashboard's own origins may call the API from a browser (CORS_ORIGINS, comma separated).
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get(
                       "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Planit-Role"])

# Crew photos are stored on this server's disk and served from /uploads.
# ponytail: local disk only, switch to S3/R2 when the API runs on more than one machine.
# UPLOADS_DIR: a persistent disk when deployed (e.g. /var/data/uploads); the repo's uploads/ folder locally.
UPLOADS = Path(os.environ.get("UPLOADS_DIR") or Path(__file__).resolve().parent.parent / "uploads")
UPLOADS.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
MAX_PHOTO_BYTES = 10 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}


def photo_ext(data):
    """File extension from the image's first bytes (not the client's claimed type), or None if it isn't one."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return ".heic"
    return None

LATLON = "ST_Y({0}::geometry) AS lat, ST_X({0}::geometry) AS lon"


def one(sql, params=()):
    with connection() as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(404, "Not found")
    return row


def many(sql, params=()):
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


@app.get("/health")
def health():
    """Database time, site count, and whether the /dev demo endpoints are on (the UI shows demo controls)."""
    return {**one("SELECT now() AS db_time, (SELECT count(*) FROM sites) AS sites"),
            "dev": os.environ.get("PLANIT_DEV") == "1"}


@app.get("/hazard-categories")
def hazard_categories():
    """Thresholds, radii and countdowns per category (the frontend's legend)."""
    return many("SELECT * FROM hazard_categories ORDER BY kind, category")


@app.get("/sites")
def sites(state: str | None = None, horizon: Literal["now", "next_day"] = "next_day",
          limit: int = Query(1000, le=1000)):
    """Dashboard ranking by hazard index for the chosen horizon (today or tomorrow), then active alerts.
    Today's score includes the live floor (active stop-work alert 8, warning 5, crew report 4) with its reason;
    model_score is the forecast model alone. percentile = where the score sits in this site's normal range for the
    month (90+ = unusual for this site)."""
    return many(f"""
        WITH latest AS (
            SELECT DISTINCT ON (site_id, horizon) site_id, horizon, score, category_scores, scored_for
              FROM hazard_scores ORDER BY site_id, horizon, scored_for DESC, created_at DESC
        ), alerts AS (
            SELECT site_id, count(*) FILTER (WHERE level = 'stop_work') AS stop_work_alerts,
                   count(*) FILTER (WHERE level = 'warning') AS warning_alerts
              FROM active_site_alerts GROUP BY site_id
        )
        SELECT s.site_id, s.name, s.operator, s.state, s.county, s.timezone, s.capacity_mw, s.primary_technology,
               s.coord_check, s.coverage, s.fema_flood_zone, s.fema_sfha, {LATLON.format('s.geog')},
               CASE WHEN %(horizon)s = 'now' THEN greatest(h.score, f.floor) ELSE h.score END AS score,
               h.score AS model_score, %(horizon)s AS horizon, h.scored_for, h.category_scores,
               greatest(n.score, f.floor) AS score_now, t.score AS score_next_day,
               n.scored_for AS score_now_for, t.scored_for AS score_next_day_for,
               f.floor AS live_floor, f.reason AS live_floor_reason,
               (h.category_scores -> '_meta' ->> 'percentile_for_site_month')::numeric AS percentile,
               (h.category_scores -> '_meta' ->> 'p_severe')::numeric AS p_severe,
               (SELECT k FROM jsonb_each(h.category_scores) e(k, v) WHERE k <> '_meta'
                 ORDER BY (v ->> 'score')::numeric DESC LIMIT 1) AS top_category,
               coalesce(a.stop_work_alerts, 0) AS stop_work_alerts, coalesce(a.warning_alerts, 0) AS warning_alerts
          FROM sites s
          LEFT JOIN latest h ON h.site_id = s.site_id AND h.horizon = %(horizon)s
          LEFT JOIN latest n ON n.site_id = s.site_id AND n.horizon = 'now'
          LEFT JOIN latest t ON t.site_id = s.site_id AND t.horizon = 'next_day'
          LEFT JOIN alerts a ON a.site_id = s.site_id
          LEFT JOIN site_live_floor f ON f.site_id = s.site_id
         WHERE %(state)s::text IS NULL OR s.state = upper(%(state)s)
         ORDER BY score DESC NULLS LAST, stop_work_alerts DESC, warning_alerts DESC, s.capacity_mw DESC NULLS LAST
         LIMIT %(limit)s""", {"state": state, "horizon": horizon, "limit": limit})


FEEDS = {  # worker job -> minutes it may go without a success before its data counts as stale (about 3 runs)
    "lightning": 5, "alerts": 8, "observations": 30, "rivers": 45, "tides": 45, "forecasts": 150, "scores": 150,
    "notify": 5, "briefings": 45, "crew_dispatch": 60,   # daily_weather (nightly model input) is not a live feed
}
COVERAGE = """
SELECT count(*) AS sites,
       count(*) FILTER (WHERE o.last_obs > now() - interval '2 hours') AS observations_current,
       count(*) FILTER (WHERE fc.issued > now() - interval '12 hours') AS forecasts_current,  -- NWS reissues grids every ~6-12 h; we fetch hourly
       count(*) FILTER (WHERE fc.future) AS forecasts_available,
       count(*) FILTER (WHERE o.days_7 >= 6) AS rain_week_ready,
       count(*) FILTER (WHERE s.weather_station_mi > 10) AS nearby_station_sites
  FROM sites s
  LEFT JOIN (SELECT site_id, max(observed_at) AS last_obs,
                    count(DISTINCT observed_at::date) FILTER (WHERE observed_at > now() - interval '7 days') AS days_7
               FROM weather_observations WHERE observed_at > now() - interval '7 days' GROUP BY 1) o USING (site_id)
  LEFT JOIN (SELECT site_id, max(issued_at) AS issued, bool_or(valid_at > now()) AS future
               FROM weather_forecasts WHERE valid_at > now() - interval '1 day' GROUP BY 1) fc USING (site_id)
"""
_status_cache = {}


@app.get("/data-status")
def data_status():
    """Is the live data current? Each worker feed: current / stale (no success within ~3 runs) / failing (last run
    errored) / starting (first run still going) / never run, plus how many sites have recent readings, forecasts and a week of rain history. The
    dashboard only trusts "no alerts" and all-clear countdowns while lightning and alerts are current."""
    hit = _status_cache.get("v")
    if hit and time.monotonic() - hit[0] < 15:  # polled every 30 s by every open dashboard: share one answer
        return hit[1]
    with connection() as conn:
        runs = {r["job"]: r for r in conn.execute("SELECT *, now() AS checked_at FROM worker_runs").fetchall()}
        checked = conn.execute("SELECT now() AS t").fetchone()["t"]
        coverage = conn.execute(COVERAGE).fetchone()
    feeds = []
    for job, minutes in FEEDS.items():
        r = runs.get(job)
        ok = r and r["last_success_at"] and checked - r["last_success_at"] < dt.timedelta(minutes=minutes)
        status = ("never run" if not r else "failing" if r["last_error"] else "current" if ok
                  else "starting" if not r["last_success_at"] else "stale")   # starting: first run still going
        feeds.append({"name": job, "status": status, "last_success": r and r["last_success_at"],
                      "detail": r and (r["last_error"] or (None if ok else "the worker hasn't updated this feed recently"))})
    out = {"checked_at": checked, "feeds": feeds, "coverage": coverage}
    _status_cache["v"] = (time.monotonic(), out)
    return out


@app.get("/nws-alerts/map")
def nws_alert_map():
    """Active NWS alerts as GeoJSON for the map layer. Storm-based warnings come with their polygon; zone and county
    alerts (heat, wind, flood watches) have no shape from NWS, so they're a point on each site they cover."""
    rows = many("""
        WITH a AS (SELECT DISTINCT ON (alert_id) * FROM nws_alerts ORDER BY alert_id, sent_at DESC)
        SELECT alert_id, event, severity, headline, expires_at, 'area' AS shape,
               ST_AsGeoJSON(geog::geometry)::json AS geometry
          FROM a WHERE expires_at > now() AND geog IS NOT NULL
        UNION ALL
        SELECT a.alert_id, a.event, a.severity, a.headline, a.expires_at, 'site', ST_AsGeoJSON(s.geog::geometry)::json
          FROM a JOIN sites s ON s.forecast_zone = ANY (a.ugc_codes) OR s.county_ugc = ANY (a.ugc_codes)
         WHERE a.expires_at > now() AND a.geog IS NULL""")
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": r.pop("geometry"), "properties": r} for r in rows]}


@app.get("/lightning/recent")
def recent_lightning(minutes: int = Query(30, le=240)):
    """Lightning strikes near the sites in the last `minutes` (GOES-19 GLM and demo strikes), newest first."""
    return many(f"""SELECT observed_at, source, {LATLON.format('geog')} FROM lightning_strikes
                     WHERE observed_at > now() - make_interval(mins => %s) ORDER BY observed_at DESC LIMIT 2000""",
                (minutes,))


@app.get("/sites/{site_id}")
def site(site_id: int):
    """One site's full picture: details, historical risk profile, active alerts, latest score."""
    s = one(f"SELECT s.*, {LATLON.format('s.geog')} FROM sites s WHERE site_id = %s", (site_id,))
    s.pop("geog")
    profile = many("SELECT * FROM site_risk_profile WHERE site_id = %s", (site_id,))
    if profile:
        profile[0].pop("geog")
        profile[0] = reports.cdc_safe(profile[0])
    levee = next(iter(many("SELECT * FROM site_levees WHERE site_id = %s", (site_id,))), None)
    return {**s, "risk_profile": profile[0] if profile else None, "levee": levee,
            "active_alerts": many("SELECT * FROM active_site_alerts WHERE site_id = %s ORDER BY level DESC, started_at",
                                  (site_id,)),
            "latest_score": next(iter(many("SELECT * FROM hazard_scores WHERE site_id = %s "
                                           "ORDER BY scored_for DESC, created_at DESC LIMIT 1", (site_id,))), None)}


@app.get("/sites/{site_id}/forecast")
def forecast(site_id: int, hours: int = Query(48, le=168)):
    """Hourly NWS forecast for the site (latest issue), from now on."""
    return many("SELECT valid_at, temp_f, heat_index_f, wind_chill_f, precip_chance, precip_in, wind_gust_mph, "
                "thunder_chance, visibility_mi, short_forecast, issued_at FROM weather_forecasts "
                "WHERE site_id = %s AND valid_at >= date_trunc('hour', now()) "
                "AND valid_at < now() + make_interval(hours => %s) ORDER BY valid_at", (site_id, hours))


COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()
PAST_HOURS = """
SELECT date_trunc('hour', observed_at) AS valid_at, max(temp_f) AS temp_f, min(temp_f) AS temp_min_f,
       max(heat_index_f) AS heat_index_f, max(precip_1h_in) AS precip_in, max(wind_mph) AS wind_mph,
       max(wind_gust_mph) AS wind_gust_mph
  FROM weather_observations WHERE site_id = %s AND observed_at > now() - interval '7 days' AND source LIKE 'nws:%%'
 GROUP BY 1 ORDER BY 1
"""
NEXT_HOURS = """
SELECT valid_at, temp_f, temp_f AS temp_min_f, heat_index_f, precip_in, precip_chance, wind_mph, wind_gust_mph,
       thunder_chance, short_forecast, issued_at
  FROM weather_forecasts WHERE site_id = %s AND valid_at >= date_trunc('hour', now()) ORDER BY valid_at
"""
LIGHTNING_NEAR = """
SELECT count(*) FILTER (WHERE d <= 10) AS within_10_mi, count(*) FILTER (WHERE d <= 20) AS within_20_mi,
       count(*) AS within_100_mi, round(min(d)::numeric, 1) AS nearest_mi,
       (array_agg(az ORDER BY d))[1] AS bearing_deg, (array_agg(t ORDER BY d))[1] AS nearest_at
  FROM (SELECT ST_Distance(l.geog, s.geog) / 1609.344 AS d, degrees(ST_Azimuth(s.geog, l.geog)) AS az,
               l.observed_at AS t
          FROM lightning_strikes l JOIN sites s ON s.site_id = %s
         WHERE l.observed_at > now() - interval '30 minutes' AND ST_DWithin(l.geog, s.geog, 100 * 1609.344)) n
"""


TIDE_NEAR = """
SELECT t.station_id, t.name, round((ST_Distance(t.geog, s.geog) / 1609.344)::numeric, 1) AS miles, r.observed_at,
       round(r.level_ft - t.mhhw_ft, 2) AS above_high_tide_ft, round(t.minor_ft - t.mhhw_ft, 2) AS minor_flood_above_high_tide_ft,
       r.level_ft >= t.minor_ft AS flooding
  FROM sites s JOIN tide_stations t ON ST_DWithin(t.geog, s.geog, 15 * 1609.344)
  LEFT JOIN LATERAL (SELECT level_ft, observed_at FROM tide_readings WHERE station_id = t.station_id
                      ORDER BY observed_at DESC LIMIT 1) r ON true
 WHERE s.site_id = %s ORDER BY t.geog <-> s.geog LIMIT 1
"""


def daily(past, future, tz):
    """Per local day: high, low, rain, max gust, max heat index, max thunder; 'measured', 'forecast' or both (today)."""
    from zoneinfo import ZoneInfo
    days = {}
    for kind, rows in (("measured", past), ("forecast", future)):
        for r in rows:
            day = r["valid_at"].astimezone(ZoneInfo(tz)).date()
            d = days.setdefault(day, {"day": day, "sources": set(), "temps": [], "rain_in": 0.0, "gust": [], "heat": [],
                                      "thunder": []})
            d["sources"].add(kind)
            d["temps"] += [float(v) for v in (r["temp_f"], r["temp_min_f"]) if v is not None]
            d["rain_in"] += float(r["precip_in"] or 0)
            d["gust"] += [float(r["wind_gust_mph"])] if r["wind_gust_mph"] is not None else []
            d["heat"] += [float(r["heat_index_f"])] if r["heat_index_f"] is not None else []
            d["thunder"] += [float(r.get("thunder_chance"))] if r.get("thunder_chance") is not None else []
    top = lambda xs: round(max(xs)) if xs else None
    return [{"day": d["day"], "source": " + ".join(sorted(d["sources"])), "high_f": top(d["temps"]),
             "low_f": round(min(d["temps"])) if d["temps"] else None, "rain_in": round(d["rain_in"], 2),
             "max_gust_mph": top(d["gust"]), "max_heat_index_f": top(d["heat"]), "max_thunder_pct": top(d["thunder"])}
            for d in sorted(days.values(), key=lambda d: d["day"])]


@app.get("/sites/{site_id}/weather")
def site_weather(site_id: int, refresh: bool = False):
    """Weather scanner: the past 7 days measured at the site's station, today so far, the next 7 days forecast
    (hourly and by day), and the nearest lightning in the last 30 minutes with distance and direction.
    Opening it pulls fresh NWS data when it's stale (readings over 20 min old, forecast over 2 h; refresh=true:
    over 2 min). The worker also refreshes every site at 6 PM local, right before its briefing."""
    with connection() as conn:
        s = conn.execute("SELECT site_id, timezone, weather_station_id FROM sites WHERE site_id = %s", (site_id,)).fetchone()
        if s is None:
            raise HTTPException(404, "Not found")
        age = conn.execute(
            "SELECT now() - max(observed_at) FILTER (WHERE source LIKE 'nws:%%') AS obs_age, "
            "count(DISTINCT date_trunc('hour', observed_at)) FILTER (WHERE observed_at > now() - interval '7 days' "
            "AND source LIKE 'nws:%%') AS week_hours, "
            "(SELECT now() - max(issued_at) FROM weather_forecasts WHERE site_id = %(s)s) AS fc_age "
            "FROM weather_observations WHERE site_id = %(s)s", {"s": site_id}).fetchone()
        limit = dt.timedelta(minutes=2) if refresh else None
        if age["obs_age"] is None or age["obs_age"] > (limit or dt.timedelta(minutes=20)):
            observations.sync(conn, [site_id], history_days=7 if age["week_hours"] < 150 else 0)  # of 168
        if age["fc_age"] is None or age["fc_age"] > (limit or dt.timedelta(hours=2)):
            nws.sync_forecasts(conn, [site_id])
        past = conn.execute(PAST_HOURS, (site_id,)).fetchall()
        future = conn.execute(NEXT_HOURS, (site_id,)).fetchall()
        bolt = conn.execute(LIGHTNING_NEAR, (site_id,)).fetchone()
        latest = conn.execute("SELECT * FROM weather_observations WHERE site_id = %s AND source LIKE 'nws:%%' "
                              "ORDER BY observed_at DESC LIMIT 1", (site_id,)).fetchone()
        coast = conn.execute(TIDE_NEAR, (site_id,)).fetchone()
    if bolt["nearest_mi"] is not None:
        bolt["direction"] = COMPASS[round(float(bolt["bearing_deg"]) / 22.5) % 16]
    return {"site_id": site_id, "timezone": s["timezone"], "station": s["weather_station_id"], "latest_reading": latest,
            "forecast_issued_at": future[0]["issued_at"] if future else None,
            "past_hours": past, "next_hours": future, "daily": daily(past, future, s["timezone"]), "lightning": bolt,
            "coast": coast}


@app.get("/sites/{site_id}/hazard-events")
def hazard_events(site_id: int, category: str | None = None, limit: int = Query(50, le=500)):
    """Historical events tied to the site (within each category's history radius), newest first."""
    return many("SELECT * FROM site_hazard_events WHERE site_id = %s AND (%s::text IS NULL OR category = %s) "
                "ORDER BY event_time DESC LIMIT %s", (site_id, category, category, limit))


@app.get("/alerts/active")
def active_alerts(site_id: int | None = None, state: str | None = None):
    """Open alerts with their all-clear countdown. Poll this every 15-30 s for instant alerts."""
    return many(f"""SELECT a.*, s.state, {LATLON.format('s.geog')},
                           extract(epoch FROM a.all_clear_remaining)::int AS all_clear_seconds
                      FROM active_site_alerts a JOIN sites s USING (site_id)
                     WHERE (%(site)s::bigint IS NULL OR a.site_id = %(site)s)
                       AND (%(state)s::text IS NULL OR s.state = upper(%(state)s))
                     ORDER BY a.level DESC, a.last_trigger_at DESC""", {"site": site_id, "state": state})


@app.get("/briefings/{site_id}")
def briefing(site_id: int, date: dt.date | None = None):
    """The 6 PM next-day briefing (latest one if no date)."""
    return one("SELECT * FROM daily_briefings WHERE site_id = %s AND (%s::date IS NULL OR briefing_date = %s) "
               "ORDER BY briefing_date DESC LIMIT 1", (site_id, date, date))


@app.post("/sites/{site_id}/report")
def site_report(site_id: int, refresh: bool = False, user=Depends(rate_limit("ai", 10))):
    """On-tap safety report for one site: facts computed in code, explained by Gemini (or a template fallback).
    Cached for 30 minutes unless refresh=true. Check `verified`: false means the model wrote a number that isn't
    in the facts (listed in unverified_numbers)."""
    with connection() as conn:
        r = reports.generate(conn, site_id, refresh)
    if r is None:
        raise HTTPException(404, "Not found")
    return r


@app.post("/sites/{site_id}/compliance")
def site_compliance(site_id: int, refresh: bool = False, user=Depends(rate_limit("ai", 10))):
    """Supervisor next steps for OSHA reports near the site and open crew reports: priorities computed in code,
    playbook steps with the OSHA standards they come from, reporting deadlines; Gemini (optional) explains them."""
    with connection() as conn:
        r = compliance.generate(conn, site_id, refresh)
    if r is None:
        raise HTTPException(404, "Not found")
    return r


def clear_report_alert(conn, site_id, reported_at):
    """Close the site alert a crew report raised. The report may have opened it or extended an alert that was already
    open (e.g. an iNaturalist sighting), so match its trigger time too; a real sighting is re-raised by its job."""
    conn.execute("UPDATE site_alerts SET cleared_at = now() WHERE site_id = %s AND cleared_at IS NULL "
                 "AND message LIKE '%%Crew report:%%' AND (started_at = %s OR last_trigger_at = %s)",
                 (site_id, reported_at, reported_at))


class ReportStatus(BaseModel):
    status: Literal["open", "resolved", "dismissed"]


@app.patch("/crew-reports/{report_id}")
def update_crew_report(report_id: uuid.UUID, body: ReportStatus, user=Depends(rate_limit("write", 60))):
    """Supervisor closes (or reopens) a crew report. Closing also clears the site alert it raised."""
    with connection() as conn:
        r = conn.execute("UPDATE crew_reports SET status = %(s)s, "
                         "resolved_at = CASE WHEN %(s)s = 'open' THEN NULL ELSE now() END, "
                         "resolved_by = CASE WHEN %(s)s = 'open' THEN NULL ELSE %(u)s::uuid END "
                         "WHERE report_id = %(id)s RETURNING report_id, site_id, category, status, reported_at, "
                         "resolved_at", {"s": body.status, "u": user["user_id"], "id": report_id}).fetchone()
        if r is None:
            raise HTTPException(404, "Not found")
        if body.status != "open":
            clear_report_alert(conn, r["site_id"], r["reported_at"])
    return r


@app.delete("/crew-reports/{report_id}", status_code=204)
def delete_crew_report(report_id: uuid.UUID, user=Depends(rate_limit("write", 60))):
    """Remove a crew report, the site alert it raised and its photo: your own reports, or any if you're a supervisor.
    (To keep a record instead, close it with PATCH status=resolved or dismissed.)"""
    with connection() as conn:
        r = conn.execute("SELECT site_id, reported_at, user_id, photo_url FROM crew_reports WHERE report_id = %s",
                         (report_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "Not found")
        if r["user_id"] != user["user_id"] and not user["can_supervise"]:
            raise HTTPException(403, "You can delete your own reports; supervisors can delete any.")
        clear_report_alert(conn, r["site_id"], r["reported_at"])
        conn.execute("DELETE FROM crew_reports WHERE report_id = %s", (report_id,))
    if r["photo_url"] and "/uploads/" in r["photo_url"]:  # our own upload: just the file name, never a path
        (UPLOADS / Path(r["photo_url"].rsplit("/", 1)[-1]).name).unlink(missing_ok=True)


@app.get("/plan/{horizon}")
def supervisor_plan(horizon: Literal["now", "week", "invest", "economics"], limit: int = Query(50, le=700),
                    site_ids: str | None = Query(None, max_length=8000, description="comma-separated: only these sites")):
    """Where to focus attention and resources first: now (action queue with actions and resources), week (7-day
    forecast outlook with what to schedule or stage), invest (sites by yearly weather loss, with a fix and payback;
    fix costs are labeled assumptions), economics (region-wide money view: this week's crew-hours and dollars at
    risk, yearly loss by utility, weather in real sales, real crew dispatches, findings, assumptions)."""
    only = {int(x) for x in site_ids.split(",") if x.strip().isdigit()} if site_ids else None
    with connection() as conn:
        return {"now": lambda: plan.now(conn, limit, only), "week": lambda: plan.week(conn, only),
                "invest": lambda: plan.invest(conn, limit, only), "economics": lambda: plan.economics(conn, only)}[horizon]()


class Assumption(BaseModel):
    value: float = Field(ge=0, le=1_000_000)


ASSUMPTION_LIMITS = {"crew_size": (1, 50), "shift_hours": (1, 24)}  # hours_lost_* <= 24, mitigation_reduction_* <= 1


@app.put("/economics/assumptions/{key}")
def set_assumption(key: str, a: Assumption, user=Depends(require_supervisor)):
    """Replace one economic assumption with your company's number (supervisors). The note records who and when;
    the BLS loader never overwrites it. Invest, Economics, the week's at-risk dollars and briefings use it at once."""
    lo, hi = ASSUMPTION_LIMITS.get(key, (0, 24 if key.startswith("hours_lost_") else 1 if key.startswith(
        "mitigation_reduction_") else 1_000_000))
    if not lo <= a.value <= hi:
        raise HTTPException(422, f"{key} must be between {lo} and {hi}.")
    with connection() as conn:
        row = conn.execute("UPDATE economic_assumptions SET value = %s, note = %s WHERE key = %s RETURNING *",
                           (a.value, f"COMPANY: set by {user.get('email') or user.get('name') or 'a supervisor'} "
                                     f"on {dt.date.today()}", key)).fetchone()
    if row is None:
        raise HTTPException(404, "No such assumption")
    return row


@app.get("/model/backtest")
def model_backtest():
    """Walk-forward backtest results and the current model's test metrics (ml/backtest.py, ml/hazard_model.py)."""
    models = Path(__file__).resolve().parent.parent / "ml" / "models"
    load = lambda name: json.loads((models / name).read_text()) if (models / name).exists() else None
    return {"metrics": load("metrics.json"), "backtest": load("backtest.json")}


@app.get("/sites/{site_id}/economics")
def site_economics(site_id: int, weeks: int = Query(26, le=520)):
    """The site's company and grid operator, weekly weather impact and demand, and significant correlations."""
    s = one("SELECT site_id, name, utility_id, utility_name, balancing_authority FROM sites WHERE site_id = %s",
            (site_id,))
    return {**s,
            "weekly": many("SELECT * FROM economic_weekly WHERE balancing_authority = %s ORDER BY week DESC LIMIT %s",
                           (s["balancing_authority"], weeks)),
            "correlations": many("SELECT * FROM economic_correlations WHERE entity IN (%s, %s) AND deseasonalized "
                                 "ORDER BY p_value", (s["balancing_authority"], s["utility_name"])),
            "assumptions": many("SELECT * FROM economic_assumptions ORDER BY key")}


@app.get("/economics/contract-disruption")
def contract_disruption():
    """Did weather disrupt field contracts, and did bad revenue quarters follow? (ml/contract_disruption.py)"""
    path = Path(__file__).resolve().parent.parent / "ml" / "models" / "contract_disruption.json"
    if not path.exists():
        raise HTTPException(404, "Not computed yet: run python -m ml.contract_disruption")
    return json.loads(path.read_text())


@app.get("/sites/{site_id}/contracts")
def site_contracts(site_id: int, limit: int = Query(20, le=200)):
    """Field-construction contracts performed in the site's county (USAspending stand-in), weather-attributed
    disruptions first, with the evidence: documented_and_corroborated / documented / storm_overlap_only / none."""
    s = one("SELECT site_id, county_fips FROM sites WHERE site_id = %s", (site_id,))
    rows = many("""SELECT f.award_key, f.recipient, f.agency, f.naics_description, f.description, f.city, f.start_date,
                          f.original_end_date, f.final_end_date, f.potential_value_usd, f.terminated, f.slip_days,
                          f.weather_cited_text, a.disrupted, a.attributed, a.evidence, a.storm_match, a.exposure
                     FROM field_contracts f JOIN contract_weather_attribution a USING (award_key)
                    WHERE f.county_fips = %s
                    ORDER BY a.attributed DESC, a.disrupted DESC, f.start_date DESC LIMIT %s""", (s["county_fips"], limit))
    totals = one("""SELECT count(*) AS contracts, count(*) FILTER (WHERE a.disrupted) AS disrupted,
                           count(*) FILTER (WHERE a.attributed) AS weather_attributed
                      FROM field_contracts f JOIN contract_weather_attribution a USING (award_key)
                     WHERE f.county_fips = %s""", (s["county_fips"],))
    return {**totals, "county_fips": s["county_fips"], "contracts_list": rows}


@app.get("/economics/correlations")
def economic_correlations(significant_only: bool = True):
    """Weather vs demand/sales correlations for every company (deseasonalized and raw)."""
    return many("SELECT * FROM economic_correlations WHERE NOT %s OR p_value < 0.05 ORDER BY deseasonalized DESC, "
                "abs(r) DESC", (significant_only,))


@app.post("/uploads", status_code=201)
async def upload_photo(file: UploadFile, request: Request, user=Depends(rate_limit("upload", 20))):
    """Upload a crew photo (JPEG, PNG, WebP or HEIC, up to 10 MB). Send the returned url as photo_url."""
    if file.content_type not in PHOTO_TYPES:
        raise HTTPException(415, f"Unsupported type {file.content_type!r}; send one of {sorted(PHOTO_TYPES)}")
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is larger than 10 MB")
    ext = photo_ext(data)
    if not ext:
        raise HTTPException(415, "That file isn't a JPEG, PNG, WebP or HEIC image.")
    name = f"{uuid.uuid4().hex}{ext}"
    (UPLOADS / name).write_bytes(data)
    return {"url": str(request.url_for("uploads", path=name))}


class CrewReport(BaseModel):
    report_id: uuid.UUID | None = None      # set by the app, so a report queued offline and re-sent isn't saved twice
    reported_at: dt.datetime | None = None  # when it was seen (queued reports); kept within the last 24 hours
    category: str = Field(examples=["wildlife"])
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    description: str | None = Field(None, max_length=1000)
    photo_url: str | None = None          # from POST /uploads
    reported_by: str | None = Field(None, max_length=80)
    severity: int | None = Field(None, ge=1, le=5)
    site_id: int | None = None            # defaults to the nearest site within 10 miles


@app.post("/crew-reports", status_code=201)
def create_crew_report(r: CrewReport, user=Depends(rate_limit("write", 60))):
    """Save a crew hazard report and alert the site right away (warning, open for 12 hours). Sending the same
    report_id again returns the saved report instead of a duplicate."""
    cols = f"report_id, reported_at, site_id, category, status, {LATLON.format('geog')}"
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM hazard_categories WHERE category = %s", (r.category,)).fetchone():
            raise HTTPException(422, f"Unknown category {r.category!r}; see GET /hazard-categories")
        if r.report_id and (old := conn.execute(f"SELECT {cols} FROM crew_reports WHERE report_id = %s",
                                                (r.report_id,)).fetchone()):
            return old
        report = conn.execute(
            f"""INSERT INTO crew_reports (report_id, reported_at, site_id, category, description, photo_url, geog,
                                          reported_by, severity, user_id, reported_as)
                VALUES (coalesce(%(report_id)s, gen_random_uuid()),
                        least(now(), greatest(coalesce(%(reported_at)s, now()), now() - interval '24 hours')),
                        coalesce(%(site_id)s, (SELECT site_id FROM sites
                                               WHERE ST_DWithin(geog, ST_MakePoint(%(lon)s, %(lat)s)::geography, 16093)
                                               ORDER BY geog <-> ST_MakePoint(%(lon)s, %(lat)s)::geography LIMIT 1)),
                        %(category)s, %(description)s, %(photo_url)s, ST_MakePoint(%(lon)s, %(lat)s)::geography,
                        coalesce(%(reported_by)s, %(user_name)s), %(severity)s, %(user_id)s, %(identity)s)
                RETURNING {cols}""",
            {**r.model_dump(), "user_name": user["name"], "user_id": user["user_id"], "identity": user["identity"]}).fetchone()
        if report["site_id"]:
            conn.execute("SELECT raise_site_alert(%s, %s, 'warning', %s, NULL, %s, %s, %s)",
                         (report["site_id"], r.category, report["reported_at"],
                          "Crew report: " + (r.description or r.category.replace("_", " ")),
                          report["reported_at"] + dt.timedelta(hours=12), r.photo_url))
        return report


@app.get("/crew-reports")
def crew_reports(site_id: int | None = None, category: str | None = None,
                 status: Literal["open", "resolved", "dismissed"] | None = "open", limit: int = Query(100, le=1000)):
    return many(f"""SELECT report_id, reported_at, site_id, category, description, photo_url, reported_by, severity,
                           status, resolved_at, {LATLON.format('geog')}
                      FROM crew_reports
                     WHERE (%(site)s::bigint IS NULL OR site_id = %(site)s)
                       AND (%(category)s::text IS NULL OR category = %(category)s)
                       AND (%(status)s::text IS NULL OR status = %(status)s)
                     ORDER BY reported_at DESC LIMIT %(limit)s""",
                {"site": site_id, "category": category, "status": status, "limit": limit})


@app.get("/hazard-pins")
def hazard_pins(days: int = Query(1095, ge=1, le=3650)):
    """Map layer of past hazards for future crews: crew reports (any status) and OSHA severe-injury reports within
    50 miles of a site in the last `days` days (OSHA data lags, so its window ends at the latest report on file)."""
    return many(f"""
        SELECT 'crew' AS source, report_id::text AS id, reported_at AS at, category, status,
               coalesce(description, replace(category, '_', ' ')) AS title, photo_url, severity,
               {LATLON.format('geog')}
          FROM crew_reports WHERE reported_at > now() - make_interval(days => %(days)s)
        UNION ALL
        SELECT 'osha', o.report_id::text, o.event_time,
               coalesce(o.weather_category, o.crew_categories[1], 'osha_inspection'), NULL,
               coalesce(o.employer || ': ', '') || coalesce(o.event_title, o.nature, 'severe injury'), NULL,
               CASE WHEN o.amputation > 0 OR o.loss_of_eye > 0 THEN 5 WHEN o.hospitalized > 0 THEN 4 ELSE 3 END,
               {LATLON.format('o.geog')}
          FROM osha_injuries o
         WHERE o.geog IS NOT NULL
           AND o.event_time > (SELECT max(event_time) FROM osha_injuries) - make_interval(days => %(days)s)
           AND EXISTS (SELECT 1 FROM sites s WHERE ST_DWithin(s.geog, o.geog, 80467))
         ORDER BY at DESC LIMIT 6000""", {"days": days})


@app.get("/me")
def me(user=Depends(current_user)):
    """The signed-in user (created on first sign-in)."""
    return {k: user[k] for k in ("user_id", "name", "email", "role", "created_at", "identity", "supervisor_approved",
                                 "can_supervise")}


class SavedLocation(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_mi: float = Field(50, ge=1, le=300)
    site_id: int | None = None   # set when saved from a site card (the ☆ toggle)


LOCATION_COLS = f"""l.location_id, l.name, l.radius_mi, l.created_at, l.site_id, {LATLON.format('l.geog')},
    (SELECT count(*) FROM sites s WHERE ST_DWithin(s.geog, l.geog, l.radius_mi * 1609.344)) AS plants,
    b.briefing_date, b.summary AS briefing, b.sent_at AS briefing_at"""
LOCATION_BRIEF = """LEFT JOIN LATERAL (SELECT * FROM location_briefings WHERE location_id = l.location_id
                                       ORDER BY briefing_date DESC LIMIT 1) b ON true"""


@app.get("/me/locations")
def my_locations(user=Depends(current_user)):
    """Your saved locations, each with its plant count and latest 6 PM briefing."""
    return many(f"SELECT {LOCATION_COLS} FROM saved_locations l {LOCATION_BRIEF} WHERE l.user_id = %s "
                "AND l.identity = %s ORDER BY l.created_at", (user["user_id"], user["identity"]))


@app.post("/me/locations", status_code=201)
def save_location(loc: SavedLocation, user=Depends(rate_limit("write", 60))):
    """Save a starting point. At 6 PM local it gets a briefing for the plants within radius_mi, sent to your
    Discord/Slack channels that include 6 PM briefings."""
    with connection() as conn:
        # the same spot (within 50 m) saved again: keep the one row, with the new name and radius
        same = conn.execute("UPDATE saved_locations SET name = %s, radius_mi = %s, site_id = coalesce(%s, site_id) "
                            "WHERE location_id = (SELECT location_id FROM saved_locations WHERE user_id = %s AND identity = %s "
                            "AND ST_DWithin(geog, ST_MakePoint(%s, %s)::geography, 50) LIMIT 1) RETURNING location_id",
                            (loc.name.strip(), loc.radius_mi, loc.site_id, user["user_id"], user["identity"], loc.lon,
                             loc.lat)).fetchone()
        if same:
            return conn.execute(f"SELECT {LOCATION_COLS} FROM saved_locations l {LOCATION_BRIEF} WHERE l.location_id = %s",
                                (same["location_id"],)).fetchone()
        if conn.execute("SELECT count(*) AS n FROM saved_locations WHERE user_id = %s AND identity = %s",
                        (user["user_id"], user["identity"])).fetchone()["n"] >= 20:
            raise HTTPException(409, "You already have 20 saved locations; delete one first.")
        lid = conn.execute("INSERT INTO saved_locations (user_id, identity, name, geog, radius_mi, site_id) "
                           "VALUES (%s, %s, %s, ST_MakePoint(%s, %s)::geography, %s, %s) "
                           "ON CONFLICT (user_id, identity, site_id) WHERE site_id IS NOT NULL DO UPDATE SET name = EXCLUDED.name "
                           "RETURNING location_id",
                           (user["user_id"], user["identity"], loc.name.strip(), loc.lon, loc.lat, loc.radius_mi,
                            loc.site_id)).fetchone()["location_id"]
        return conn.execute(f"SELECT {LOCATION_COLS} FROM saved_locations l {LOCATION_BRIEF} WHERE l.location_id = %s",
                            (lid,)).fetchone()


@app.delete("/me/locations/{location_id}", status_code=204)
def delete_location(location_id: int, user=Depends(rate_limit("write", 60))):
    with connection() as conn:
        if not conn.execute("DELETE FROM saved_locations WHERE location_id = %s AND user_id = %s RETURNING 1",
                            (location_id, user["user_id"])).fetchone():
            raise HTTPException(404, "Not found")


@app.get("/me/reports")
def my_reports(limit: int = Query(50, le=200), user=Depends(current_user)):
    """Crew reports you sent, newest first, with their site and whether a supervisor closed them."""
    return many(f"""SELECT r.report_id, r.reported_at, r.category, r.description, r.photo_url, r.severity, r.status,
                           r.resolved_at, r.site_id, s.name AS site_name, {LATLON.format('r.geog')}
                      FROM crew_reports r LEFT JOIN sites s USING (site_id)
                     WHERE r.user_id = %s AND coalesce(r.reported_as, 'crew') = %s
                     ORDER BY r.reported_at DESC LIMIT %s""", (user["user_id"], user["identity"], limit))


@app.post("/me/locations/{location_id}/briefing")
def location_briefing_now(location_id: int, user=Depends(rate_limit("ai", 10))):
    """Write tomorrow's briefing for a saved location now (normally 6 PM local). It's also sent to your channels."""
    with connection() as conn:
        day = conn.execute("""SELECT (now() AT TIME ZONE tz.timezone)::date + 1 AS d FROM saved_locations l
                              CROSS JOIN LATERAL (SELECT timezone FROM sites ORDER BY geog <-> l.geog LIMIT 1) tz
                              WHERE l.location_id = %s AND l.user_id = %s""", (location_id, user["user_id"])).fetchone()
        if day is None:
            raise HTTPException(404, "Not found")
        return {"briefing_date": day["d"], "summary": briefings.save_location_briefing(conn, location_id, day["d"])}


class BriefingRequest(BaseModel):
    kind: Literal["start", "6pm"] = "start"
    lat: float | None = Field(None, ge=-90, le=90)     # null = all sites
    lon: float | None = Field(None, ge=-180, le=180)
    radius_mi: float = Field(50, ge=1, le=300)
    label: str | None = Field(None, max_length=80)
    refresh: bool = False                               # "Run again": skip the 2 h cache


@app.post("/me/briefing")
def my_briefing(b: BriefingRequest, user=Depends(rate_limit("ai", 10))):
    """Your briefing across your starting area and saved locations (Gemini, template fallback). For a supervisor
    identity it includes a drafted crew message per Discord/Slack channel; nothing is sent until you press Send."""
    if (b.lat is None) != (b.lon is None):
        raise HTTPException(422, "Give both lat and lon, or neither for all sites.")
    with connection() as conn:
        return team_briefing.generate(conn, user, b.model_dump())


CHANNEL_COLS = ("channel_id, kind, label, webhook_hint, levels, site_ids, briefings, crew_reports, created_at, "
                "last_sent_at, last_error")


class Channel(BaseModel):
    webhook_url: str = Field(max_length=500, description="Discord or Slack incoming-webhook URL")
    label: str | None = Field(None, max_length=80)
    levels: list[Literal["warning", "stop_work"]] = ["stop_work"]
    site_ids: list[int] | None = Field(None, max_length=500)  # null = every site
    briefings: bool = True
    crew_reports: bool = True


@app.get("/me/channels")
def my_channels(user=Depends(current_user)):
    """Your Discord/Slack channels (webhook URLs are never returned, only their last 4 characters)."""
    return many(f"SELECT {CHANNEL_COLS} FROM notification_channels WHERE user_id = %s AND identity = %s ORDER BY channel_id",
                (user["user_id"], user["identity"]))


@app.post("/me/channels", status_code=201)
def add_channel(c: Channel, user=Depends(rate_limit("write", 60))):
    """Add a channel. Only https://discord.com/api/webhooks/... and https://hooks.slack.com/services/... are accepted."""
    url = c.webhook_url.strip()
    kind = notify.webhook_kind(url)
    if not kind:
        raise HTTPException(422, "Paste a Discord (https://discord.com/api/webhooks/...) or Slack "
                                 "(https://hooks.slack.com/services/...) incoming-webhook URL.")
    if not (c.levels or c.briefings or c.crew_reports):
        raise HTTPException(422, "Pick something to send.")
    try:
        secret = notify.encrypt(url)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    with connection() as conn:
        if conn.execute("SELECT count(*) AS n FROM notification_channels WHERE user_id = %s AND identity = %s",
                        (user["user_id"], user["identity"])).fetchone()["n"] >= 10:
            raise HTTPException(409, "You already have 10 channels; delete one first.")
        return conn.execute(
            f"INSERT INTO notification_channels (user_id, identity, kind, label, webhook_encrypted, webhook_hint, levels, "
            f"site_ids, briefings, crew_reports) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {CHANNEL_COLS}",
            (user["user_id"], user["identity"], kind, c.label, secret, url[-4:], sorted(set(c.levels)), c.site_ids, c.briefings,
             c.crew_reports)).fetchone()


def own_channel(conn, channel_id, user):
    ch = conn.execute("SELECT * FROM notification_channels WHERE channel_id = %s AND user_id = %s",
                      (channel_id, user["user_id"])).fetchone()
    if ch is None:
        raise HTTPException(404, "Not found")
    return ch


@app.delete("/me/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: int, user=Depends(rate_limit("write", 60))):
    with connection() as conn:
        own_channel(conn, channel_id, user)
        conn.execute("DELETE FROM notification_channels WHERE channel_id = %s", (channel_id,))


@app.post("/me/channels/{channel_id}/test")
def test_channel(channel_id: int, user=Depends(rate_limit("test", 5))):
    """Send a hello message to the channel now, to check the webhook works."""
    with connection() as conn:
        error = notify.send_test(conn, own_channel(conn, channel_id, user))
    if error:
        raise HTTPException(502, f"The webhook didn't accept the message ({error}). Check the URL in Discord/Slack.")
    return {"sent": True}


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=notify.MAX_CHARS)


@app.post("/me/channels/{channel_id}/send")
def send_to_channel(channel_id: int, m: Message, user=Depends(rate_limit("send", 10))):
    """Post a message (e.g. a briefing's drafted crew message, after you've read it) to one of your channels."""
    with connection() as conn:
        error = notify.send_test(conn, own_channel(conn, channel_id, user), m.text.strip())
    if error:
        raise HTTPException(502, f"The webhook didn't accept the message ({error}). Check the URL in Discord/Slack.")
    return {"sent": True}


# ---------------------------------------------------------------------------------------------------------
# Demo helpers (PLANIT_DEV=1): trigger the lightning countdown or a briefing without waiting for weather.
# ---------------------------------------------------------------------------------------------------------
if os.environ.get("PLANIT_DEV") == "1":
    from backend.worker import JOBS

    @app.post("/dev/lightning/{site_id}")
    def fake_strike(site_id: int, miles_away: float = Query(3.0, ge=0, le=10), user=Depends(require_supervisor)):
        """Insert a strike `miles_away` north of the site and raise alerts, exactly like a real GLM strike."""
        with connection() as conn:
            since = conn.execute("SELECT now() - interval '1 second' AS t").fetchone()["t"]
            conn.execute("INSERT INTO lightning_strikes (observed_at, geog, source) "
                         "SELECT now(), ST_Project(geog, %s * 1609.344, 0), 'dev' FROM sites WHERE site_id = %s",
                         (miles_away, site_id))
            lightning.raise_alerts(conn, since)
        return active_alerts(site_id=site_id)

    @app.post("/dev/briefings/{site_id}")
    def briefing_now(site_id: int, user=Depends(require_supervisor)):
        """Write tomorrow's briefing for one site right now (normally 6 PM local)."""
        with connection() as conn:
            day = conn.execute("SELECT (now() AT TIME ZONE timezone)::date + 1 AS d FROM sites WHERE site_id = %s",
                               (site_id,)).fetchone()["d"]
            briefings.save(conn, site_id, day, *briefings.build(conn, site_id, day))
        return briefing(site_id, day)

    @app.post("/dev/jobs/{name}")
    def run_job(name: str, user=Depends(require_supervisor)):
        if name not in JOBS:
            raise HTTPException(404, f"Unknown job; one of {sorted(JOBS)}")
        with connection() as conn:
            return {"job": name, "result": JOBS[name](conn)}

    @app.post("/dev/scenario/{site_id}")
    def start_scenario(site_id: int, user=Depends(require_supervisor)):
        """Put the site into the brief's story: heat index 108°F, lightning 4 mi away, a week of rain, a gator
        report, and a stormy 112°F tomorrow with its briefing. Remove with DELETE."""
        with connection() as conn:
            result = scenario.start(conn, site_id)
        if result is None:
            raise HTTPException(404, "Not found")
        return {**result, "active_alerts": active_alerts(site_id=site_id)}

    @app.delete("/dev/scenario/{site_id}")
    def stop_scenario(site_id: int, user=Depends(require_supervisor)):
        with connection() as conn:
            return scenario.stop(conn, site_id)
