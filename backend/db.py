"""Shared connection pool for the API and the worker. The free Tiger tier has no pooler, so keep it small."""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from db.setup_db import database_url

pool = ConnectionPool(database_url(), min_size=1, max_size=5, kwargs={"row_factory": dict_row}, open=False)


def connection():
    """`with connection() as conn:` commits on success, rolls back on error."""
    pool.open()  # no-op once open
    return pool.connection()
