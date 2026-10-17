"""Load the static climatology Hazard Index (data/hazard_scores.csv) into the hazard_scores table.

This takes the per-plant 0-10 scores produced by hardzardModel.py and writes them to the database so the
API and website display them. Each plant gets both horizons ('now' and 'next_day') with the same value,
because this score is a long-term climatology risk, not a per-day forecast. category_scores is left NULL
(we have no per-category breakdown), so the site card's "what drives the score" section stays empty.

Rows are tagged model_version = 'climatology-csv-v1' so they're easy to find or remove later. They use a
scored_for of now() / now()+1 day so they beat any existing forecast rows in the API's "latest" query.

    python -m ml.load_csv_scores
"""
import csv
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from db.setup_db import database_url

CSV = Path(__file__).resolve().parent.parent / "data" / "hazard_scores.csv"
MODEL_VERSION = "climatology-csv-v1"


def main():
    scores = {}
    with open(CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            scores[int(r["plant_id"])] = float(r["hazard_index"])
    print(f"Read {len(scores)} scores from {CSV.name}")

    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        valid = {r["site_id"] for r in conn.execute("SELECT site_id FROM sites")}
        rows = []
        for site_id, score in scores.items():
            if site_id not in valid:
                continue  # skip plants that aren't loaded as sites
            for horizon, offset in (("now", "0 days"), ("next_day", "1 day")):
                rows.append((site_id, offset, horizon, score, MODEL_VERSION))

        conn.cursor().executemany(
            "INSERT INTO hazard_scores (site_id, scored_for, horizon, score, category_scores, model_version) "
            "VALUES (%s, now() + %s::interval, %s, %s, NULL, %s) "
            "ON CONFLICT (site_id, scored_for, horizon) DO UPDATE SET score = EXCLUDED.score, "
            "category_scores = EXCLUDED.category_scores, model_version = EXCLUDED.model_version, created_at = now()",
            rows,
        )
        conn.commit()

        check = conn.execute(
            "SELECT horizon, count(*) n, min(score) mn, max(score) mx FROM hazard_scores "
            "WHERE model_version = %s GROUP BY horizon", (MODEL_VERSION,)).fetchall()
    print(f"Wrote {len(rows)} rows for {len(rows)//2} sites:")
    for r in check:
        print(f"  {r['horizon']:9} n={r['n']} score {r['mn']}-{r['mx']}")


if __name__ == "__main__":
    main()
