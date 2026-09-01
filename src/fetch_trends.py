"""
Pull Google Trends search interest for ad-industry keywords using pytrends.

This is your "unstructured/alternative data" signal - the hypothesis being
tested later (Epic 3) is whether search interest leads ad revenue by a
quarter or two.
"""
import sys
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.companies import TREND_KEYWORDS
from src.db import get_conn, log_step

try:
    from pytrends.request import TrendReq
except ImportError:
    TrendReq = None


def fetch_trend(keyword: str, timeframe: str = "today 5-y"):
    if TrendReq is None:
        raise ImportError("Run: pip install pytrends --break-system-packages")

    pytrends = TrendReq(hl="en-US", tz=360)
    pytrends.build_payload([keyword], timeframe=timeframe)
    df = pytrends.interest_over_time()

    if df.empty:
        return []

    rows = []
    for date, row in df.iterrows():
        rows.append((keyword, date.date().isoformat(), float(row[keyword])))
    return rows


def run(run_id: str = None):
    run_id = run_id or str(uuid.uuid4())[:8]
    total_rows = 0

    with get_conn() as conn:
        for keyword in TREND_KEYWORDS:
            try:
                rows = fetch_trend(keyword)
                conn.executemany(
                    "INSERT OR REPLACE INTO trends (keyword, obs_date, interest_score) VALUES (?, ?, ?)",
                    rows,
                )
                total_rows += len(rows)
                print(f"[trends] '{keyword}': {len(rows)} data points pulled")
                log_step(run_id, "fetch_trends", "success", len(rows), keyword)
            except Exception as e:
                print(f"[trends] '{keyword}': FAILED - {e}")
                log_step(run_id, "fetch_trends", "error", 0, f"{keyword}: {e}")

    print(f"Total trend rows written: {total_rows}")
    return total_rows


if __name__ == "__main__":
    run()
