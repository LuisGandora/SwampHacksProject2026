"""Database regression fixtures; every change is rolled back. Run explicitly with python -m db.test_alert_consistency."""
import datetime as dt

import psycopg
from psycopg.rows import dict_row

from backend import rivers, reports
from db.setup_db import database_url
from db.upgrade_alert_consistency import apply


def main():
    with psycopg.connect(database_url(), connect_timeout=10, row_factory=dict_row) as conn:
        with conn.transaction(force_rollback=True):
            apply(conn)
            site = conn.execute("SELECT site_id FROM sites ORDER BY site_id LIMIT 1").fetchone()["site_id"]
            conn.execute("UPDATE site_alerts SET cleared_at = now() WHERE site_id = %s", (site,))
            conn.execute("UPDATE crew_reports SET status = 'dismissed' WHERE site_id = %s", (site,))
            now = dt.datetime.now(dt.timezone.utc)
            call = "SELECT raise_site_alert(%s, 'flooding', %s, %s, NULL, %s, %s)"
            conn.execute(call, (site, "stop_work", now, "Major flooding · also: Crew report: water",
                                now + dt.timedelta(hours=2)))
            floor = conn.execute("SELECT floor FROM site_live_floor WHERE site_id = %s", (site,)).fetchone()["floor"]
            assert floor >= 8, floor
            conn.execute("UPDATE site_alerts SET cleared_at = now() WHERE site_id = %s", (site,))
            conn.execute(call, (site, "warning", now, "Action stage (prepare; flooding not confirmed) at Test River",
                                now + dt.timedelta(hours=2)))
            floor = conn.execute("SELECT floor FROM site_live_floor WHERE site_id = %s", (site,)).fetchone()["floor"]
            assert float(floor) == 3.5, floor
            # Exercise report collection and the cache query with the real schema, without an LLM call.
            facts = reports.gather(conn, site)
            assert facts["facts_version"] == reports.FACTS_VERSION
            assert reports.template(facts)["summary"]
            reports.cached(conn, site, "safety")

            # Use the production river query over isolated fixtures; capture emissions instead of raising live alerts.
            conn.execute("CREATE TEMP TABLE sites (site_id bigint, geog geography)")
            conn.execute("CREATE TEMP TABLE river_gauges (gauge_id text, name text, geog geography)")
            conn.execute("""CREATE TEMP TABLE river_gauge_readings (gauge_id text, observed_at timestamptz,
                flood_category text, forecast_flood_category text, stage_ft numeric)""")
            conn.execute("CREATE TEMP TABLE captured (level text, message text)")
            conn.execute("""CREATE FUNCTION pg_temp.capture(bigint, text, text, timestamptz, numeric, text, timestamptz)
                RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN
                    INSERT INTO captured VALUES ($3, $6); RETURN true; END $$""")
            conn.execute("INSERT INTO sites VALUES (1, ST_MakePoint(-81, 29)::geography)")
            conn.execute("""INSERT INTO river_gauges VALUES
                ('major', 'Test coastal gauge (MHHW)', ST_MakePoint(-81, 29)::geography),
                ('action', 'Test River', ST_MakePoint(-81, 29)::geography)""")
            conn.execute("""INSERT INTO river_gauge_readings VALUES
                ('major', now(), 'major', NULL, 16), ('action', now(), 'action', NULL, 2)""")
            conn.execute(rivers.RAISE.replace("raise_site_alert(", "pg_temp.capture("),
                         {"flooding": list(rivers.FLOODING)})
            emitted = conn.execute("SELECT * FROM captured ORDER BY level").fetchall()
            assert len(emitted) == 2, emitted
            assert any(r["level"] == "stop_work" and "Major flooding" in r["message"] for r in emitted), emitted
            assert any(r["level"] == "warning" and "Action stage" in r["message"] for r in emitted), emitted
            assert all("not confirmed site flooding" in r["message"] for r in emitted), emitted
            print("Database checks passed: upgrade, floors, report facts/cache, major/action-stage rivers (all rolled back).")


if __name__ == "__main__":
    main()
