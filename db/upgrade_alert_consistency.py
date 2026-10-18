"""Apply only the dashboard alert-rule upgrade, without reloading site/history data.

python -m db.upgrade_alert_consistency --check  # validate in a rolled-back transaction
python -m db.upgrade_alert_consistency          # apply atomically; safe to repeat
"""
import argparse
from pathlib import Path

import psycopg

from db.setup_db import database_url


def apply(conn):
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    start = schema.index("-- Upgrade the old defaults")
    end = schema.index("-- =================================================================", start)
    conn.execute(schema[start:end])
    start = schema.index("CREATE OR REPLACE FUNCTION raise_site_alert")
    end = schema.index("END $$;", start) + len("END $$;")
    conn.execute(schema[start:end])
    start = schema.index("CREATE VIEW site_live_floor AS")
    end = schema.index("ORDER BY site_id, floor DESC;", start) + len("ORDER BY site_id, floor DESC;")
    conn.execute(schema[start:end].replace("CREATE VIEW", "CREATE OR REPLACE VIEW", 1))
    # Existing stored classifications must follow the rule edit too, including briefing/report queries.
    conn.execute("""
        UPDATE nws_alerts a SET category = (
            SELECT category FROM hazard_categories
             WHERE a.event = ANY (nws_warn_events || nws_stop_events) LIMIT 1)
         WHERE a.expires_at > now()
    """)
    cleared = conn.execute("""
        UPDATE site_alerts SET cleared_at = now()
         WHERE cleared_at IS NULL AND (
             message LIKE 'Rip Current Statement%%' OR message LIKE 'Beach Hazards Statement%%'
             OR message LIKE 'Lake Wind Advisory%%' OR message ILIKE '%%has been cancelled%%')
    """).rowcount
    conn.execute("""
        UPDATE nws_alerts SET expires_at = least(expires_at, now())
         WHERE expires_at > now() AND headline ILIKE '%%has been cancelled%%'
    """)
    conn.execute("""
        UPDATE site_alerts SET message = CASE
            WHEN message LIKE 'Action flooding forecast at %%'
                THEN replace(message, 'Action flooding forecast at ', 'Action stage forecast (prepare; flooding not confirmed) at ')
            ELSE replace(message, 'Action flooding at ', 'Action stage (prepare; flooding not confirmed) at ') END
         WHERE cleared_at IS NULL AND category = 'flooding' AND message LIKE 'Action flooding%%'
    """)
    # Shorten only the legacy wildlife window. Crew records themselves remain available for review.
    conn.execute("""
        UPDATE site_alerts SET all_clear_at = least(all_clear_at,
            CASE WHEN message LIKE 'Crew report:%%' THEN last_trigger_at + interval '12 hours'
                 ELSE last_trigger_at + interval '3 days' END)
         WHERE cleared_at IS NULL AND category = 'wildlife'
    """)
    return cleared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with psycopg.connect(database_url(), connect_timeout=10) as conn:
        with conn.transaction(force_rollback=args.check):
            cleared = apply(conn)
            print(f"Alert rules {'validated (rolled back)' if args.check else 'applied'}; {cleared} obsolete alerts cleared.")


if __name__ == "__main__":
    main()
