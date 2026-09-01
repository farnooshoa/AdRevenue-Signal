"""
Forecasting for AdRevenue Signal.

Two models, compared head-to-head via backtest:
  1. Baseline: naive YoY - forecast = revenue from 4 quarters ago * (1 + last known YoY growth rate)
  2. Model: multivariate linear regression on lag features (own lagged growth,
     macro level, trend level) using least squares - no external ML
     dependency needed for this feature set size.

Backtest is walk-forward: for each quarter in the test window, train only
on data strictly before it, forecast that quarter, compare to actual.
This avoids look-ahead bias (training on future data to predict the past).

Calls qa_gate() first, same governance boundary as features.py and
hypothesis_tests.py.
"""
import sys
import statistics
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.db import get_conn
from src.qa_checks import qa_gate, QAGateError

try:
    import numpy as np
except ImportError:
    np = None

FORECAST_SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    ticker TEXT NOT NULL,
    period_end DATE NOT NULL,
    model_name TEXT NOT NULL,      -- 'baseline_naive_yoy' or 'linear_multivariate'
    actual_revenue REAL,
    forecast_revenue REAL,
    abs_error REAL,                -- |actual - forecast|, in revenue units
    abs_pct_error REAL,            -- |actual - forecast| / |actual|, unitless fraction
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, period_end, model_name)
);

CREATE TABLE IF NOT EXISTS backtest_summary (
    model_name TEXT NOT NULL,
    ticker TEXT,                   -- NULL = aggregate across all tickers
    mape REAL,                     -- mean absolute percentage error (unitless fraction)
    rmse REAL,                     -- root mean squared error, in revenue units
    n_forecasts INTEGER,
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_forecast_tables():
    with get_conn() as conn:
        conn.executescript(FORECAST_SCHEMA)


def get_ticker_history(conn, ticker: str):
    """Ordered list of (period_end, total_revenue, revenue_qoq_growth,
    revenue_yoy_growth, revenue_qoq_growth_deseasonalized)."""
    return conn.execute(
        "SELECT period_end, total_revenue, revenue_qoq_growth, revenue_yoy_growth, "
        "revenue_qoq_growth_deseasonalized "
        "FROM features WHERE ticker = ? ORDER BY period_end",
        (ticker,)
    ).fetchall()


# ---------- Model 1: Baseline naive YoY ----------

def baseline_naive_yoy_forecast(history: list, target_idx: int):
    """
    Forecast revenue at history[target_idx] using: revenue 4 quarters
    earlier * (1 + most recent known YoY growth rate at that point).
    Returns None if there isn't enough history.
    """
    if target_idx < 4:
        return None

    revenue_4q_ago = history[target_idx - 4][1]
    if revenue_4q_ago is None:
        return None

    # most recent YoY growth rate known BEFORE the target quarter
    known_yoy = None
    for i in range(target_idx - 1, 3, -1):
        if history[i][3] is not None:  # revenue_yoy_growth column
            known_yoy = history[i][3]
            break

    if known_yoy is None:
        known_yoy = 0.0  # no prior YoY signal - assume flat

    return revenue_4q_ago * (1 + known_yoy)


# ---------- Model 2: Linear multivariate (lag features) ----------

def build_training_matrix(conn, ticker: str, target_idx: int, history: list):
    """
    Build (X, y) for training a linear model on all quarters STRICTLY
    BEFORE target_idx - this is what makes the backtest walk-forward and
    avoids look-ahead bias.

    Features per row: [1 (intercept), deseasonalized_qoq_growth_lag1, macro_level_lag1]
    Target: total_revenue

    Uses deseasonalized growth, not raw QoQ growth, for the same reason
    H1/H2 in hypothesis_tests.py do - raw growth carries a strong
    recurring seasonal signal that adds noise rather than predictive
    power to a lag feature.
    """
    macro_series = conn.execute(
        "SELECT obs_date, value FROM macro WHERE series_id = 'UMCSENT' ORDER BY obs_date"
    ).fetchall()

    def macro_as_of(date_str):
        """Most recent macro observation on/before this date."""
        val = None
        for obs_date, value in macro_series:
            if obs_date <= date_str:
                val = value
            else:
                break
        return val

    X, y = [], []
    for i in range(1, target_idx):  # strictly before target_idx
        period_end, revenue, _, _, deseasonalized_growth = history[i]
        prev_deseasonalized = history[i - 1][4]  # lag-1 deseasonalized growth
        macro_val = macro_as_of(period_end)

        if revenue is None or prev_deseasonalized is None or macro_val is None:
            continue

        X.append([1.0, prev_deseasonalized, macro_val])
        y.append(revenue)

    return X, y


def fit_and_predict(X: list, y: list, x_new: list):
    """
    Ordinary least squares via numpy. Returns None if numpy isn't
    installed or there's not enough data to fit (need at least as many
    rows as columns, plus a margin for a meaningful fit).
    """
    if np is None or len(X) < 4:
        return None

    X_arr = np.array(X)
    y_arr = np.array(y)

    try:
        coeffs, *_ = np.linalg.lstsq(X_arr, y_arr, rcond=None)
        prediction = float(np.dot(np.array(x_new), coeffs))
        return prediction
    except np.linalg.LinAlgError:
        return None


def linear_multivariate_forecast(conn, ticker: str, target_idx: int, history: list):
    if target_idx < 5:  # need enough history to train + have lag features
        return None

    X, y = build_training_matrix(conn, ticker, target_idx, history)
    if len(X) < 4:
        return None

    period_end, _, _, _, _ = history[target_idx]
    prev_deseasonalized = history[target_idx - 1][4]

    macro_series = conn.execute(
        "SELECT obs_date, value FROM macro WHERE series_id = 'UMCSENT' ORDER BY obs_date"
    ).fetchall()
    macro_val = None
    for obs_date, value in macro_series:
        if obs_date <= period_end:
            macro_val = value
        else:
            break

    if prev_deseasonalized is None or macro_val is None:
        return None

    return fit_and_predict(X, y, [1.0, prev_deseasonalized, macro_val])


# ---------- Backtest orchestration ----------

def compute_error(actual, forecast):
    """Returns (abs_error_in_revenue_units, abs_pct_error) or (None, None)."""
    if actual is None or forecast is None:
        return None, None
    abs_error = abs(actual - forecast)
    pct_error = abs_error / abs(actual) if actual != 0 else None
    return abs_error, pct_error


def run_backtest(min_history: int = 5):
    print("Checking QA gate before forecasting...")
    try:
        qa_gate()
    except QAGateError as e:
        print(f"BLOCKED: {e}")
        return

    print("QA gate passed. Running backtest...\n")
    init_forecast_tables()

    with get_conn() as conn:
        tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM features").fetchall()]

        all_errors = {"baseline_naive_yoy": [], "linear_multivariate": []}
        all_abs_errors = {"baseline_naive_yoy": [], "linear_multivariate": []}
        per_ticker_errors = {}
        per_ticker_abs_errors = {}

        for ticker in tickers:
            history = get_ticker_history(conn, ticker)
            if len(history) < min_history:
                print(f"[{ticker}] Skipped - only {len(history)} periods, need >= {min_history}")
                continue

            per_ticker_errors[ticker] = {"baseline_naive_yoy": [], "linear_multivariate": []}
            per_ticker_abs_errors[ticker] = {"baseline_naive_yoy": [], "linear_multivariate": []}

            for target_idx in range(min_history, len(history)):
                period_end, actual_revenue, _, _, _ = history[target_idx]
                if actual_revenue is None:
                    continue

                baseline_pred = baseline_naive_yoy_forecast(history, target_idx)
                linear_pred = linear_multivariate_forecast(conn, ticker, target_idx, history)

                for model_name, pred in [
                    ("baseline_naive_yoy", baseline_pred),
                    ("linear_multivariate", linear_pred),
                ]:
                    if pred is None:
                        continue
                    abs_error, pct_error = compute_error(actual_revenue, pred)
                    conn.execute(
                        "INSERT OR REPLACE INTO forecasts "
                        "(ticker, period_end, model_name, actual_revenue, forecast_revenue, abs_error, abs_pct_error) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (ticker, period_end, model_name, actual_revenue, pred, abs_error, pct_error)
                    )
                    if pct_error is not None:
                        all_errors[model_name].append(pct_error)
                        per_ticker_errors[ticker][model_name].append(pct_error)
                    if abs_error is not None:
                        all_abs_errors[model_name].append(abs_error)
                        per_ticker_abs_errors[ticker][model_name].append(abs_error)

        # Aggregate summary, overall + per ticker
        for model_name, errors in all_errors.items():
            if errors:
                mape = statistics.mean(errors)
                abs_errors = all_abs_errors[model_name]
                rmse = (statistics.mean([e ** 2 for e in abs_errors])) ** 0.5
                conn.execute(
                    "INSERT INTO backtest_summary (model_name, ticker, mape, rmse, n_forecasts) VALUES (?, NULL, ?, ?, ?)",
                    (model_name, mape, rmse, len(errors))
                )
                print(f"[OVERALL] {model_name}: MAPE={mape:.1%}, RMSE=${rmse:,.0f} (n={len(errors)})")
            else:
                print(f"[OVERALL] {model_name}: no forecasts generated")

        for ticker, models in per_ticker_errors.items():
            for model_name, errors in models.items():
                if errors:
                    mape = statistics.mean(errors)
                    abs_errors = per_ticker_abs_errors[ticker][model_name]
                    rmse = (statistics.mean([e ** 2 for e in abs_errors])) ** 0.5 if abs_errors else None
                    conn.execute(
                        "INSERT INTO backtest_summary (model_name, ticker, mape, rmse, n_forecasts) VALUES (?, ?, ?, ?, ?)",
                        (model_name, ticker, mape, rmse, len(errors))
                    )

    # Explicit lift check
    if all_errors["baseline_naive_yoy"] and all_errors["linear_multivariate"]:
        baseline_mape = statistics.mean(all_errors["baseline_naive_yoy"])
        linear_mape = statistics.mean(all_errors["linear_multivariate"])
        lift = (baseline_mape - linear_mape) / baseline_mape if baseline_mape else None
        if lift is not None:
            verdict = "BEATS baseline" if lift > 0 else "underperforms baseline"
            print(f"\nModel lift vs baseline: {lift:+.1%} ({verdict})")


if __name__ == "__main__":
    run_backtest()
