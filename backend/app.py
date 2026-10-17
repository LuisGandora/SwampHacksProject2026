"""PlanIT Control API.

    uvicorn backend.app:app --reload        # from the project root; docs at http://127.0.0.1:8000/docs

Set PLANIT_DEV=1 to enable the /dev endpoints (fake lightning strike, run a job, write a briefing now).
"""
import datetime as dt
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import briefings, lightning
from backend.db import connection

app = FastAPI(title="PlanIT Control API")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])

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
    return one("SELECT now() AS db_time, (SELECT count(*) FROM sites) AS sites")


@app.get("/hazard-categories")
def hazard_categories():
    """Thresholds, radii and countdowns per category (the frontend's legend)."""
    return many("SELECT * FROM hazard_categories ORDER BY kind, category")


@app.get("/sites")
def sites(state: str | None = None, limit: int = Query(100, le=1000)):
    """Dashboard ranking: latest hazard score first, then active stop-work and warning alerts."""
    return many(f"""
        SELECT s.site_id, s.name, s.operator, s.state, s.county, s.timezone, s.capacity_mw, s.primary_technology,
               {LATLON.format('s.geog')}, hs.score, hs.horizon, hs.scored_for, hs.category_scores,
               count(a.alert_id) FILTER (WHERE a.level = 'stop_work') AS stop_work_alerts,
               count(a.alert_id) FILTER (WHERE a.level = 'warning')   AS warning_alerts
          FROM sites s
          LEFT JOIN LATERAL (SELECT * FROM hazard_scores h WHERE h.site_id = s.site_id
                              ORDER BY scored_for DESC, created_at DESC LIMIT 1) hs ON true
          LEFT JOIN active_site_alerts a ON a.site_id = s.site_id
         WHERE %(state)s::text IS NULL OR s.state = upper(%(state)s)
         GROUP BY s.site_id, hs.score, hs.horizon, hs.scored_for, hs.category_scores
         ORDER BY hs.score DESC NULLS LAST, stop_work_alerts DESC, warning_alerts DESC, s.capacity_mw DESC NULLS LAST
         LIMIT %(limit)s""", {"state": state, "limit": limit})


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


class CrewReport(BaseModel):
    category: str = Field(examples=["wildlife"])
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    description: str | None = None
    photo_url: str | None = None          # upload the photo to storage first, send its URL here
    reported_by: str | None = None
    severity: int | None = Field(None, ge=1, le=5)
    site_id: int | None = None            # defaults to the nearest site within 10 miles


@app.post("/crew-reports", status_code=201)
def create_crew_report(r: CrewReport):
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM hazard_categories WHERE category = %s", (r.category,)).fetchone():
            raise HTTPException(422, f"Unknown category {r.category!r}; see GET /hazard-categories")
        return conn.execute(
            f"""INSERT INTO crew_reports (site_id, category, description, photo_url, geog, reported_by, severity)
                VALUES (coalesce(%(site_id)s, (SELECT site_id FROM sites
                                               WHERE ST_DWithin(geog, ST_MakePoint(%(lon)s, %(lat)s)::geography, 16093)
                                               ORDER BY geog <-> ST_MakePoint(%(lon)s, %(lat)s)::geography LIMIT 1)),
                        %(category)s, %(description)s, %(photo_url)s, ST_MakePoint(%(lon)s, %(lat)s)::geography,
                        %(reported_by)s, %(severity)s)
                RETURNING report_id, reported_at, site_id, category, status, {LATLON.format('geog')}""",
            r.model_dump()).fetchone()


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
    def run_job(name: Literal["points", "alerts", "forecasts", "lightning", "briefings"]):
        with connection() as conn:
            return {"job": name, "result": JOBS[name](conn)}
