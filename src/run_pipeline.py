"""
Orchestrator: runs the full Epic 1 data pipeline end-to-end.

Usage:
    python src/run_pipeline.py
    python src/run_pipeline.py --skip trends,sec   # skip specific steps
"""
import sys
import uuid
import argparse
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.db import init_db, get_conn
from src import fetch_financials, fetch_macro, fetch_sec_filings, fetch_trends
from src.qa_checks import run_all_checks, print_summary

STEPS = {
    "financials": fetch_financials.run,
    "macro": fetch_macro.run,
    "sec": fetch_sec_filings.run,
    "trends": fetch_trends.run,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip", type=str, default="", help="Comma-separated steps to skip")
    args = parser.parse_args()
    skip = set(s.strip() for s in args.skip.split(",") if s.strip())

    run_id = str(uuid.uuid4())[:8]
    print(f"=== AdRevenue Signal Pipeline | run_id={run_id} ===\n")

    init_db()

    summary = {}
    for name, fn in STEPS.items():
        if name in skip:
            print(f"--- Skipping: {name} ---\n")
            continue
        print(f"--- Running: {name} ---")
        try:
            summary[name] = fn(run_id=run_id)
        except Exception as e:
            print(f"Step '{name}' crashed: {e}")
            summary[name] = f"ERROR: {e}"
        print()

    print("=== Pipeline Summary ===")
    for step, result in summary.items():
        print(f"  {step}: {result}")

    # Quick sanity check on what's in the DB now
    with get_conn() as conn:
        for table in ("financials", "macro", "sec_filings", "trends"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  [{table}] total rows in DB: {count}")

    # Epic 2: run QA gate automatically after ingestion, before anything
    # downstream (features/models/dashboard) is allowed to use this data
    print("\n=== Running QA Gate ===")
    qa_results = run_all_checks(run_id=run_id)
    print_summary(qa_results)

    if any(r.status == "fail" for r in qa_results):
        print(f"\nPipeline run {run_id} completed with QA FAILURES. Review before downstream use.")
    else:
        print(f"\nPipeline run {run_id} completed. Data cleared for downstream use (see any warnings above).")


if __name__ == "__main__":
    main()
