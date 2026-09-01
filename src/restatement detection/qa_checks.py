"""
Automated data quality checks for AdRevenue Signal.

Run after ingestion, before any downstream feature engineering or modeling
touches the data. Each check returns a QAResult; results are logged to
the `qa_results` table and printed as a summary.

Checks implemented:
  1. Missing values       - nulls in required fields
  2. Duplicate periods     - same ticker+period reported twice
  3. Outlier detection     - values far outside historical range (z-score)
  4. Staleness             - data older than expected refresh cadence
  5. Restatement detection - a previously-stored financial figure changed
"""
import sys
import uuid
import statistics
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.db import get_conn

QA_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_results (
    run_id TEXT,
    check_name TEXT,
    table_name TEXT,
    status TEXT,           -- 'pass', 'warn', 'fail'
    detail TEXT,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS restatements (
    ticker TEXT,
    period_end TEXT,
    field_name TEXT,
    old_value REAL,
    new_value REAL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass
class QAResult:
    check_name: str
    table_name: str
    status: str  # pass / warn / fail
    detail: str = ""


def init_qa_tables():
    with get_conn() as conn:
        conn.executescript(QA_SCHEMA)


# ---------- Check 1: Missing values ----------

def check_missing_values(conn) -> list[QAResult]:
    results = []
    checks = [
        ("financials", "total_revenue", "SELECT ticker, period_end FROM financials WHERE total_revenue IS NULL"),
        ("financials", "net_income", "SELECT ticker, period_end FROM financials WHERE net_income IS NULL"),
        ("macro", "value", "SELECT series_id, obs_date FROM macro WHERE value IS NULL"),
    ]
    for table, field_name, query in checks:
        rows = conn.execute(query).fetchall()
        if rows:
            results.append(QAResult(
                "missing_values", table, "warn",
                f"{len(rows)} null '{field_name}' values, e.g. {rows[:3]}"
            ))
        else:
            results.append(QAResult("missing_values", table, "pass", f"No nulls in '{field_name}'"))
    return results


# ---------- Check 2: Duplicate periods ----------

def check_duplicates(conn) -> list[QAResult]:
    results = []
    dupes = conn.execute("""
        SELECT ticker, period_end, period_type, COUNT(*) c
        FROM financials
        GROUP BY ticker, period_end, period_type
        HAVING c > 1
    """).fetchall()
    if dupes:
        results.append(QAResult("duplicates", "financials", "fail", f"{len(dupes)} duplicate periods found: {dupes[:3]}"))
    else:
        results.append(QAResult("duplicates", "financials", "pass", "No duplicate ticker/period rows"))
    return results


# ---------- Check 3: Outlier detection (robust z-score via MAD) ----------
#
# NOTE: an earlier version of this check used mean/stdev z-scores and
# FAILED to catch a planted outlier during testing, because a single
# extreme value inflates the standard deviation enough to mask itself
# ("masking effect"). Switched to median + median absolute deviation
# (MAD), which is robust to exactly this failure mode.

def check_outliers(conn, mad_threshold: float = 3.5) -> list[QAResult]:
    results = []
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM financials").fetchall()]

    for ticker in tickers:
        rows = conn.execute(
            "SELECT period_end, total_revenue FROM financials "
            "WHERE ticker = ? AND total_revenue IS NOT NULL ORDER BY period_end",
            (ticker,)
        ).fetchall()

        if len(rows) < 4:
            continue  # not enough history to judge an outlier

        values = [r[1] for r in rows]
        median = statistics.median(values)
        abs_deviations = [abs(v - median) for v in values]
        mad = statistics.median(abs_deviations) or 1e-9  # avoid div by zero

        # 0.6745 scales MAD to be comparable to a standard deviation
        # under a normal distribution assumption
        flagged = []
        for period_end, value in rows:
            robust_z = 0.6745 * (value - median) / mad
            if abs(robust_z) > mad_threshold:
                flagged.append((period_end, value, round(robust_z, 2)))

        if flagged:
            results.append(QAResult(
                "outliers", "financials", "warn",
                f"{ticker}: {len(flagged)} outlier period(s) [period, value, robust_z]: {flagged[:2]}"
            ))

    if not any(r.status == "warn" for r in results):
        results.append(QAResult("outliers", "financials", "pass", "No revenue outliers beyond robust_z>3.5"))

    return results


# ---------- Check 4: Staleness ----------

def check_staleness(conn, max_age_days: int = 120) -> list[QAResult]:
    results = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

    for table, date_field in [("financials", "period_end"), ("macro", "obs_date"), ("trends", "obs_date")]:
        row = conn.execute(f"SELECT MAX({date_field}) FROM {table}").fetchone()
        latest = row[0]
        if latest is None:
            results.append(QAResult("staleness", table, "warn", "Table is empty"))
            continue
        if latest < cutoff[:10]:
            results.append(QAResult("staleness", table, "warn", f"Latest data is {latest}, older than {max_age_days} days"))
        else:
            results.append(QAResult("staleness", table, "pass", f"Latest data point: {latest}"))
    return results


# ---------- Check 5: Restatement detection ----------
# NOTE: as of Epic 2 v2, restatement detection runs directly inside
# fetch_financials.py, BEFORE each INSERT OR REPLACE - see that file.
# It has to run there, not here, because by the time this post-ingest
# gate runs, the old value has already been overwritten and there's
# nothing left to compare against. This function is kept here so the
# fetcher can import and reuse the same check logic.

def check_restatements(conn, incoming_rows: list[tuple]) -> list[QAResult]:
    """
    Compare incoming financial rows against what's already stored.
    Call this BEFORE the INSERT OR REPLACE happens in the fetcher, passing
    the freshly-fetched rows, so we can catch a changed value before it's
    overwritten silently.

    incoming_rows: list of (ticker, period_end, period_type, total_revenue, net_income)
    """
    results = []
    restated = []

    for ticker, period_end, period_type, new_revenue, new_net_income in incoming_rows:
        existing = conn.execute(
            "SELECT total_revenue, net_income FROM financials "
            "WHERE ticker = ? AND period_end = ? AND period_type = ?",
            (ticker, period_end, period_type)
        ).fetchone()

        if existing is None:
            continue  # new data, not a restatement

        old_revenue, old_net_income = existing
        if old_revenue is not None and new_revenue is not None and abs(old_revenue - new_revenue) > 1e-6:
            restated.append((ticker, period_end, "total_revenue", old_revenue, new_revenue))
        if old_net_income is not None and new_net_income is not None and abs(old_net_income - new_net_income) > 1e-6:
            restated.append((ticker, period_end, "net_income", old_net_income, new_net_income))

    if restated:
        conn.executemany(
            "INSERT INTO restatements (ticker, period_end, field_name, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
            restated
        )
        results.append(QAResult("restatements", "financials", "warn", f"{len(restated)} restated value(s) detected: {restated[:3]}"))
    else:
        results.append(QAResult("restatements", "financials", "pass", "No restatements detected"))

    return results


# ---------- Orchestration ----------

def run_all_checks(run_id: str = None, incoming_financial_rows: list = None) -> list[QAResult]:
    run_id = run_id or str(uuid.uuid4())[:8]
    init_qa_tables()

    all_results = []
    with get_conn() as conn:
        all_results += check_missing_values(conn)
        all_results += check_duplicates(conn)
        all_results += check_outliers(conn)
        all_results += check_staleness(conn)
        if incoming_financial_rows:
            all_results += check_restatements(conn, incoming_financial_rows)

        conn.executemany(
            "INSERT INTO qa_results (run_id, check_name, table_name, status, detail) VALUES (?, ?, ?, ?, ?)",
            [(run_id, r.check_name, r.table_name, r.status, r.detail) for r in all_results]
        )

    return all_results


def print_summary(results: list[QAResult]):
    fails = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    passes = [r for r in results if r.status == "pass"]

    print(f"\n=== QA Summary: {len(passes)} passed, {len(warns)} warnings, {len(fails)} failed ===")
    for r in results:
        icon = {"pass": "✓", "warn": "!", "fail": "✗"}[r.status]
        print(f"  [{icon}] {r.check_name} ({r.table_name}): {r.detail}")

    if fails:
        print("\n⚠ QA FAILURES present — do not promote this data to downstream modeling without review.")


# ---------- Governance boundary: the guard downstream code must call ----------

class QAGateError(Exception):
    """Raised when data fails QA and a caller tries to proceed anyway."""
    pass


def qa_gate(run_id: str = None, raise_on_fail: bool = True) -> list[QAResult]:
    """
    The governance boundary. Any script in Epic 3+ (feature engineering,
    modeling, dashboard) should call this FIRST, before querying the
    financials/macro/sec_filings/trends tables directly.

    Usage:
        from src.qa_checks import qa_gate
        qa_gate()   # raises QAGateError if current data has any 'fail' status
        # ... now safe to build features / train models ...

    Set raise_on_fail=False to get results back without raising, e.g. for
    a dashboard that wants to show a "data quality" banner instead of
    crashing.
    """
    results = run_all_checks(run_id=run_id)
    fails = [r for r in results if r.status == "fail"]

    if fails and raise_on_fail:
        detail = "; ".join(f"{r.check_name}({r.table_name}): {r.detail}" for r in fails)
        raise QAGateError(f"QA gate blocked: {len(fails)} check(s) failed - {detail}")

    return results


if __name__ == "__main__":
    results = run_all_checks()
    print_summary(results)
