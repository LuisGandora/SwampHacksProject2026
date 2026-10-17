"""PlanIT Control API.

    uvicorn backend.app:app --reload        # from the project root; docs at http://127.0.0.1:8000/docs

Set PLANIT_DEV=1 to enable the /dev endpoints (demo scenario, fake lightning strike, run a job, briefing now).
Never set it on a public URL: they write to the shared database.
"""
import datetime as dt
import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import briefings, lightning, reports, scenario
from backend.db import connection

app = FastAPI(title="PlanIT Control API")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])

# Crew photos are stored on this server's disk and served from /uploads.
# ponytail: local disk only, switch to S3/R2 when the API runs on more than one machine.
UPLOADS = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
MAX_PHOTO_BYTES = 10 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}

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
    """Dashboard ranking by hazard index for the chosen horizon (today or tomorrow), then active stop-work and
    warning alerts. Each site also carries the other horizon's score and its top hazard category."""
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
               s.coord_check, {LATLON.format('s.geog')},
               h.score, %(horizon)s AS horizon, h.scored_for, h.category_scores,
               n.score AS score_now, t.score AS score_next_day,
               (SELECT k FROM jsonb_each(h.category_scores) e(k, v) WHERE k <> '_meta'
                 ORDER BY (v ->> 'score')::numeric DESC LIMIT 1) AS top_category,
               coalesce(a.stop_work_alerts, 0) AS stop_work_alerts, coalesce(a.warning_alerts, 0) AS warning_alerts
          FROM sites s
          LEFT JOIN latest h ON h.site_id = s.site_id AND h.horizon = %(horizon)s
          LEFT JOIN latest n ON n.site_id = s.site_id AND n.horizon = 'now'
          LEFT JOIN latest t ON t.site_id = s.site_id AND t.horizon = 'next_day'
          LEFT JOIN alerts a ON a.site_id = s.site_id
         WHERE %(state)s::text IS NULL OR s.state = upper(%(state)s)
         ORDER BY h.score DESC NULLS LAST, stop_work_alerts DESC, warning_alerts DESC, s.capacity_mw DESC NULLS LAST
         LIMIT %(limit)s""", {"state": state, "horizon": horizon, "limit": limit})


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
    return {**s, "risk_profile": profile[0] if profile else None,
            "active_alerts": many("SELECT * FROM active_site_alerts WHERE site_id = %s ORDER BY level DESC, started_at",
                                  (site_id,)),
            "latest_score": next(iter(many("SELECT * FROM hazard_scores WHERE site_id = %s "
                                           "ORDER BY scored_for DESC, created_at DESC LIMIT 1", (site_id,))), None)}


@app.get("/sites/{site_id}/forecast")
def forecast(site_id: int, hours: int = Query(48, le=48)):
    """Hourly NWS forecast for the site (latest issue), from now on."""
    return many("SELECT valid_at, temp_f, heat_index_f, wind_chill_f, precip_chance, precip_in, wind_gust_mph, "
                "thunder_chance, visibility_mi, short_forecast, issued_at FROM weather_forecasts "
                "WHERE site_id = %s AND valid_at >= date_trunc('hour', now()) "
                "AND valid_at < now() + make_interval(hours => %s) ORDER BY valid_at", (site_id, hours))


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
def site_report(site_id: int, refresh: bool = False):
    """On-tap safety report for one site: facts computed in code, explained by Gemini (or a template fallback).
    Cached for 30 minutes unless refresh=true. Check `verified`: false means the model wrote a number that isn't
    in the facts (listed in unverified_numbers)."""
    with connection() as conn:
        r = reports.generate(conn, site_id, refresh)
    if r is None:
        raise HTTPException(404, "Not found")
    return r


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


@app.get("/economics/correlations")
def economic_correlations(significant_only: bool = True):
    """Weather vs demand/sales correlations for every company (deseasonalized and raw)."""
    return many("SELECT * FROM economic_correlations WHERE NOT %s OR p_value < 0.05 ORDER BY deseasonalized DESC, "
                "abs(r) DESC", (significant_only,))


@app.post("/uploads", status_code=201)
async def upload_photo(file: UploadFile, request: Request):
    """Upload a crew photo (JPEG, PNG, WebP or HEIC, up to 10 MB). Send the returned url as photo_url."""
    ext = PHOTO_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(415, f"Unsupported type {file.content_type!r}; send one of {sorted(PHOTO_TYPES)}")
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is larger than 10 MB")
    name = f"{uuid.uuid4().hex}{ext}"
    (UPLOADS / name).write_bytes(data)
    return {"url": str(request.url_for("uploads", path=name))}


class CrewReport(BaseModel):
    category: str = Field(examples=["wildlife"])
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    description: str | None = None
    photo_url: str | None = None          # from POST /uploads
    reported_by: str | None = None
    severity: int | None = Field(None, ge=1, le=5)
    site_id: int | None = None            # defaults to the nearest site within 10 miles


@app.post("/crew-reports", status_code=201)
def create_crew_report(r: CrewReport):
    """Save a crew hazard report and alert the site right away (warning, open for 12 hours)."""
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM hazard_categories WHERE category = %s", (r.category,)).fetchone():
            raise HTTPException(422, f"Unknown category {r.category!r}; see GET /hazard-categories")
        report = conn.execute(
            f"""INSERT INTO crew_reports (site_id, category, description, photo_url, geog, reported_by, severity)
                VALUES (coalesce(%(site_id)s, (SELECT site_id FROM sites
                                               WHERE ST_DWithin(geog, ST_MakePoint(%(lon)s, %(lat)s)::geography, 16093)
                                               ORDER BY geog <-> ST_MakePoint(%(lon)s, %(lat)s)::geography LIMIT 1)),
                        %(category)s, %(description)s, %(photo_url)s, ST_MakePoint(%(lon)s, %(lat)s)::geography,
                        %(reported_by)s, %(severity)s)
                RETURNING report_id, reported_at, site_id, category, status, {LATLON.format('geog')}""",
            r.model_dump()).fetchone()
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


# ---------------------------------------------------------------------------------------------------------
# Demo helpers (PLANIT_DEV=1): trigger the lightning countdown or a briefing without waiting for weather.
# ---------------------------------------------------------------------------------------------------------
if os.environ.get("PLANIT_DEV") == "1":
    from backend.worker import JOBS

    @app.post("/dev/lightning/{site_id}")
    def fake_strike(site_id: int, miles_away: float = 3.0):
        """Insert a strike `miles_away` north of the site and raise alerts, exactly like a real GLM strike."""
        with connection() as conn:
            since = conn.execute("SELECT now() - interval '1 second' AS t").fetchone()["t"]
            conn.execute("INSERT INTO lightning_strikes (observed_at, geog, source) "
                         "SELECT now(), ST_Project(geog, %s * 1609.344, 0), 'dev' FROM sites WHERE site_id = %s",
                         (miles_away, site_id))
            lightning.raise_alerts(conn, since)
        return active_alerts(site_id=site_id)

    @app.post("/dev/briefings/{site_id}")
    def briefing_now(site_id: int):
        """Write tomorrow's briefing for one site right now (normally 6 PM local)."""
        with connection() as conn:
            day = conn.execute("SELECT (now() AT TIME ZONE timezone)::date + 1 AS d FROM sites WHERE site_id = %s",
                               (site_id,)).fetchone()["d"]
            briefings.save(conn, site_id, day, *briefings.build(conn, site_id, day))
        return briefing(site_id, day)

    @app.post("/dev/jobs/{name}")
    def run_job(name: str):
        if name not in JOBS:
            raise HTTPException(404, f"Unknown job; one of {sorted(JOBS)}")
        with connection() as conn:
            return {"job": name, "result": JOBS[name](conn)}

    @app.post("/dev/scenario/{site_id}")
    def start_scenario(site_id: int):
        """Put the site into the brief's story: heat index 108°F, lightning 4 mi away, a week of rain, a gator
        report, and a stormy 112°F tomorrow with its briefing. Remove with DELETE."""
        with connection() as conn:
            result = scenario.start(conn, site_id)
        if result is None:
            raise HTTPException(404, "Not found")
        return {**result, "active_alerts": active_alerts(site_id=site_id)}

    @app.delete("/dev/scenario/{site_id}")
    def stop_scenario(site_id: int):
        with connection() as conn:
            return scenario.stop(conn, site_id)
