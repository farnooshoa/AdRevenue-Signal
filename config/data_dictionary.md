# Data Dictionary — AdRevenue Signal

Documents every field in the pipeline: where it comes from, what it means,
and how it's transformed. This is the artifact a governance reviewer (or
the Chief Insights & Analytics Officer) would ask for first.

---

## Table: `financials`

| Field | Type | Source | Definition | Notes |
|---|---|---|---|---|
| `ticker` | TEXT | Config (`companies.py`) | Stock ticker symbol | Primary key component |
| `period_end` | DATE | yfinance `quarterly_financials` | Last day of the reporting quarter | ISO 8601 (YYYY-MM-DD) |
| `period_type` | TEXT | Pipeline logic | `'quarterly'` or `'annual'` | Currently only quarterly is populated |
| `total_revenue` | REAL | yfinance income statement, row "Total Revenue" | GAAP total revenue, USD | Not ad-revenue-specific — segment-level ad revenue requires 10-Q parsing (see `sec_filings`) |
| `net_income` | REAL | yfinance income statement, row "Net Income" | GAAP net income, USD | |
| `fetched_at` | TIMESTAMP | Pipeline | When this row was ingested | Use for freshness checks |

**Known limitation:** yfinance's `Total Revenue` is company-wide, not
segment-level ad revenue. For companies like Amazon (retail media is one
segment of many), this figure will overstate ad-specific revenue. True
segment revenue needs to be extracted from 10-Q footnotes — flagged as a
feature-engineering task in Epic 3.

---

## Table: `macro`

| Field | Type | Source | Definition | Notes |
|---|---|---|---|---|
| `series_id` | TEXT | FRED | FRED series code (e.g. `UMCSENT`) | See `MACRO_SERIES` in config |
| `obs_date` | DATE | FRED | Observation date | Frequency varies by series (monthly/quarterly) |
| `value` | REAL | FRED | Raw indicator value | Units vary by series — see FRED series metadata |
| `fetched_at` | TIMESTAMP | Pipeline | Ingestion timestamp | |

---

## Table: `sec_filings`

| Field | Type | Source | Definition | Notes |
|---|---|---|---|---|
| `ticker` | TEXT | Config | Stock ticker | |
| `accession_number` | TEXT | SEC EDGAR | Unique filing identifier | Primary key component |
| `form_type` | TEXT | SEC EDGAR | `10-Q` or `10-K` | |
| `filing_date` | DATE | SEC EDGAR | Date filed with SEC | |
| `report_date` | DATE | SEC EDGAR | Period the filing covers | May differ from filing_date by weeks |
| `primary_doc_url` | TEXT | SEC EDGAR (constructed) | Direct link to filing document | Not yet parsed for content — metadata only |
| `fetched_at` | TIMESTAMP | Pipeline | Ingestion timestamp | |

---

## Table: `trends`

| Field | Type | Source | Definition | Notes |
|---|---|---|---|---|
| `keyword` | TEXT | Config (`TREND_KEYWORDS`) | Search term tracked | |
| `obs_date` | DATE | Google Trends (pytrends) | Date of observation | Weekly granularity for 5-year lookback |
| `interest_score` | REAL | Google Trends | Relative search interest, 0-100 | **Normalized per-query**, not comparable across separate fetches — see caveat below |
| `fetched_at` | TIMESTAMP | Pipeline | Ingestion timestamp | |

**Known limitation:** Google Trends scores are relative to the max value
*within that specific query's timeframe*. Scores for two different
keywords pulled in separate API calls are **not directly comparable** to
each other without a shared baseline term. Flagged for Epic 3 — use a
consistent anchor keyword across all pulls if cross-keyword comparison is needed.

---

## Table: `pipeline_log`

| Field | Type | Definition |
|---|---|---|
| `run_id` | TEXT | Groups all steps from one pipeline execution |
| `step` | TEXT | Which fetcher ran (`fetch_financials`, etc.) |
| `status` | TEXT | `success` or `error` |
| `rows_written` | INTEGER | Row count written in that step |
| `message` | TEXT | Ticker/keyword or error detail |
| `logged_at` | TIMESTAMP | When logged |

---

## Lineage summary

```
External API → fetch_*.py → SQLite (raw ingest) → [Epic 2: QA gate] → downstream features/models
```

Nothing downstream (features, models, dashboards) should read from these
tables directly without passing through the QA gate in `src/qa_checks.py`.
