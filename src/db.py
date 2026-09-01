"""
Lightweight SQLite storage layer.

Keeping this simple on purpose — swap for Postgres/cloud warehouse later
without changing the fetcher scripts, since they only talk to these
functions, never to SQL directly.
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "adrevenue.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS financials (
    ticker TEXT NOT NULL,
    period_end DATE NOT NULL,
    period_type TEXT NOT NULL,       -- 'quarterly' or 'annual'
    total_revenue REAL,
    net_income REAL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, period_end, period_type)
);

CREATE TABLE IF NOT EXISTS macro (
    series_id TEXT NOT NULL,
    obs_date DATE NOT NULL,
    value REAL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (series_id, obs_date)
);

CREATE TABLE IF NOT EXISTS sec_filings (
    ticker TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    form_type TEXT,
    filing_date DATE,
    report_date DATE,
    primary_doc_url TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, accession_number)
);

CREATE TABLE IF NOT EXISTS trends (
    keyword TEXT NOT NULL,
    obs_date DATE NOT NULL,
    interest_score REAL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (keyword, obs_date)
);

CREATE TABLE IF NOT EXISTS pipeline_log (
    run_id TEXT,
    step TEXT,
    status TEXT,
    rows_written INTEGER,
    message TEXT,
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    # WAL mode lets one writer + readers coexist without "database is
    # locked" errors - needed because log_step() opens its own connection
    # while a fetcher's connection may still be open.
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    print(f"Database ready at {DB_PATH}")


def log_step(run_id: str, step: str, status: str, rows_written: int = 0, message: str = "", conn=None):
    """
    Logs a pipeline step. Pass an existing `conn` when calling this from
    inside a function that already holds an open connection (e.g. inside
    a `with get_conn() as conn:` block) - opening a second connection
    there caused "database is locked" errors, since the first connection
    still holds an uncommitted write transaction at that point.
    """
    if conn is not None:
        conn.execute(
            "INSERT INTO pipeline_log (run_id, step, status, rows_written, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, step, status, rows_written, message),
        )
    else:
        with get_conn() as new_conn:
            new_conn.execute(
                "INSERT INTO pipeline_log (run_id, step, status, rows_written, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, step, status, rows_written, message),
            )


if __name__ == "__main__":
    init_db()
