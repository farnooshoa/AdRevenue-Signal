"""
Insight memo generator for AdRevenue Signal.

Produces a 1-page markdown memo per ticker, translating model output into
plain-English business language — this is the "shape conversations around
trends" deliverable from the job description, meant to be dropped into an
email or shared with a sales team ahead of a client conversation.

Usage:
    python src/generate_memo.py TICKER
    python src/generate_memo.py TICKER --output custom_name.md
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.db import get_conn
from src.qa_checks import qa_gate, QAGateError
from config.companies import COMPANY_BASKET

MEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "memos"


def get_latest_feature_row(conn, ticker: str):
    return conn.execute(
        "SELECT period_end, total_revenue, revenue_qoq_growth, revenue_yoy_growth, fiscal_quarter "
        "FROM features WHERE ticker = ? ORDER BY period_end DESC LIMIT 1",
        (ticker,)
    ).fetchone()


def get_latest_forecast_comparison(conn, ticker: str):
    """Most recent period where we have both models' forecasts, for a lift statement."""
    return conn.execute(
        "SELECT model_name, forecast_revenue, actual_revenue, abs_pct_error FROM forecasts "
        "WHERE ticker = ? ORDER BY period_end DESC LIMIT 2",
        (ticker,)
    ).fetchall()


def get_relevant_hypothesis_results(conn):
    return conn.execute(
        "SELECT hypothesis_id, description, result_summary, supports_hypothesis "
        "FROM hypothesis_tests ORDER BY tested_at DESC LIMIT 5"
    ).fetchall()


def get_qa_status_line(conn):
    row = conn.execute(
        "SELECT status, COUNT(*) FROM qa_results GROUP BY status"
    ).fetchall()
    if not row:
        return "No QA run recorded for this data snapshot."
    counts = {status: count for status, count in row}
    return (
        f"{counts.get('pass', 0)} checks passed, "
        f"{counts.get('warn', 0)} warnings, "
        f"{counts.get('fail', 0)} failed."
    )


def format_pct(value):
    if value is None:
        return "N/A"
    return f"{value:+.1%}"


def build_memo(ticker: str) -> str:
    company_name = COMPANY_BASKET.get(ticker, (ticker, ""))[0]

    with get_conn() as conn:
        latest = get_latest_feature_row(conn, ticker)
        forecasts = get_latest_forecast_comparison(conn, ticker)
        hypotheses = get_relevant_hypothesis_results(conn)
        qa_line = get_qa_status_line(conn)

    if latest is None:
        return (
            f"# {company_name} ({ticker}) — Insight Memo\n\n"
            f"No feature data available yet for {ticker}. Run the pipeline, "
            f"features, and forecast scripts before generating a memo.\n"
        )

    period_end, revenue, qoq, yoy, fiscal_q = latest

    lines = []
    lines.append(f"# {company_name} ({ticker}) — Ad Revenue Insight Memo")
    lines.append(f"*Generated {datetime.now().strftime('%Y-%m-%d')} · Period ending {period_end}*")
    lines.append("")
    lines.append("## Headline")
    lines.append(
        f"{company_name} reported revenue of **${revenue:,.0f}** for the period ending "
        f"{period_end} (fiscal Q{fiscal_q})."
    )

    trend_bits = []
    if qoq is not None:
        direction = "growth" if qoq > 0 else "contraction"
        trend_bits.append(f"quarter-over-quarter {direction} of {format_pct(qoq)}")
    if yoy is not None:
        direction = "growth" if yoy > 0 else "contraction"
        trend_bits.append(f"year-over-year {direction} of {format_pct(yoy)}")
    if trend_bits:
        lines.append(f"That reflects {' and '.join(trend_bits)}.")

    if fiscal_q == 4:
        lines.append(
            "This is a Q4 figure — ad-exposed companies typically see a seasonal "
            "lift from holiday retail spend, so compare against prior Q4s rather "
            "than sequential quarters for a cleaner read."
        )

    lines.append("")
    lines.append("## Forecast Track Record")
    if forecasts:
        for model_name, forecast_rev, actual_rev, pct_err in forecasts:
            if actual_rev and forecast_rev:
                lines.append(
                    f"- **{model_name}**: forecasted ${forecast_rev:,.0f} vs. actual "
                    f"${actual_rev:,.0f} ({format_pct(pct_err) if pct_err else 'N/A'} error)"
                )
    else:
        lines.append("No backtested forecasts available yet for this ticker.")

    lines.append("")
    lines.append("## What's Driving the Trend")
    if hypotheses:
        for hyp_id, desc, summary, supports in hypotheses:
            verdict = {"yes": "Supported", "no": "Not supported", "inconclusive": "Inconclusive"}.get(supports, supports)
            lines.append(f"- **{hyp_id} ({desc})** — {verdict}: {summary}")
    else:
        lines.append("No hypothesis tests run yet — see `src/hypothesis_tests.py`.")

    lines.append("")
    lines.append("## Data Quality Note")
    lines.append(qa_line)
    lines.append(
        "See `config/data_dictionary.md` for known limitations (e.g. revenue "
        "figures are company-wide, not ad-segment-specific unless noted)."
    )

    lines.append("")
    lines.append("---")
    lines.append(
        "*This memo is generated from AdRevenue Signal's internal pipeline. "
        "Not investment advice. For internal sales/insights use only.*"
    )

    return "\n".join(lines)


def run(ticker: str, output_path: str = None):
    print("Checking QA gate before generating memo...")
    try:
        qa_gate()
    except QAGateError as e:
        print(f"BLOCKED: {e}")
        print("Memo generation refused - fix failing QA checks first.")
        return None

    memo_text = build_memo(ticker)

    MEMO_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(output_path) if output_path else MEMO_DIR / f"{ticker}_memo_{datetime.now().strftime('%Y%m%d')}.md"
    out_path.write_text(memo_text)

    print(f"Memo written to {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", help="Ticker symbol, e.g. GOOGL")
    parser.add_argument("--output", help="Custom output path", default=None)
    args = parser.parse_args()
    run(args.ticker.upper(), args.output)
