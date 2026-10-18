"""Load data/levees.geojson (scraper/levees.py, USACE National Levee Database) into levee_features.

    python -m db.load_levees       # replaces what's there; the site_levees view reads it
"""
import json

import psycopg

from db.setup_db import DATA, database_url


def main():
    features = json.loads((DATA / "levees.geojson").read_text())["features"]
    with psycopg.connect(database_url()) as conn:
        conn.execute("TRUNCATE levee_features")
        conn.cursor().executemany(
            "INSERT INTO levee_features (kind, system_id, system_name, props, geog) "
            "VALUES (%s, %s, %s, %s, ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))::geography)",
            [(f["properties"]["kind"], f["properties"].get("SYSTEM_ID"), f["properties"].get("SYSTEM_NAME"),
              json.dumps({k: v for k, v in f["properties"].items() if v is not None}), json.dumps(f["geometry"]))
             for f in features])
        print(conn.execute("SELECT kind, count(*) FROM levee_features GROUP BY 1 ORDER BY 1").fetchall())
        print("sites behind a levee:", conn.execute("SELECT count(*) FROM site_levees WHERE leveed_area_system IS NOT NULL").fetchone()[0],
              "| within 10 mi of one:", conn.execute("SELECT count(*) FROM site_levees WHERE nearest_levee_mi IS NOT NULL").fetchone()[0])


if __name__ == "__main__":
    main()
