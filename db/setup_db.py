"""Apply db/schema.sql to the Tiger Data service and load the power plant sites.

Reads DATABASE_URL from .env (written during Tiger setup; never commit it). Safe to re-run.
"""
import csv
import datetime
import json
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_env():
    """Copy KEY=value lines from .env into os.environ (real environment variables win)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


def database_url():
    load_env()
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    raise SystemExit("DATABASE_URL not found in the environment or .env")


def load_sites(conn):
    plants = list(csv.DictReader(open(DATA / "power_plants.csv", encoding="utf-8")))
    links = {r["plant_id"]: r for r in csv.DictReader(open(DATA / "plant_stations.csv", encoding="utf-8"))}
    meta = {}
    for path in (DATA / "raw").glob("iem_*_ASOS.geojson"):
        for f in json.loads(path.read_text())["features"]:
            meta[f["properties"]["sid"]] = f
    used = {links[p["eia_plant_id"]]["station"] for p in plants if p["eia_plant_id"] in links}
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO weather_stations VALUES (%s, %s, %s, %s, ST_MakePoint(%s, %s)::geography)
               ON CONFLICT (station_id) DO UPDATE SET name = EXCLUDED.name, network = EXCLUDED.network,
                   nws_office = EXCLUDED.nws_office, geog = EXCLUDED.geog""",
            [(sid, meta[sid]["properties"]["sname"], meta[sid]["properties"]["network"],
              meta[sid]["properties"].get("wfo"), *meta[sid]["geometry"]["coordinates"]) for sid in sorted(used)])
        none = lambda v: v if v != "" else None
        rows = []
        for p in plants:
            if not p["lat"]:
                continue
            l = links.get(p["eia_plant_id"], {})
            rows.append((int(p["eia_plant_id"]), p["name"], p["operator"], p["state"], none(p["county"]),
                         none(p["county_fips"]), p["timezone"], none(p["capacity_mw"]), p["primary_technology"],
                         p["fuels"], none(p["generators"]), float(p["lon"]), float(p["lat"]), p["coord_source"],
                         none(p["osm_id"]), p["coord_check"], none(l.get("nws_office")),
                         none(l.get("fl_climate_region")), none(l.get("station")), none(l.get("station_distance_mi"))))
        cur.executemany(
            """INSERT INTO sites VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                        ST_MakePoint(%s, %s)::geography, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (site_id) DO UPDATE SET
                   name = EXCLUDED.name, operator = EXCLUDED.operator, state = EXCLUDED.state,
                   county = EXCLUDED.county, county_fips = EXCLUDED.county_fips, timezone = EXCLUDED.timezone,
                   capacity_mw = EXCLUDED.capacity_mw, primary_technology = EXCLUDED.primary_technology,
                   fuels = EXCLUDED.fuels, generators = EXCLUDED.generators, geog = EXCLUDED.geog,
                   coord_source = EXCLUDED.coord_source, osm_id = EXCLUDED.osm_id, coord_check = EXCLUDED.coord_check,
                   nws_office = EXCLUDED.nws_office, fl_climate_region = EXCLUDED.fl_climate_region,
                   weather_station_id = EXCLUDED.weather_station_id, weather_station_mi = EXCLUDED.weather_station_mi""",
            rows)
    return len(used), len(rows)


def check_lightning_countdown(conn):
    """Strikes at 0 and +20 min: one alert whose all-clear moves from +30 to +50 min. Rolled back."""
    t0 = datetime.datetime(2026, 8, 1, 14, 0, tzinfo=datetime.timezone.utc)
    with conn.transaction(force_rollback=True):
        site = conn.execute("SELECT site_id FROM sites LIMIT 1").fetchone()[0]
        call = "SELECT alert_id, all_clear_at FROM raise_site_alert(%s, 'lightning', 'stop_work', %s, 4.2)"
        a1 = conn.execute(call, (site, t0)).fetchone()
        a2 = conn.execute(call, (site, t0 + datetime.timedelta(minutes=20))).fetchone()
        assert a1[0] == a2[0], "second strike should extend the same alert"
        assert a1[1] == t0 + datetime.timedelta(minutes=30) and a2[1] == t0 + datetime.timedelta(minutes=50), (a1, a2)
        a3 = conn.execute(call, (site, t0 + datetime.timedelta(minutes=90))).fetchone()
        assert a3[0] != a1[0], "a strike after the all-clear should open a new alert"


def main():
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        print("Schema applied.")
        stations, sites = load_sites(conn)
        print(f"Loaded {sites} sites and {stations} weather stations.")
        check_lightning_countdown(conn)
        print("Lightning countdown check passed.")
        conn.execute("REFRESH MATERIALIZED VIEW site_risk_profile")
        n = conn.execute("SELECT count(*) FROM site_risk_profile").fetchone()[0]
        print(f"site_risk_profile refreshed: {n} rows (history columns fill in once events are loaded).")


if __name__ == "__main__":
    main()
