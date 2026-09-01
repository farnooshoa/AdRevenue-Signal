# Hypothesis Log — AdRevenue Signal

Document every hypothesis BEFORE testing it — rationale first, result after.
This prevents hindsight bias ("we always knew that") and gives a governance
reviewer a clear record of what was tried, including what didn't work.

Results are also written to the `hypothesis_tests` table by
`src/hypothesis_tests.py` — this doc is the human-readable companion.

---

## H1: Search interest leads ad revenue

- **Rationale:** Advertisers and marketers researching "digital advertising"
  or "programmatic advertising" ahead of a spend increase should show up in
  search trends before that spend shows up in quarterly revenue.
- **Method:** Lagged Pearson correlation between `trends.interest_score`
  (aggregated to quarterly) and `financials`-derived revenue QoQ growth,
  at lags of 0, 1, and 2 quarters.
- **Prediction:** Correlation should be strongest at a positive lag
  (search leads revenue by 1-2 quarters), not at lag 0.
- **Methodology note (found during testing, not a result — see below):**
  the first version of this test used raw QoQ growth and returned a
  near-zero correlation regardless of lag, at any keyword. Root cause:
  raw QoQ growth carries a strong seasonal signal (see H3) that swamps
  any other relationship in the variance. Fixed by testing against
  **deseasonalized** QoQ growth instead (`revenue_qoq_growth_deseasonalized`
  in `features` — raw growth minus that ticker's historical mean growth
  for that fiscal quarter). This is a real methodological fix, not a
  result — any hypothesis test involving revenue growth should use the
  deseasonalized column unless it's specifically testing for seasonality.
- **Result:** _(run `src/hypothesis_tests.py` against real data and paste
  the `[H1]` line here)_
- **Decision:** _(keep as feature / drop / needs more data — decide once
  run against real financials + trends data, not synthetic test data)_

## H2: Consumer sentiment predicts ad revenue growth

- **Rationale:** Ad budgets are often the first thing cut when consumer
  spending outlook weakens, so a drop in Consumer Sentiment (`UMCSENT`)
  should precede a slowdown in ad-exposed companies' revenue growth.
- **Method:** Lagged correlation between `macro` (`UMCSENT`) and
  deseasonalized revenue QoQ growth (see H1 methodology note), same lag
  structure as H1.
- **Prediction:** Negative correlation at a positive lag.
- **Result:** _(run against real data and paste the `[H2]` line here)_
- **Decision:** _(keep / drop / needs more data)_

## H3: Revenue growth is seasonal (Q4 lift)

- **Rationale:** Advertising spend has a well-known Q4 seasonal bump
  (holiday retail). If true, Q4 QoQ growth should be systematically
  higher than other quarters across the basket.
- **Method:** Compare mean QoQ growth by fiscal quarter across all tickers.
- **Prediction:** Q4 growth rate mean > other quarters.
- **Result:** _(filled in after test run)_
- **Decision:** _(keep as a seasonal feature / drop)_

---

## Template for adding a new hypothesis

```
## H<N>: <short name>

- **Rationale:**
- **Method:**
- **Prediction:**
- **Result:**
- **Decision:**
```
