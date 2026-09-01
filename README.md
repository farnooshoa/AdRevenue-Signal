# AdRevenue Signal: Epic 1: Data Foundation & Pipeline

Ingestion pipeline for structured + unstructured data used to forecast
advertising revenue trends for a basket of publicly traded companies.

## Structure

```
adrevenue-signal/
├── config/
│   └── companies.py       # company basket, macro series, trend keywords
├── src/
│   ├── db.py               # SQLite schema + connection helper
│   ├── fetch_financials.py # yfinance quarterly financials
│   ├── fetch_macro.py      # FRED macro indicators
│   ├── fetch_sec_filings.py# SEC EDGAR filing metadata
│   ├── fetch_trends.py     # Google Trends search interest
│   └── run_pipeline.py     # orchestrator - runs everything
├── data/
│   ├── raw/                 # (reserved for any raw dumps you add later)
│   └── processed/
│       └── adrevenue.db     # SQLite database (created on first run)
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt --break-system-packages

# Required for SEC EDGAR (they require an identifying User-Agent)
export SEC_USER_AGENT="Your Name your_email@example.com"

# Required for macro data - get a free key:
# https://fred.stlouisfed.org/docs/api/api_key.html
export FRED_API_KEY="your_fred_key_here"
```

## Run the full pipeline

```bash
python src/run_pipeline.py
```

Run a subset (e.g. skip the slower Google Trends step while iterating):

```bash
python src/run_pipeline.py --skip trends
```

## What each step does

| Step | Source | What it pulls |
|---|---|---|
| `financials` | yfinance | Quarterly revenue & net income per ticker |
| `macro` | FRED API | Consumer sentiment, CPI, PCE, unemployment |
| `sec` | SEC EDGAR | 10-Q/10-K filing metadata + doc URLs |
| `trends` | Google Trends (pytrends) | Search interest for ad-industry keywords |

