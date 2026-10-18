"""Create a least-privilege database user for the public API server.

    python -m db.create_api_role        # writes .env.api (git-ignored); copy its DATABASE_URL to the API host

The API user can read every table but write only the rows the API writes: crew reports, alerts, AI reports, users and
their notification channels. It can't create, alter, drop or truncate anything, so a bug or an injection in the API
can't damage the schema or the history. The worker, the ML scripts and db/setup_db.py keep the admin URL; they run on
a trusted machine. Re-running rotates the password.
"""
import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from db.setup_db import ROOT, database_url

ROLE = "planit_api"
WRITES = ["crew_reports", "site_alerts", "site_reports", "app_users", "notification_channels", "notification_log",
          "saved_locations", "location_briefings", "user_briefings", "economic_assumptions",
          "weather_observations", "weather_forecasts"]  # opening a site card refreshes its weather


def main():
    admin = database_url()
    password = secrets.token_urlsafe(24)
    r = sql.Identifier(ROLE)
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone()
        conn.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
            sql.SQL("ALTER" if exists else "CREATE"), r, sql.Literal(password)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(conn.info.dbname), r))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(r))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(r))
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
            sql.SQL(", ").join(map(sql.Identifier, WRITES)), r))
        conn.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(r))
        for (mv,) in conn.execute("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'").fetchall():
            conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(mv), r))
        # tables and views created later (new features) are readable too, or the live API fails with 500s
        conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(r))
        conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO {}").format(r))
    u = urlsplit(admin)
    url = urlunsplit(u._replace(netloc=f"{ROLE}:{password}@{u.hostname}:{u.port}"))
    (ROOT / ".env.api").write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    print(f"{ROLE} ready. Its DATABASE_URL is in .env.api: set it on the API host (not the worker). Never commit it.")


if __name__ == "__main__":
    main()
