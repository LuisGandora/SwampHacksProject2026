"""Load data/us_live_sites.csv (scraper/us_live_sites.py) as live-only sites. Run only after the server runs code
that knows about sites.coverage (the model must not score these sites).

    python -m db.load_live_sites            # then: python -m backend.worker points forecasts observations alerts rivers tides

Adds the sites and their weather stations (existing sites are never changed), shortens weather retention to 8 days
so the database stays under the free tier, and pins Discord/Slack channels set to "every site" to the sites they
covered before (the Southeast study area), so they don't suddenly get alerts for the whole country.
"""
import csv
import json

import psycopg

from db.setup_db import DATA, database_url


def main():
    sites = list(csv.DictReader(open(DATA / "us_live_sites.csv", encoding="utf-8")))
    stations = json.loads((DATA / "us_live_stations.geojson").read_text())["features"]
    none = lambda v: v if v not in ("", None) else None
    with psycopg.connect(database_url()) as conn:
        conn.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS coverage text NOT NULL DEFAULT 'full' "
                     "CHECK (coverage IN ('full', 'live'))")
        conn.execute("UPDATE notification_channels SET site_ids = (SELECT array_agg(site_id) FROM sites "
                     "WHERE coverage = 'full') WHERE site_ids IS NULL")
        cur = conn.cursor()
        cur.executemany("INSERT INTO weather_stations VALUES (%s, %s, %s, %s, ST_MakePoint(%s, %s)::geography) "
                        "ON CONFLICT (station_id) DO NOTHING",
                        [(f["properties"]["sid"], f["properties"]["sname"], f["properties"]["network"],
                          f["properties"].get("wfo"), *f["geometry"]["coordinates"][:2]) for f in stations])
        cur.executemany(
            "INSERT INTO sites (site_id, name, operator, state, county, county_fips, timezone, capacity_mw, "
            "primary_technology, fuels, generators, geog, coord_source, coord_check, nws_office, weather_station_id, "
            "weather_station_mi, coverage) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "ST_MakePoint(%s, %s)::geography, 'eia860m', 'eia_only', %s, %s, %s, 'live') ON CONFLICT (site_id) DO NOTHING",
            [(int(s["eia_plant_id"]), s["name"], s["operator"], s["state"], none(s["county"]), none(s["county_fips"]),
              s["timezone"], none(s["capacity_mw"]), s["primary_technology"], s["fuels"], none(s["generators"]),
              float(s["lon"]), float(s["lat"]), none(s["nws_office"]), s["station"], none(s["station_distance_mi"]))
             for s in sites])
        for table in ("weather_observations", "weather_forecasts"):   # 2,700 sites x 30 days won't fit the free tier
            conn.execute(f"SELECT remove_retention_policy('{table}', if_exists => true)")
            conn.execute(f"SELECT add_retention_policy('{table}', INTERVAL '8 days')")
        conn.execute("UPDATE tide_stations SET updated_at = now() - interval '2 days'")  # re-list stations for new states
        conn.execute("REFRESH MATERIALIZED VIEW site_risk_profile")
        print(conn.execute("SELECT coverage, count(*) FROM sites GROUP BY 1 ORDER BY 1").fetchall())


if __name__ == "__main__":
    main()
