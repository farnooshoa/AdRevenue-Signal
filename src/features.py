"""
Feature engineering for AdRevenue Signal.

Builds a single feature table joining:
  - financials (QoQ/YoY revenue growth per ticker)
  - macro indicators (as-of each period)
  - trends (search interest, aggregated to quarterly)

Calls qa_gate() first - this is exactly the kind of downstream script the
governance boundary exists for. If the data hasn't passed QA, this module
refuses to build features on top of it.
"""
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.db import get_conn
from src.qa_checks import qa_gate, QAGateError

FEATURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    ticker TEXT NOT NULL,
    period_end DATE NOT NULL,
    total_revenue REAL,
    revenue_qoq_growth REAL,     -- (this quarter - prior quarter) / prior quarter
    revenue_yoy_growth REAL,     -- (this quarter - same quarter last year) / same quarter last year
    revenue_qoq_growth_deseasonalized REAL,  -- qoq growth minus this ticker's historical mean growth for that fiscal quarter
    fiscal_quarter INTEGER,      -- 1-4, derived from period_end month
    built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, period_end)
);
"""


def init_features_table():
    with get_conn() as conn:
        conn.executescript(FEATURES_SCHEMA)


def fiscal_quarter_from_date(date_str: str) -> int:
    month = int(date_str.split("-")[1])
    return (month - 1) // 3 + 1


def build_revenue_features(conn) -> int:
    """QoQ / YoY growth + fiscal quarter, per ticker, from `financials`.

    Also computes a deseasonalized QoQ growth: raw growth minus this
    ticker's historical average growth for that fiscal quarter. Needed
    because raw QoQ growth carries a strong seasonal signal (e.g. a
    recurring Q4 lift) that swamps any other relationship you might try
    to correlate against it - confirmed by testing H1 against raw growth
    and finding no signal purely because seasonality dominated the
    variance, not because the underlying relationship didn't exist.
    """
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM financials").fetchall()]
    rows_written = 0

    for ticker in tickers:
        history = conn.execute(
            "SELECT period_end, total_revenue FROM financials "
            "WHERE ticker = ? AND total_revenue IS NOT NULL ORDER BY period_end",
            (ticker,)
        ).fetchall()

        # Pass 1: compute raw qoq/yoy growth + fiscal quarter for every period
        computed = []
        for i, (period_end, revenue) in enumerate(history):
            qoq_growth = None
            yoy_growth = None

            if i >= 1:
                prev_revenue = history[i - 1][1]
                if prev_revenue:
                    qoq_growth = (revenue - prev_revenue) / prev_revenue

            if i >= 4:
                yoy_revenue = history[i - 4][1]
                if yoy_revenue:
                    yoy_growth = (revenue - yoy_revenue) / yoy_revenue

            fq = fiscal_quarter_from_date(period_end)
            computed.append((period_end, revenue, qoq_growth, yoy_growth, fq))

        # Pass 2: this ticker's historical mean qoq growth per fiscal quarter
        growth_by_fq = {1: [], 2: [], 3: [], 4: []}
        for _, _, qoq_growth, _, fq in computed:
            if qoq_growth is not None:
                growth_by_fq[fq].append(qoq_growth)
        mean_growth_by_fq = {
            fq: (sum(v) / len(v) if v else None) for fq, v in growth_by_fq.items()
        }

        # Pass 3: write rows with deseasonalized growth = raw - this fiscal quarter's mean
        for period_end, revenue, qoq_growth, yoy_growth, fq in computed:
            deseasonalized = None
            if qoq_growth is not None and mean_growth_by_fq[fq] is not None:
                deseasonalized = qoq_growth - mean_growth_by_fq[fq]

            conn.execute(
                "INSERT OR REPLACE INTO features "
                "(ticker, period_end, total_revenue, revenue_qoq_growth, revenue_yoy_growth, "
                "revenue_qoq_growth_deseasonalized, fiscal_quarter) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, period_end, revenue, qoq_growth, yoy_growth, deseasonalized, fq)
            )
            rows_written += 1

    return rows_written


def get_quarterly_trend_series(conn, keyword: str) -> dict:
    """
    Aggregate weekly Google Trends data to quarterly averages.
    Returns {period_end_quarter_label: avg_interest_score}, e.g. {'2024-Q1': 42.5}
    """
    rows = conn.execute(
        "SELECT obs_date, interest_score FROM trends WHERE keyword = ? ORDER BY obs_date",
        (keyword,)
    ).fetchall()

    quarterly = {}
    for obs_date, score in rows:
        year, month, _ = obs_date.split("-")
        q = (int(month) - 1) // 3 + 1
        key = f"{year}-Q{q}"
        quarterly.setdefault(key, []).append(score)

    return {k: sum(v) / len(v) for k, v in quarterly.items()}


def run():
    print("Checking QA gate before building features...")
    try:
        qa_gate()
    except QAGateError as e:
        print(f"BLOCKED: {e}")
        print("Fix the failing check(s) before running feature engineering.")
        return 0

    print("QA gate passed. Building features...\n")
    init_features_table()

    with get_conn() as conn:
        rows_written = build_revenue_features(conn)

    print(f"Feature table built: {rows_written} rows written to `features`")
    return rows_written


if __name__ == "__main__":
    run()
