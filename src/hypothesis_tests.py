"""
Hypothesis testing for AdRevenue Signal.

Tests the hypotheses documented in config/hypotheses.md:
  H1: Search interest leads ad revenue (lagged correlation)
  H2: Consumer sentiment predicts ad revenue growth (lagged correlation)
  H3: Revenue growth is seasonal, Q4 lift (group comparison)

Every result is written to `hypothesis_tests` so the log in
config/hypotheses.md can be filled in from actual output, not vibes.

Uses only the standard library (statistics module) for correlation math,
so no extra dependency is needed for something this size. If the feature
set grows, swap in scipy.stats.pearsonr for p-values.
"""
import sys
import statistics
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.db import get_conn
from src.qa_checks import qa_gate, QAGateError

HYPOTHESIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypothesis_tests (
    hypothesis_id TEXT NOT NULL,
    description TEXT,
    method TEXT,
    result_summary TEXT,
    supports_hypothesis TEXT,   -- 'yes', 'no', 'inconclusive'
    n_observations INTEGER,
    tested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_hypothesis_table():
    with get_conn() as conn:
        conn.executescript(HYPOTHESIS_SCHEMA)


def pearson_correlation(x: list, y: list) -> float:
    """Standard-library Pearson correlation. Returns None if undefined."""
    if len(x) < 3 or len(x) != len(y):
        return None
    try:
        return statistics.correlation(x, y)
    except statistics.StatisticsError:
        return None


def log_result(conn, hyp_id, description, method, summary, supports, n_obs):
    conn.execute(
        "INSERT INTO hypothesis_tests "
        "(hypothesis_id, description, method, result_summary, supports_hypothesis, n_observations) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (hyp_id, description, method, summary, supports, n_obs)
    )


# ---------- H1: Search interest leads ad revenue ----------

def test_h1_search_leads_revenue(conn, keyword: str = "digital advertising", max_lag: int = 2):
    from src.features import get_quarterly_trend_series
    quarterly_trends = get_quarterly_trend_series(conn, keyword)

    # Uses deseasonalized growth, not raw QoQ growth - raw growth carries
    # a strong seasonal signal (see H3) that swamps any lagged
    # relationship with an external series like search trends. Confirmed
    # empirically: the raw-growth version of this test returned a
    # near-zero correlation purely because seasonality dominated the
    # variance, not because no relationship existed.
    rows = conn.execute(
        "SELECT period_end, revenue_qoq_growth_deseasonalized FROM features "
        "WHERE revenue_qoq_growth_deseasonalized IS NOT NULL"
    ).fetchall()

    if not rows or not quarterly_trends:
        log_result(conn, "H1", "Search interest leads ad revenue", "lagged Pearson correlation",
                    "Insufficient data: trends or features table is empty", "inconclusive", 0)
        print("[H1] Insufficient data: trends or features table is empty -> supports_hypothesis=inconclusive")
        return

    # Build quarter-labeled revenue growth series (average across basket per quarter)
    revenue_by_quarter = {}
    for period_end, growth in rows:
        year, month, _ = period_end.split("-")
        q = (int(month) - 1) // 3 + 1
        key = f"{year}-Q{q}"
        revenue_by_quarter.setdefault(key, []).append(growth)
    revenue_avg_by_quarter = {k: sum(v) / len(v) for k, v in revenue_by_quarter.items()}

    best_lag, best_corr, best_n = None, None, 0
    for lag in range(0, max_lag + 1):
        trend_vals, revenue_vals = [], []
        sorted_quarters = sorted(revenue_avg_by_quarter.keys())
        for q in sorted_quarters:
            # shift trend backward by `lag` quarters to test if trend[t-lag] predicts revenue[t]
            year, qtr = q.split("-Q")
            qtr = int(qtr)
            lag_qtr = qtr - lag
            lag_year = int(year)
            while lag_qtr < 1:
                lag_qtr += 4
                lag_year -= 1
            lag_key = f"{lag_year}-Q{lag_qtr}"

            if lag_key in quarterly_trends and q in revenue_avg_by_quarter:
                trend_vals.append(quarterly_trends[lag_key])
                revenue_vals.append(revenue_avg_by_quarter[q])

        corr = pearson_correlation(trend_vals, revenue_vals)
        if corr is not None and (best_corr is None or abs(corr) > abs(best_corr)):
            best_lag, best_corr, best_n = lag, corr, len(trend_vals)

    if best_corr is None:
        summary = "Not enough overlapping quarters between trends and revenue to compute correlation"
        supports = "inconclusive"
    else:
        summary = f"Strongest correlation at lag={best_lag} quarters: r={best_corr:.3f} (n={best_n})"
        supports = "yes" if (best_lag and best_lag > 0 and abs(best_corr) > 0.3) else "no"

    log_result(conn, "H1", "Search interest leads ad revenue", "lagged Pearson correlation, lags 0-2Q",
                summary, supports, best_n)
    print(f"[H1] {summary} -> supports_hypothesis={supports}")


# ---------- H2: Consumer sentiment predicts ad revenue growth ----------

def test_h2_sentiment_predicts_growth(conn, series_id: str = "UMCSENT", max_lag: int = 2):
    macro_rows = conn.execute(
        "SELECT obs_date, value FROM macro WHERE series_id = ? ORDER BY obs_date", (series_id,)
    ).fetchall()

    if not macro_rows:
        log_result(conn, "H2", "Consumer sentiment predicts ad revenue growth", "lagged Pearson correlation",
                    f"No macro data found for series '{series_id}'", "inconclusive", 0)
        print(f"[H2] No macro data for '{series_id}' -> supports_hypothesis=inconclusive")
        return

    # Aggregate macro to quarterly average
    macro_quarterly = {}
    for obs_date, value in macro_rows:
        year, month, _ = obs_date.split("-")
        q = (int(month) - 1) // 3 + 1
        key = f"{year}-Q{q}"
        macro_quarterly.setdefault(key, []).append(value)
    macro_avg = {k: sum(v) / len(v) for k, v in macro_quarterly.items()}

    rows = conn.execute(
        "SELECT period_end, revenue_qoq_growth_deseasonalized FROM features "
        "WHERE revenue_qoq_growth_deseasonalized IS NOT NULL"
    ).fetchall()
    revenue_by_quarter = {}
    for period_end, growth in rows:
        year, month, _ = period_end.split("-")
        q = (int(month) - 1) // 3 + 1
        key = f"{year}-Q{q}"
        revenue_by_quarter.setdefault(key, []).append(growth)
    revenue_avg = {k: sum(v) / len(v) for k, v in revenue_by_quarter.items()}

    best_lag, best_corr, best_n = None, None, 0
    for lag in range(0, max_lag + 1):
        macro_vals, revenue_vals = [], []
        for q in sorted(revenue_avg.keys()):
            year, qtr = q.split("-Q")
            qtr = int(qtr)
            lag_qtr, lag_year = qtr - lag, int(year)
            while lag_qtr < 1:
                lag_qtr += 4
                lag_year -= 1
            lag_key = f"{lag_year}-Q{lag_qtr}"
            if lag_key in macro_avg:
                macro_vals.append(macro_avg[lag_key])
                revenue_vals.append(revenue_avg[q])

        corr = pearson_correlation(macro_vals, revenue_vals)
        if corr is not None and (best_corr is None or abs(corr) > abs(best_corr)):
            best_lag, best_corr, best_n = lag, corr, len(macro_vals)

    if best_corr is None:
        summary = "Not enough overlapping quarters between macro series and revenue"
        supports = "inconclusive"
    else:
        summary = f"Strongest correlation at lag={best_lag} quarters: r={best_corr:.3f} (n={best_n})"
        supports = "yes" if (best_lag and best_lag > 0 and best_corr > 0.3) else "no"

    log_result(conn, "H2", "Consumer sentiment predicts ad revenue growth", "lagged Pearson correlation, lags 0-2Q",
                summary, supports, best_n)
    print(f"[H2] {summary} -> supports_hypothesis={supports}")


# ---------- H3: Revenue growth is seasonal (Q4 lift) ----------

def test_h3_seasonal_q4_lift(conn):
    rows = conn.execute(
        "SELECT fiscal_quarter, revenue_qoq_growth FROM features WHERE revenue_qoq_growth IS NOT NULL"
    ).fetchall()

    if not rows:
        log_result(conn, "H3", "Revenue growth is seasonal (Q4 lift)", "group mean comparison",
                    "No feature data available", "inconclusive", 0)
        print("[H3] No feature data available -> supports_hypothesis=inconclusive")
        return

    by_quarter = {1: [], 2: [], 3: [], 4: []}
    for fq, growth in rows:
        by_quarter[fq].append(growth)

    means = {q: (statistics.mean(v) if v else None) for q, v in by_quarter.items()}
    q4_mean = means[4]
    other_means = [means[q] for q in (1, 2, 3) if means[q] is not None]

    if q4_mean is None or not other_means:
        summary = "Insufficient Q4 or comparison-quarter data"
        supports = "inconclusive"
        n_obs = sum(len(v) for v in by_quarter.values())
    else:
        other_avg = statistics.mean(other_means)
        summary = f"Q4 mean growth={q4_mean:.3f} vs. other quarters avg={other_avg:.3f} (n={len(rows)})"
        supports = "yes" if q4_mean > other_avg else "no"
        n_obs = len(rows)

    log_result(conn, "H3", "Revenue growth is seasonal (Q4 lift)", "group mean comparison",
                summary, supports, n_obs)
    print(f"[H3] {summary} -> supports_hypothesis={supports}")


def run():
    print("Checking QA gate before hypothesis testing...")
    try:
        qa_gate()
    except QAGateError as e:
        print(f"BLOCKED: {e}")
        return

    print("QA gate passed. Running hypothesis tests...\n")
    init_hypothesis_table()

    with get_conn() as conn:
        test_h1_search_leads_revenue(conn)
        test_h2_sentiment_predicts_growth(conn)
        test_h3_seasonal_q4_lift(conn)

    print("\nResults written to `hypothesis_tests` table.")
    print("Update config/hypotheses.md 'Result' and 'Decision' fields from this output.")


if __name__ == "__main__":
    run()
