"""Scheduler for live data. Run it as its own process (one copy only):

    python -m backend.worker                 # run on schedule
    python -m backend.worker alerts points   # run named jobs once and exit

Schedule: lightning every minute, NWS alerts every 2 min, forecasts hourly, 6 PM briefings every 15 min,
site grid lookups daily (only sites not looked up yet).
"""
import datetime as dt
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from backend import briefings, lightning, nws
from backend.db import connection, pool

JOBS = {
    "points": nws.sync_points,
    "alerts": nws.sync_alerts,
    "forecasts": nws.sync_forecasts,
    "lightning": lightning.sync,
    "briefings": briefings.run_due,
}


def run(name):
    t = time.time()
    with connection() as conn:
        result = JOBS[name](conn)
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
    s.add_job(run, "cron", minute=5, args=["forecasts"], next_run_time=now + dt.timedelta(minutes=1))
    s.add_job(run, "cron", minute="*/15", args=["briefings"])
    print("Worker running. Ctrl+C to stop.", flush=True)
    s.start()


if __name__ == "__main__":
    main()
