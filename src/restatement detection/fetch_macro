"""
Pull macro indicators (consumer sentiment, CPI, etc.) from FRED.

Requires a free FRED API key: https://fred.stlouisfed.org/docs/api/api_key.html
Set it as an environment variable: FRED_API_KEY

Uses the raw FRED REST API directly (no extra dependency needed beyond
`requests`), so it's easy to swap out later.
"""
import sys
import os
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.companies import MACRO_SERIES
from src.db import get_conn, log_step

import requests

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


def fetch_series(series_id: str, api_key: str, start_date: str = "2019-01-01"):
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
    }
    resp = requests.get(FRED_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for obs in data.get("observations", []):
        if obs["value"] == ".":  # FRED uses "." for missing values
            continue
        rows.append((series_id, obs["date"], float(obs["value"])))
    return rows


def run(run_id: str = None):
    run_id = run_id or str(uuid.uuid4())[:8]
    api_key = os.environ.get("FRED_API_KEY")

    if not api_key:
        print("FRED_API_KEY not set. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
        print("Then run: export FRED_API_KEY=your_key_here")
        return 0

    total_rows = 0
    with get_conn() as conn:
        for series_id, label in MACRO_SERIES.items():
            try:
                rows = fetch_series(series_id, api_key)
                conn.executemany(
                    "INSERT OR REPLACE INTO macro (series_id, obs_date, value) VALUES (?, ?, ?)",
                    rows,
                )
                total_rows += len(rows)
                print(f"[macro] {series_id} ({label}): {len(rows)} observations pulled")
                log_step(run_id, "fetch_macro", "success", len(rows), series_id, conn=conn)
            except Exception as e:
                print(f"[macro] {series_id}: FAILED - {e}")
                log_step(run_id, "fetch_macro", "error", 0, f"{series_id}: {e}", conn=conn)

    print(f"Total macro rows written: {total_rows}")
    return total_rows


if __name__ == "__main__":
    run()
