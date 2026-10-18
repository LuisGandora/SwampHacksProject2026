"""Call every API endpoint as the restricted planit_api database user, exactly like the live server.

    python -m backend.smoke_api        # needs .env.api (python -m db.create_api_role); exit code 1 on any failure

Catches what local runs with the admin login can't: a table or view the API user isn't allowed to read (500s).
It writes one crew report and one saved location, then deletes them.
"""
import os, re, sys
from pathlib import Path
os.environ["DATABASE_URL"] = re.search(r"^DATABASE_URL=(.+)$", Path(".env.api").read_text(), re.M).group(1).strip()
from fastapi.testclient import TestClient
from backend import app as A
from backend.auth import current_user, with_identity
from backend.db import connection
with connection() as c:
    u = c.execute("SELECT * FROM app_users WHERE role = 'supervisor' ORDER BY created_at LIMIT 1").fetchone()
A.app.dependency_overrides[current_user] = lambda: with_identity(u, "supervisor", auth_on=True)
cl = TestClient(A.app, raise_server_exceptions=False)
site = 62923
ids = "57438,62923,136,7345"
gets = ["/health", "/data-status", "/hazard-categories", "/sites?horizon=now&limit=5000", "/sites?horizon=next_day&limit=5",
        f"/sites/{site}", f"/sites/{site}/weather", f"/sites/{site}/forecast?hours=48", f"/sites/{site}/hazard-events",
        f"/briefings/{site}", f"/sites/{site}/economics?weeks=8", f"/sites/{site}/contracts", "/alerts/active",
        "/lightning/recent?minutes=30", "/nws-alerts/map", "/hazard-pins?days=1095", "/economics/correlations",
        "/economics/contract-disruption", "/model/backtest", f"/crew-reports?site_id={site}&status=open",
        f"/crew-reports?site_id={site}&status=needs_review",
        *[f"/plan/{h}?limit=50&site_ids={ids}" for h in ("now", "week", "invest", "economics")], "/plan/economics",
        "/me", "/me/locations", "/me/channels", "/me/reports"]
bad = 0
for path in gets:
    r = cl.get(path)
    ok = r.status_code < 500 and r.status_code != 403
    bad += not ok
    if not ok or r.status_code >= 400:
        print(f"{'FAIL' if not ok else 'ok  '} {r.status_code} {path} {r.text[:120] if not ok else ''}")
# writes: a crew report, approve it, delete it; save and delete a location
rep = cl.post("/crew-reports", json={"category": "wildlife", "lat": 29.6436, "lon": -82.3549, "description": "smoke test", "site_id": 7345})
print("write crew report:", rep.status_code)
if rep.status_code < 300:
    rid = rep.json()["report_id"]
    print("approve:", cl.patch(f"/crew-reports/{rid}", json={"status": "open"}).status_code,
          "delete:", cl.delete(f"/crew-reports/{rid}").status_code)
loc = cl.post("/me/locations", json={"name": "smoke test", "lat": 29.65, "lon": -82.32, "radius_mi": 25})
print("save location:", loc.status_code, "delete:", cl.delete(f"/me/locations/{loc.json()['location_id']}").status_code if loc.status_code < 300 else "-")
print("assumption edit:", cl.put("/economics/assumptions/crew_size", json={"value": 4}).status_code)
with connection() as c:
    c.execute("UPDATE economic_assumptions SET note = 'ASSUMPTION: typical field crew; replace with company data' WHERE key = 'crew_size'")
print("GET failures:", bad, "of", len(gets))
sys.exit(1 if bad else 0)
