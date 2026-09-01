"""
Pull recent 10-Q / 10-K filing metadata from SEC EDGAR for the company basket.

SEC EDGAR requires a descriptive User-Agent header identifying who's making
the request (their policy, not optional) - set SEC_USER_AGENT env var to
"Your Name your_email@example.com" before running.

This grabs filing metadata + doc URLs. Actual text extraction from the
filings (MD&A sections, ad-revenue-segment commentary) is a good candidate
for Epic 3 (feature engineering / NLP), not this ingestion step.
"""
import sys
import os
import uuid
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.companies import COMPANY_BASKET
from src.db import get_conn, log_step

import requests

# SEC requires ticker -> CIK mapping; this endpoint provides it
TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"


def get_user_agent():
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise EnvironmentError(
            "Set SEC_USER_AGENT env var, e.g. "
            "export SEC_USER_AGENT='Jane Doe jane@example.com'"
        )
    return ua


def build_ticker_cik_map(headers):
    resp = requests.get(TICKER_CIK_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # data is {"0": {"cik_str": ..., "ticker": "AAPL", "title": "..."}, ...}
    return {row["ticker"]: row["cik_str"] for row in data.values()}


def fetch_filings_for_ticker(ticker: str, cik: int, headers, forms=("10-Q", "10-K"), limit=8):
    url = SUBMISSIONS_URL.format(cik=cik)
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    recent = data["filings"]["recent"]
    rows = []
    count = 0
    for i, form in enumerate(recent["form"]):
        if form in forms and count < limit:
            accession = recent["accessionNumber"][i].replace("-", "")
            primary_doc = recent["primaryDocument"][i]
            doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primary_doc}"
            )
            rows.append((
                ticker,
                recent["accessionNumber"][i],
                form,
                recent["filingDate"][i],
                recent["reportDate"][i],
                doc_url,
            ))
            count += 1
    return rows


def run(run_id: str = None):
    run_id = run_id or str(uuid.uuid4())[:8]
    headers = {"User-Agent": get_user_agent()}

    print("Building ticker -> CIK map...")
    ticker_cik_map = build_ticker_cik_map(headers)

    total_rows = 0
    with get_conn() as conn:
        for ticker in COMPANY_BASKET:
            cik = ticker_cik_map.get(ticker)
            if not cik:
                print(f"[sec] {ticker}: no CIK found, skipping")
                continue
            try:
                rows = fetch_filings_for_ticker(ticker, cik, headers)
                conn.executemany(
                    "INSERT OR REPLACE INTO sec_filings "
                    "(ticker, accession_number, form_type, filing_date, report_date, primary_doc_url) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
                total_rows += len(rows)
                print(f"[sec] {ticker}: {len(rows)} filings pulled")
                log_step(run_id, "fetch_sec_filings", "success", len(rows), ticker, conn=conn)
                time.sleep(0.2)  # be polite to SEC's rate limits
            except Exception as e:
                print(f"[sec] {ticker}: FAILED - {e}")
                log_step(run_id, "fetch_sec_filings", "error", 0, f"{ticker}: {e}", conn=conn)

    print(f"Total SEC filing rows written: {total_rows}")
    return total_rows


if __name__ == "__main__":
    run()
