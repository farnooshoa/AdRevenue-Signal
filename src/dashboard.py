"""
AdRevenue Signal — client-facing dashboard.

Run with:
    streamlit run src/dashboard.py

Shows, per ticker: forecast vs. actual, model comparison, revenue trend,
data-quality status, and hypothesis test results — the actual "data
product" a client or sales team would see, per the job description this
project is scoped against.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd

from src.db import get_conn
from src.qa_checks import run_all_checks

st.set_page_config(page_title="AdRevenue Signal", layout="wide")


@st.cache_data(ttl=60)
def load_table(query: str) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(query, conn)


def render_header():
    st.title("📊 AdRevenue Signal")
    st.caption(
        "Ad-revenue trend forecasting for publicly traded, ad-exposed companies. "
        "Not investment advice — for internal sales/insights use."
    )


def render_qa_banner():
    """Shows a data-quality status banner, doesn't crash the dashboard on
    a QA failure — a client-facing product should degrade gracefully with
    a visible warning, not throw a stack trace."""
    try:
        results = run_all_checks()
        fails = [r for r in results if r.status == "fail"]
        warns = [r for r in results if r.status == "warn"]

        if fails:
            st.error(f"⚠ Data quality: {len(fails)} check(s) FAILED. Figures below may be unreliable.")
            with st.expander("See failed checks"):
                for r in fails:
                    st.write(f"- **{r.check_name}** ({r.table_name}): {r.detail}")
        elif warns:
            st.warning(f"Data quality: {len(warns)} warning(s) — see details.")
            with st.expander("See warnings"):
                for r in warns:
                    st.write(f"- **{r.check_name}** ({r.table_name}): {r.detail}")
        else:
            st.success("Data quality: all checks passed.")
    except Exception as e:
        st.warning(f"Could not run data quality checks: {e}")


def render_ticker_selector():
    df = load_table("SELECT DISTINCT ticker FROM financials ORDER BY ticker")
    if df.empty:
        st.info("No financial data yet. Run `python src/run_pipeline.py` first.")
        st.stop()
    return st.selectbox("Select company", df["ticker"].tolist())


def render_revenue_trend(ticker: str):
    df = load_table(
        f"SELECT period_end, total_revenue, revenue_qoq_growth, revenue_yoy_growth "
        f"FROM features WHERE ticker = '{ticker}' ORDER BY period_end"
    )
    if df.empty:
        st.info("No feature data for this ticker yet. Run `python src/features.py`.")
        return

    st.subheader(f"{ticker} — Revenue Trend")
    st.line_chart(df.set_index("period_end")["total_revenue"])

    col1, col2 = st.columns(2)
    with col1:
        st.metric(
            "Latest QoQ growth",
            f"{df['revenue_qoq_growth'].iloc[-1]:.1%}" if pd.notna(df["revenue_qoq_growth"].iloc[-1]) else "N/A"
        )
    with col2:
        st.metric(
            "Latest YoY growth",
            f"{df['revenue_yoy_growth'].iloc[-1]:.1%}" if pd.notna(df["revenue_yoy_growth"].iloc[-1]) else "N/A"
        )


def render_forecast_vs_actual(ticker: str):
    df = load_table(
        f"SELECT period_end, model_name, actual_revenue, forecast_revenue, abs_pct_error "
        f"FROM forecasts WHERE ticker = '{ticker}' ORDER BY period_end"
    )
    if df.empty:
        st.info("No backtest results for this ticker yet. Run `python src/forecast.py`.")
        return

    st.subheader(f"{ticker} — Forecast vs. Actual (backtest)")
    pivot = df.pivot(index="period_end", columns="model_name", values="forecast_revenue")
    actuals = df.drop_duplicates("period_end").set_index("period_end")["actual_revenue"]
    chart_df = pivot.copy()
    chart_df["actual"] = actuals
    st.line_chart(chart_df)

    st.markdown("**Model performance (MAPE, lower is better)**")
    summary = load_table(
        f"SELECT model_name, mape, rmse, n_forecasts FROM backtest_summary "
        f"WHERE ticker = '{ticker}'"
    )
    if not summary.empty:
        summary["mape"] = summary["mape"].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "N/A")
        summary["rmse"] = summary["rmse"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "N/A")
        st.dataframe(summary, hide_index=True, use_container_width=True)


def render_hypothesis_results():
    df = load_table(
        "SELECT hypothesis_id, description, result_summary, supports_hypothesis, tested_at "
        "FROM hypothesis_tests ORDER BY tested_at DESC LIMIT 10"
    )
    if df.empty:
        st.info("No hypothesis tests run yet. Run `python src/hypothesis_tests.py`.")
        return

    st.subheader("Recent Hypothesis Test Results")
    st.dataframe(df, hide_index=True, use_container_width=True)


def render_narrative(ticker: str):
    """Auto-generated plain-English summary panel — the 'shape
    conversations around trends' piece from the job description."""
    df = load_table(
        f"SELECT period_end, total_revenue, revenue_qoq_growth, revenue_yoy_growth, fiscal_quarter "
        f"FROM features WHERE ticker = '{ticker}' ORDER BY period_end DESC LIMIT 1"
    )
    if df.empty:
        return

    row = df.iloc[0]
    st.subheader("Narrative Summary")

    lines = [f"**{ticker}** reported revenue of **${row['total_revenue']:,.0f}** for the period ending {row['period_end']}."]

    if pd.notna(row["revenue_qoq_growth"]):
        direction = "up" if row["revenue_qoq_growth"] > 0 else "down"
        lines.append(f"That's **{direction} {abs(row['revenue_qoq_growth']):.1%}** quarter-over-quarter.")

    if pd.notna(row["revenue_yoy_growth"]):
        direction = "up" if row["revenue_yoy_growth"] > 0 else "down"
        lines.append(f"Year-over-year, revenue is **{direction} {abs(row['revenue_yoy_growth']):.1%}**.")

    if int(row["fiscal_quarter"]) == 4:
        lines.append("Q4 typically carries a seasonal ad-spend lift — worth factoring in when comparing to Q1-Q3.")

    st.markdown(" ".join(lines))


def main():
    render_header()
    render_qa_banner()
    st.divider()

    ticker = render_ticker_selector()
    st.divider()

    tab1, tab2, tab3 = st.tabs(["Trend & Narrative", "Forecast", "Hypothesis Tests"])
    with tab1:
        render_revenue_trend(ticker)
        render_narrative(ticker)
    with tab2:
        render_forecast_vs_actual(ticker)
    with tab3:
        render_hypothesis_results()


if __name__ == "__main__":
    main()
