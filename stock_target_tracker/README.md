# Stock Target Price Tracker

Fetches analyst target prices for stocks from multiple web sources, stores them in a SQLite database, and tracks prediction accuracy at 30, 90, 180, and 365-day checkpoints.

## Quick Start

```powershell
# Install dependencies (from project root)
uv sync

# Initialize database + load the whitelist from input/sample_symbols.csv
python stock_target_tracker/tracker.py init

# Fetch analyst target prices (for all whitelisted symbols)
python stock_target_tracker/tracker.py fetch

# Fetch current stock prices
python stock_target_tracker/tracker.py prices

# Run accuracy checkpoint checks
python stock_target_tracker/tracker.py accuracy

# View accuracy summary
python stock_target_tracker/tracker.py summary

# View detail for a specific symbol
python stock_target_tracker/tracker.py detail AAPL

# Generate an HTML accuracy report (opens in browser)
python stock_target_tracker/report.py

# Start daily auto-fetch scheduler
python stock_target_tracker/tracker.py schedule
```

To track a different set of stocks, edit `input/sample_symbols.csv` — only symbols in that file will be tracked.

## Refreshing the Report (portfolio vs sample)

Two strictly-separated workflows, run via `refresh.py`:

```powershell
# Portfolio — updates the main DB (stock_tracker.db) AND writes output/latest.html
python stock_target_tracker/refresh.py --mode portfolio
python stock_target_tracker/refresh.py --mode portfolio --full   # also fetch MarketBeat dated targets

# Sample — writes ONLY output/sample_output.html, using an isolated DB
#           (sample_tracker.db via STT_DB_PATH). Never touches the main DB or latest.html.
python stock_target_tracker/refresh.py --mode sample
```

| | Portfolio | Sample |
|---|---|---|
| Whitelist | `input/portfolio_whitelist.csv` | `input/sample_symbols.csv` |
| Database | main `stock_tracker.db` | isolated `sample_tracker.db` |
| Output | `output/latest.html` | `output/sample_output.html` |
| Writes `latest.html`? | yes | **no** |

`latest.html` (personal portfolio) is local-only/untracked. The public GitHub
Pages site publishes `sample_output.html` (the non-personal sample) via
`deploy_gh_pages.py` to https://sray1.github.io/sr_project/ . See the deploy
script header for details.

## Data Sources

| Source | Method | API Key Required | Rate Limit |
|--------|--------|-----------------|------------|
| Yahoo Finance | `yfinance` library + scraping fallback | No | 0.5-1s between requests |
| Financial Modeling Prep | REST API | Yes (`FMP_API_KEY` env var) | 0.3s between calls (250/day free tier) |
| oanor | REST API | Yes (`OANOR_API_KEY` env var) | 1s between calls (free tier) |
| MarketBeat | Web scraping | No | 2-3s between requests (bot detection) |

### Setting up API Keys

Get free keys and add them to a `.env` file in the project root (which is
gitignored, so keys stay local):

1. **FMP** — free key at [financialmodelingprep.com](https://financialmodelingprep.com/). Gives consensus + period-averaged targets.
2. **oanor** — free key at [oanor.com](https://www.oanor.com/) (Analyst API). Gives Nasdaq-sourced consensus + a month-by-month dated target timeline.
   ```
   FMP_API_KEY=your_fmp_key_here
   OANOR_API_KEY=your_oanor_key_here
   ```

## Input CSV (Whitelist)

The input CSV at `stock_target_tracker/input/sample_symbols.csv` is the **whitelist** of symbols that may be tracked. **Only symbols listed in this CSV can be fetched, priced, or accuracy-checked.** Symbols passed via `--symbols` that are not in the CSV are rejected (and never added to the database).

To track a new symbol, add it to `input/sample_symbols.csv` first:

```csv
symbol,company_name,sector
AAPL,Apple Inc.,Technology
MSFT,Microsoft Corporation,Technology
GOOGL,Alphabet Inc.,Technology
```

Only `symbol` is required. `company_name` and `sector` are optional metadata. You can point any command at a different whitelist file with `--csv path/to/file.csv`.

### Whitelist Enforcement

| Scenario | Behavior |
|----------|----------|
| `fetch --symbols AAPL,MSFT` (both in CSV) | Fetches both |
| `fetch --symbols AAPL,FAKECO` (FAKECO not in CSV) | Fetches AAPL; rejects FAKECO with a message |
| `fetch --symbols FAKECO` (none in CSV) | Nothing fetched; tells you to add it to the CSV |
| `fetch` (no `--symbols`) | Fetches all symbols in the CSV |
| `prices` / `accuracy --symbol` | Same whitelist rules apply |

## CLI Commands

| Command | Description |
|---------|-------------|
| `init [--csv file.csv]` | Create DB tables + load symbols from the whitelist CSV |
| `fetch [--symbols AAPL,MSFT] [--source yahoo_finance] [--csv file.csv]` | Fetch analyst target prices (symbols must be in the whitelist) |
| `prices [--symbols AAPL] [--date 2026-06-01] [--csv file.csv]` | Fetch current or historical stock prices |
| `accuracy [--checkpoint 30] [--symbol AAPL] [--csv file.csv]` | Run accuracy checkpoint checks |
| `summary [--by-source] [--by-checkpoint]` | Display accuracy summary |
| `detail AAPL` | Full detail for a symbol |
| `schedule [--time 09:30]` | Start daily auto-fetch scheduler |

## Accuracy Tracking

Accuracy is measured at four checkpoints after a target price is issued:

- **30-day**: How close was the target to the actual price after 1 month?
- **90-day**: After 3 months?
- **180-day**: After 6 months?
- **365-day**: After 1 year?

### Accuracy Ratings

| Rating | Meaning |
|--------|---------|
| **HIT** | Actual price within ±5% of target |
| **MISS_LOW** | Stock exceeded target by >5% (analyst was too conservative) |
| **MISS_HIGH** | Stock fell short of target by >5% (analyst was too optimistic) |
| **NO_DATA** | Stock price unavailable on checkpoint date |

## Database Schema

SQLite database (`stock_tracker.db`) with four tables:

- **`symbols`** — Tracked stock symbols with company metadata
- **`target_prices`** — Analyst targets from each source (upsert by symbol/source/firm/date). Includes `ever_hit` / `first_hit_date` / `days_to_hit` / `ever_hit_eval_at` migration columns for the TPMetANY metric (see below).
- **`actual_prices`** — Historical stock prices (upsert by symbol/date)
- **`accuracy_snapshots`** — Checkpoint comparison results (upsert by target/checkpoint)

## Accuracy Metrics

Two complementary metrics are tracked per target:

- **TPMetEND (checkpoint hit)** — At each fixed checkpoint (30/90/180/365 days after the target was issued), is the actual close within ±5% of the target? This is the snapshot-based hit/miss rating above. Hit rate declines with horizon and is the headline per-checkpoint stat.
- **TPMetANY (ever-hit)** — Did the stock touch the target *at any point* within a 365-day window from issue? Stored as a sticky `ever_hit` flag on `target_prices` (once true, stays true), with `days_to_hit` (avg ~23 days observed). This is a more lenient "did the target ever get reached" measure and runs noticeably higher than the checkpoint hit rate.

## Report Features

The generated HTML report (`report.py`) is a standalone file with embedded JSON + vanilla JS (no external assets), so it can be shared/opened offline. Recent additions:

- **Conviction score** — the Consensus Picks table is sorted by a conviction score = `(implied_move + avg_pct_diff) × hit_rate`. This is a bias-adjusted gap (the symbol's historical analyst bias added back to the current gap to price) multiplied by the symbol's observed hit rate, so it favors targets that are both wide-of-price *and* on symbols analysts have historically nailed. Hover the cell for the component breakdown; symbols with no snapshots show "—".
- **Checkpoint chips** — per-symbol best/worst analyst items show a `Nd hit/miss` chip indicating which checkpoint (30/90/180/365d) that analyst's accuracy was measured at, color-coded green/red.
- **Consensus vs individual-firm classification** — FMP and any firm whose name contains "consensus" are routed into the consensus sections (not ranked as individual firms). FMP's free tier is consensus + period-averaged only (no per-analyst targets), so it is correctly treated as a consensus source.
- **Methodology collapse** — per-symbol-varying oanor/FMP methodology rows are merged into single rows with a chip cloud of the months/windows covered.
- **Source descriptions** — the methodology section includes brief cards describing oanor (Nasdaq-sourced, dated consensus timeline) and FMP (consensus + period averages).
- **Comparable-services note** — methodology names comparable services (AnaChart, Eidolum, TipRanks, MarketBeat, WallStreetZen, Quiver) and notes this tracker's differentiators (TPMetEND + TPMetANY, Asquith/Bonini/Bilinski methodology lineage).
- **Run-timing footer** — `refresh.py` injects a small-font footer into each generated report with per-step elapsed times, % of total, and the e2e TOTAL, plus a generation timestamp.

## DB Health Check & Trends

`db_check.py` inspects both databases (portfolio `stock_tracker.db` + isolated `sample_tracker.db`) and prints schema/integrity checks, data-quality issues, and overall trends:

```powershell
python stock_target_tracker/db_check.py
```

It checks: expected tables, ever_hit migration columns, orphan foreign keys, leftover cash/fund symbols (e.g. SPAXX), NULL `date_posted` on dated sources, duplicate target groups, bad target prices, dated targets missing snapshots, stale `ever_hit` on closed windows, price staleness, and `no_data` snapshots. It then reports trends: targets by source/month (with a sparkline + ▲/▼ direction), hit rate by checkpoint (with avg `pct_diff` bias), the ever-hit summary, consensus vs individual-firm hit rate, and most/least accurate firms (min 5 snapshots).

## Project Structure

```
stock_target_tracker/
    tracker.py               # Main CLI entry point
    db.py                    # SQLite persistence (tables + CRUD + display)
    utils.py                 # Shared utilities (retry, output, config)
    csv_loader.py            # Read + validate the input whitelist CSV
    sources/
        __init__.py           # Source registry + dispatcher
        yahoo_finance.py      # Yahoo Finance fetcher
        fmp.py               # Financial Modeling Prep API fetcher (consensus + period averages)
        oanor.py             # oanor Analyst API fetcher (Nasdaq-sourced consensus + dated timeline)
        marketbeat.py         # MarketBeat web scraper
    price_fetcher.py          # Current/historical stock price fetcher
    accuracy.py               # Multi-checkpoint accuracy logic
    scheduler.py              # APScheduler daily auto-fetch
    report.py                 # HTML accuracy report generator (standalone, embedded JSON + JS)
    refresh.py                # One-shot portfolio/sample refresh wrapper (fetch+prices+accuracy+report, with timing)
    db_check.py               # DB health check + trend report (run ad-hoc)
    deploy_gh_pages.py        # Publish sample_output.html to GitHub Pages (orphan gh-pages branch)
    input/
        sample_symbols.csv    # Whitelist of trackable symbols (the only stocks tracked)
    output/                   # Generated HTML reports (timestamped)
    tests/
        test_db.py
        test_csv_loader.py
        test_accuracy.py
```

## Scheduler

The scheduler runs two jobs on weekdays:

- **Fetch job** (default 09:30): Fetches new analyst targets + current prices
- **Accuracy job** (default 16:00): Runs accuracy checkpoint comparisons

```powershell
# Start with defaults
python stock_target_tracker/tracker.py schedule

# Custom times
python stock_target_tracker/tracker.py schedule --time 10:00 --accuracy-time 17:00
```

Press Ctrl+C to stop the scheduler.

## Running Tests

```powershell
python -m pytest stock_target_tracker/tests/ -v
```