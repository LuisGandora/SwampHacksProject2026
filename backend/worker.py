"""Scheduler for live data. Run it as its own process (one copy only):

    python -m backend.worker                 # run on schedule
    python -m backend.worker alerts points   # run named jobs once and exit

Schedule (see main): lightning and notifications every minute, NWS alerts every 2 min, station observations
every 10 min, river gauges, tides, fires and crew dispatches (utility outage maps) every 15 min, wildlife every 30 min, forecasts hourly, 6 PM briefings
every 15 min, site grid lookups daily (only sites not looked up yet).
"""
import datetime as dt
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from db.setup_db import load_env

load_env()  # .env -> os.environ (WEBHOOK_KEY, GEMINI_API_KEY, ...) before the jobs read them, as the API does

from backend import briefings, crew_dispatch, daily_weather, fires, lightning, notify, nws, observations, rivers, tides, wildlife
from ml import hazard_model
from backend.db import connection, pool

JOBS = {
    "points": nws.sync_points,
    "alerts": nws.sync_alerts,
    "forecasts": nws.sync_forecasts,
    "observations": observations.sync,
    "rivers": rivers.sync,
    "tides": tides.sync,
    "crew_dispatch": crew_dispatch.sync,
    "lightning": lightning.sync,
    "wildlife": wildlife.sync,
    "fires": fires.sync,
    "briefings": briefings.run_due,
    "notify": notify.sync,
    "scores": hazard_model.score,
    "daily_weather": daily_weather.sync,
}


RECORD = ("INSERT INTO worker_runs (job, last_run_at, last_success_at, last_error, detail) VALUES (%s, now(), {ok}, %s, %s) "
          "ON CONFLICT (job) DO UPDATE SET last_run_at = now(), last_error = EXCLUDED.last_error, detail = EXCLUDED.detail"
          "{keep}")


def run(name):
    """Run one job and record the outcome in worker_runs (GET /data-status reads it)."""
    t = time.time()
    with connection() as conn:  # mark it started, so a long first run (forecasts: several minutes) reads "starting"
        conn.execute("INSERT INTO worker_runs (job, last_run_at) VALUES (%s, now()) "
                     "ON CONFLICT (job) DO UPDATE SET last_run_at = now()", (name,))
    try:
        with connection() as conn:
            result = JOBS[name](conn)
            conn.execute(RECORD.format(ok="now()", keep=", last_success_at = now()"), (name, None, str(result)[:300]))
    except Exception as e:
        with connection() as conn:  # a fresh connection: the job's transaction was rolled back
            conn.execute(RECORD.format(ok="NULL", keep=""), (name, f"{type(e).__name__}: {str(e)[:200]}", None))
        raise
    print(f"[{dt.datetime.now():%H:%M:%S}] {name}: {result} ({time.time() - t:.0f}s)", flush=True)
    return result


def main():
    if sys.argv[1:]:
        try:
            for name in sys.argv[1:]:
                run(name)
        finally:
            pool.close()
        return
    now = dt.datetime.now()
    s = BlockingScheduler()
    s.add_job(run, "interval", days=1, args=["points"], next_run_time=now)
    s.add_job(run, "interval", minutes=1, args=["lightning"], next_run_time=now)
    s.add_job(run, "interval", minutes=2, args=["alerts"], next_run_time=now)
    s.add_job(run, "interval", minutes=10, args=["observations"], next_run_time=now)
    s.add_job(run, "interval", minutes=15, args=["rivers"], next_run_time=now)
    s.add_job(run, "interval", minutes=15, args=["tides"], next_run_time=now)
    s.add_job(run, "interval", minutes=15, args=["crew_dispatch"], next_run_time=now + dt.timedelta(minutes=3))
    s.add_job(run, "interval", minutes=15, args=["fires"], next_run_time=now)
    s.add_job(run, "interval", minutes=30, args=["wildlife"], next_run_time=now)
    s.add_job(run, "cron", minute=5, args=["forecasts"], next_run_time=now + dt.timedelta(minutes=1))
    s.add_job(run, "cron", hour=11, minute=30, args=["daily_weather"])  # ~7:30 AM Eastern, yesterday is final
    s.add_job(run, "cron", minute=20, args=["scores"], next_run_time=now + dt.timedelta(minutes=2))  # after forecasts
    s.add_job(run, "cron", minute="*/15", args=["briefings"], next_run_time=now + dt.timedelta(minutes=1))
    s.add_job(run, "interval", minutes=1, args=["notify"], next_run_time=now + dt.timedelta(seconds=30))
    print("Worker running. Ctrl+C to stop.", flush=True)
    s.start()


if __name__ == "__main__":
    main()
