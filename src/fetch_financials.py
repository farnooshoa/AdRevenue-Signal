"""
Pull quarterly financials (revenue, net income) for the company basket
using yfinance.

Note: yfinance scrapes Yahoo Finance endpoints and typically only returns
the last ~4-8 quarters of statement data for free. For a longer history
you'd lean more on the SEC EDGAR fetcher (see fetch_sec_filings.py).
"""
import sys
from pathlib import Path
import uuid

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.companies import COMPANY_BASKET
from src.db import get_conn, log_step
from src.qa_checks import check_restatements, init_qa_tables

try:
    import yfinance as yf
except ImportError:
    yf = None


def fetch_financials_for_ticker(ticker: str):
    """Returns a list of (period_end, period_type, total_revenue, net_income)."""
    if yf is None:
        raise ImportError("Run: pip install yfinance --break-system-packages")

    stock = yf.Ticker(ticker)
    rows = []

    # Quarterly income statement
    q_fin = stock.quarterly_financials  # columns = period end dates
    for period_end in q_fin.columns:
        revenue = q_fin.loc["Total Revenue", period_end] if "Total Revenue" in q_fin.index else None
        net_income = q_fin.loc["Net Income", period_end] if "Net Income" in q_fin.index else None
        rows.append((ticker, period_end.date().isoformat(), "quarterly", revenue, net_income))

    return rows


def run(run_id: str = None):
    run_id = run_id or str(uuid.uuid4())[:8]
    total_rows = 0
    init_qa_tables()  # ensures `restatements` table exists

    with get_conn() as conn:
        for ticker in COMPANY_BASKET:
            try:
                rows = fetch_financials_for_ticker(ticker)

                # Catch restatements BEFORE the old value is overwritten.
                # check_restatements() compares incoming rows against
                # what's currently stored and logs any changed value to
                # the `restatements` table - it must run before the
                # INSERT OR REPLACE below, not after, or the old value
                # is already gone.
                restatement_results = check_restatements(conn, rows)
                for r in restatement_results:
                    if r.status == "warn":
                        print(f"[financials] {ticker}: RESTATEMENT DETECTED - {r.detail}")

                conn.executemany(
                    "INSERT OR REPLACE INTO financials "
                    "(ticker, period_end, period_type, total_revenue, net_income) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                total_rows += len(rows)
                print(f"[financials] {ticker}: {len(rows)} periods pulled")
                log_step(run_id, "fetch_financials", "success", len(rows), ticker, conn=conn)
            except Exception as e:
                print(f"[financials] {ticker}: FAILED - {e}")
                log_step(run_id, "fetch_financials", "error", 0, f"{ticker}: {e}", conn=conn)

    print(f"Total financial rows written: {total_rows}")
    return total_rows


if __name__ == "__main__":
    run()
