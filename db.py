"""PostgreSQL (Neon) access, connection pooling, schema, defaults, and seed data."""
import os
from datetime import date
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Provide executemany on Connection for convenience/compatibility
if not hasattr(psycopg.Connection, "executemany"):
    def _conn_executemany(self, query, params_seq):
        with self.cursor() as cur:
            return cur.executemany(query, params_seq)
    psycopg.Connection.executemany = _conn_executemany

# (name, group). group "invest" = SIP, "save" = Savings; excluded from consumption spending.
CATEGORIES = [
    ("Rent", "spend"),
    ("SIP / Investment", "invest"),
    ("Transport", "spend"),
    ("Food", "spend"),
    ("Gaming", "spend"),
    ("Entertainment", "spend"),
    ("Groceries", "spend"),
    ("Savings", "save"),
]

DEFAULT_BUDGETS = {
    "Rent": 7000,
    "SIP / Investment": 3000,
    "Transport": 1000,
    "Food": 3500,
    "Gaming": 500,
    "Entertainment": 500,
    "Groceries": 1500,
    "Savings": 3000,
}

MONTHLY_BUDGET = int(os.environ.get("MONTHLY_BUDGET", 20000))

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);

CREATE TABLE IF NOT EXISTS month_budgets (
    month VARCHAR(7) NOT NULL,
    category VARCHAR(100) NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (month, category)
);
"""

_pools = {}


def get_db_url():
    """Retrieve DATABASE_URL from environment or raise an error."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Please configure DATABASE_URL with your PostgreSQL / Neon connection string."
        )
    return url


def get_pool():
    """Get or create a connection pool for the current process PID."""
    pid = os.getpid()
    pool = _pools.get(pid)
    if pool is None or pool.closed:
        pool = ConnectionPool(
            conninfo=get_db_url(),
            min_size=int(os.environ.get("DB_POOL_MIN", 1)),
            max_size=int(os.environ.get("DB_POOL_MAX", 10)),
            timeout=float(os.environ.get("DB_POOL_TIMEOUT", 30.0)),
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
        _pools[pid] = pool
    return pool


def close_pools():
    """Close all connection pools (for shutdown or tests)."""
    for pid, pool in list(_pools.items()):
        if not pool.closed:
            pool.close()
    _pools.clear()


def get_db():
    """Get a database connection from the pool.
    Within Flask request context, binds connection to flask.g so all queries in
    a single request share the same connection.
    """
    try:
        from flask import g, has_app_context
        if has_app_context():
            if "db" not in g:
                g.db = get_pool().getconn()
            return g.db
    except ImportError:
        pass

    return get_pool().getconn()


def close_db(_exc=None):
    """Clean up and return the connection to the pool at the end of the request."""
    try:
        from flask import g, has_app_context
        if not has_app_context():
            return
        conn = g.pop("db", None)
    except ImportError:
        conn = None

    if conn is not None:
        try:
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.rollback()
        except Exception:
            pass
        finally:
            pid = os.getpid()
            pool = _pools.get(pid)
            if pool and not pool.closed:
                pool.putconn(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass


def ensure_month(db, month):
    """New month gets default budgets; existing months are never touched."""
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM month_budgets WHERE month = %s LIMIT 1", (month,))
        if not cur.fetchone():
            cur.executemany(
                "INSERT INTO month_budgets (month, category, amount) VALUES (%s, %s, %s) "
                "ON CONFLICT (month, category) DO NOTHING",
                [(month, c, DEFAULT_BUDGETS[c]) for c, _ in CATEGORIES],
            )
            db.commit()


def init_db():
    """Initialize tables and insert initial seed if brand-new database."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(SCHEMA)
        db.commit()

        # Seed only on a brand-new database (no month has ever been created).
        cur.execute("SELECT 1 FROM month_budgets LIMIT 1")
        if not cur.fetchone():
            today = date.today()
            month = today.strftime("%Y-%m")
            ensure_month(db, month)
            first = today.replace(day=1).isoformat()
            cur.executemany(
                "INSERT INTO expenses (date, description, category, amount, notes) VALUES (%s, %s, %s, %s, %s)",
                [
                    (first, "Rent", "Rent", 7000, ""),
                    (today.isoformat(), "Netflix", "Entertainment", 199, ""),
                    (today.isoformat(), "BGMI UC", "Gaming", 344, ""),
                    (today.isoformat(), "Tea", "Food", 20, ""),
                    (today.isoformat(), "Gift for sister", "Entertainment", 577, ""),
                ],
            )
            db.commit()
