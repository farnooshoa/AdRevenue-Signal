# AdRevenue Signal

A client-facing data product forecasting advertising revenue trends for a
basket of publicly traded, ad-exposed companies — built to mirror the
responsibilities of a Data Scientist, Insights role: structured +
unstructured data analysis, predictive modeling, client-facing
visuals/narratives, hypothesis testing with domain context, data
governance, and awareness of the ad-industry market backdrop.

## What this is

An end-to-end pipeline: ingest financial + macro + search-trend data →
enforce data quality/governance → engineer features → test hypotheses
about what drives ad revenue → forecast next-quarter revenue → surface it
all in a client-facing dashboard → generate plain-English insight memos.

## Project structure

```
adrevenue-signal/
├── config/
│   ├── companies.py            # company basket, macro series, trend keywords
│   ├── data_dictionary.md      # field-by-field definitions, sources, known limitations
│   ├── hypotheses.md           # hypothesis log — rationale before testing, results after
│   ├── model_card_template.md  # governance sign-off template for any model
│   └── market_context.md       # ad-industry market & regulatory backdrop (Sept 2026)
├── src/
│   ├── db.py                   # SQLite schema + connection helper
│   ├── fetch_financials.py     # yfinance quarterly financials (+ restatement detection)
│   ├── fetch_macro.py          # FRED macro indicators
│   ├── fetch_sec_filings.py    # SEC EDGAR filing metadata
│   ├── fetch_trends.py         # Google Trends search interest
│   ├── qa_checks.py            # automated QA checks + qa_gate() governance boundary
│   ├── run_pipeline.py         # orchestrator — runs all fetchers + QA gate
│   ├── features.py             # QoQ/YoY growth, fiscal quarter, deseasonalized growth
│   ├── hypothesis_tests.py     # tests H1 (search leads revenue), H2 (sentiment), H3 (seasonality)
│   ├── forecast.py             # baseline (naive YoY) + linear multivariate model, backtested
│   ├── dashboard.py            # Streamlit client-facing dashboard
│   └── generate_memo.py        # generates a 1-page insight memo per ticker
├── data/
│   ├── raw/                    # reserved for raw dumps
│   └── processed/
│       ├── adrevenue.db        # SQLite database (all pipeline output)
│       └── memos/              # generated insight memos land here
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt --break-system-packages

# Required for SEC EDGAR (they require an identifying User-Agent)
export SEC_USER_AGENT="Your Name your_email@example.com"

# Required for macro data — get a free key:
# https://fred.stlouisfed.org/docs/api/api_key.html
export FRED_API_KEY="your_fred_key_here"
```

## Running the full pipeline, start to finish

```bash
# 1. Ingest data + run QA gate
python src/run_pipeline.py

# 2. Build features (QoQ/YoY growth, deseasonalized growth, fiscal quarter)
python src/features.py

# 3. Test hypotheses about what drives ad revenue
python src/hypothesis_tests.py

# 4. Backtest baseline vs. multivariate forecast models
python src/forecast.py

# 5. Generate a client-facing insight memo for a specific company
python src/generate_memo.py GOOGL

# 6. Launch the interactive dashboard
streamlit run src/dashboard.py
```

Each script (2-6) calls `qa_gate()` first and refuses to run if the data
has failed QA — this is the governance boundary the whole project is
built around. Bad data can't silently reach a client-facing output.

---

## How each part works

### 1. Data ingestion

`run_pipeline.py` orchestrates four fetchers:

| Fetcher | Source | Pulls |
|---|---|---|
| `fetch_financials.py` | yfinance | Quarterly revenue & net income per ticker |
| `fetch_macro.py` | FRED API | Consumer sentiment, CPI, PCE, unemployment |
| `fetch_sec_filings.py` | SEC EDGAR | 10-Q/10-K filing metadata + doc URLs |
| `fetch_trends.py` | Google Trends (pytrends) | Search interest for ad-industry keywords |

**Known limitations** (documented in `config/data_dictionary.md`):
yfinance typically only exposes ~4-8 quarters for free; Google Trends
scores aren't comparable across separately-fetched keywords without a
shared anchor term; `total_revenue` is company-wide, not ad-segment-specific.

### 2. Data quality & governance

`qa_checks.py` runs five checks: missing values, duplicates, outliers
(median absolute deviation, not mean/stdev — see caveat below), staleness,
and restatements.

**Placement matters and is deliberate:**

| Check | Runs | Why there |
|---|---|---|
| Restatement detection | Inside `fetch_financials.py`, before `INSERT OR REPLACE` | Only place the old value still exists to compare against |
| Missing values, duplicates, outliers, staleness | Post-ingest gate (end of `run_pipeline.py`) | Need the full table state to evaluate (e.g. an outlier is relative to a ticker's whole history) |
| `qa_gate()` | Called at the top of `features.py`, `hypothesis_tests.py`, `forecast.py`, `generate_memo.py` | The actual governance boundary — raises `QAGateError` and blocks execution if any check has `fail` status |

**A real bug found and fixed during development:** the first version of
the outlier check used mean/stdev z-scores and failed to catch a planted
extreme value, because a single outlier inflates the standard deviation
enough to mask itself. Fixed by switching to median + median absolute
deviation (MAD), which is robust to this failure mode.

**Another real bug found and fixed:** `log_step()` originally always
opened a fresh SQLite connection, even when called from inside a fetcher
that already held one open with an uncommitted write — causing
intermittent "database is locked" errors. Fixed by letting callers pass
their existing connection through.

### 3. Feature engineering & hypothesis testing

`features.py` builds QoQ growth, YoY growth, fiscal quarter, and a
**deseasonalized** QoQ growth column (raw growth minus that ticker's
historical average growth for that fiscal quarter).

`hypothesis_tests.py` tests three hypotheses (documented with rationale
in `config/hypotheses.md` before testing, to avoid hindsight bias):

- **H1**: Search interest leads ad revenue (lagged correlation)
- **H2**: Consumer sentiment predicts ad revenue growth (lagged correlation)
- **H3**: Revenue growth is seasonal, Q4 lift (group comparison)

**A real methodological bug found and fixed:** H1/H2 initially tested
against raw QoQ growth and returned near-zero correlation at every lag —
looked like "no relationship" everywhere. Root cause: raw growth carries
a strong seasonal signal (confirmed by H3) that swamps any other signal
in the variance. Fixed by testing against the deseasonalized growth
column instead. **Honest caveat:** a follow-up synthetic test after the
fix did show a strong correlation, but at lag 0, not the lag=1 the test
data was designed to demonstrate — investigation showed both series
shared a smooth upward drift in the synthetic data, so the result
reflected that shared drift, not a cleanly validated lag relationship.
That's a limitation of the test data, not the method — and exactly the
kind of thing to watch for when this runs against real data.

### 4. Forecasting

`forecast.py` implements two models and backtests them walk-forward (each
quarter's forecast is trained only on data strictly before it, avoiding
look-ahead bias):

- **Baseline**: naive YoY — revenue 4 quarters ago × (1 + last known YoY growth)
- **Linear multivariate**: ordinary least squares on lag features
  (deseasonalized growth lag-1, macro level lag-1), fit fresh at each
  backtest step using only past data

Results are written to `forecasts` (per-forecast) and `backtest_summary`
(MAPE + RMSE, both overall and per-ticker).

**Honest finding from testing:** on synthetic data built as a smooth
trend × seasonal pattern, the baseline beat the linear model by a wide
margin. That's a legitimate result, not a failure to report positively —
naive YoY implicitly captures both trend and seasonality by construction,
and the linear model's extra macro feature was uncorrelated noise in that
synthetic set, so it added noise rather than signal. **This is exactly
the kind of baseline-vs-model comparison a model card should report
honestly** — don't assume the more complex model wins; check.

### 5. Client-facing dashboard

`dashboard.py` is a Streamlit app with three tabs per selected ticker:
revenue trend + auto-generated narrative, forecast vs. actual with model
comparison, and recent hypothesis test results. A QA status banner at the
top shows pass/warn/fail state and **degrades gracefully** (shows a
warning banner) rather than crashing if QA checks fail — appropriate for
a client-facing surface.

### 6. Insight memos

`generate_memo.py` produces a 1-page markdown memo per ticker: headline
revenue figure with QoQ/YoY context, forecast track record, hypothesis
findings relevant to the trend, and a data quality note. This is the
literal "shape conversations around trends" deliverable — meant to be
dropped into an email ahead of a client or sales conversation.

### 7. Market & regulatory context

`config/market_context.md` is a researched (not memory-based) brief on
the current ad-industry backdrop — macro ad-spend growth, the retail
media/CTV shift, the actual (partial, browser-by-browser) state of cookie
deprecation, and relevant GDPR/CCPA enforcement context — written to be
folded into memo narratives so commentary reflects the real market, not
assumptions. Sourced September 2026; flagged as a living document to
re-research quarterly.

---

## Design principles this project tries to demonstrate

1. **Governance is structural, not decorative** — `qa_gate()` isn't a
   suggestion, it's a function that raises and blocks execution.
2. **Test the tests** — every major check in this project (outlier
   detection, hypothesis correlation, forecast backtest) was validated
   against synthetic data with a *known* answer, and two real bugs were
   found and fixed this way, not just assumed to work.
3. **Report honestly, including negative results** — the baseline beating
   the "advanced" model, and the inconclusive lag-validation on synthetic
   data, are both reported as findings, not hidden or spun.
4. **Walk-forward, not look-ahead** — the backtest only ever trains on
   data strictly before the quarter it's forecasting.
5. **Client-facing surfaces degrade gracefully** — the dashboard shows a
   warning banner on QA failure instead of crashing; the memo generator
   returns a clear "no data yet" message instead of an error.

## Known limitations / natural next steps

- Segment-level ad revenue (vs. company-wide revenue) isn't parsed from
  SEC filings yet — `sec_filings` currently stores metadata + doc URLs
  only, not extracted financial figures.
- The linear forecasting model is deliberately simple (OLS on 2 lag
  features) — a gradient-boosted model (LightGBM) with more features
  would be a natural next step once more real history is ingested.
- Google Trends keyword scores need a shared anchor term to be comparable
  across separate API calls — currently each keyword is fetched
  independently.
- `market_context.md` should be re-researched each quarter; ad-tech and
  privacy regulation move fast enough that a 6-month-old summary will be stale.
