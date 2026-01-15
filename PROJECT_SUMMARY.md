# Project TL;DR - Stock Screener for Boss Presentation

## Overview
A stock screening system that identifies "cheap rebound" investment opportunities across US, European, Global, and Brazilian (B3) markets. The system finds stocks that have rebounded significantly after major declines, generating candidate lists for further analysis.

---

## Core Screening Categories

### Category A
**Criteria:** Stocks that rose **+30% in last 90 days** after falling **≥80% from their 5-year peak**
- **Metric:** `bottom_drawdown_pct >= 0.80` AND `return_90d >= 0.30`
- **What it finds:** Deep value plays - stocks that crashed hard and are recovering

### Category B
**Criteria:** Stocks that rose **+30% in last 90 days** after hitting **52-week low**
- **Metric:** `return_90d >= 0.30` AND has 52-week history
- **What it finds:** Recent rebound opportunities from short-term lows

### Category C
**Criteria:** Stocks that spiked **+30% from their 180-day low**
- **Metric:** Price reached `1.3 × low180_price` after the low date
- **What it finds:** Momentum plays from medium-term lows

---

## File-by-File Breakdown

### **src/screen.py** - Core Screening Engine
**Purpose:** Analyzes price history to identify rebound patterns

**Main Functions:**
- `screen_ticker()` - Analyzes a single stock's price history
- `run_screen()` - Batch processes entire universe

**Key Metrics Computed:**
- 5-year peak price & date
- Bottom price after peak & drawdown %
- 52-week low price & date
- 180-day low price & date
- 90-day return percentage
- Volatility (90-day)
- Average volume (30-day)
- Category qualification flags (A, B, C)

**Output:** CSV with all metrics and qualification flags

---

### **src/export_candidates.py** - Results Export & Enrichment
**Purpose:** Takes screening results, enriches with fundamentals, and exports clean candidate files

**Main Functions:**
- `clean_and_export()` - Main export pipeline
- `add_fundamentals()` - Fetches financial data (net income, market cap, net debt)
- `add_cash_flow_yield_at_low_date()` - Computes CFY at low point
- `add_ev_at_low_date()` - Computes Enterprise Value at low point
- `add_pe_at_low_date()` - Computes P/E ratio at low point
- `add_debt_years()` - Computes debt repayment time

**Key Metrics Added:**
- **Cash Flow Yield** (at low date): `(Operating CF - CapEx) / Enterprise Value`
- **Enterprise Value** (at low date): `Market Cap + Net Debt`
- **P/E Ratio** (at low date): `Market Cap / Net Income TTM`
- **Debt Years**: `Net Debt / Free Cash Flow TTM`
- **Net Income LTM** (Last Twelve Months)
- **Market Cap (USD)**
- **Net Debt (USD)**

**Filters Applied:**
- Cash Flow Yield >= 3%
- Debt Years < 6 (for leveraged companies)
- Net Debt > 0 (only companies with debt)

**Output:** Three CSV files:
- `candidates_A.csv` - Category A matches
- `candidates_B.csv` - Category B matches  
- `candidates_C.csv` - Category C matches

---

### **src/fetch_prices.py** - Price Data Fetcher
**Purpose:** Downloads and caches historical price data from Yahoo Finance

**Main Functions:**
- `update_prices_for_ticker()` - Downloads/updates price data for one ticker
- `_download_prices()` - Fetches from Yahoo Finance API

**Features:**
- Incremental updates (only fetches new data)
- Handles currency conversion (GBp → GBP scaling)
- Caches to CSV files per ticker

**Output:** CSV files in `data/prices/` directory (one per ticker)

---

### **src/filter_market_cap.py** - Universe Filtering
**Purpose:** Filters stock universe by investible criteria

**Main Functions:**
- `main()` - Filters universe by market cap and price thresholds

**Filters Applied:**
- Market Cap >= $400M USD (default)
- Price >= $1.00 USD (default)

**Output:** Filtered universe CSV with market cap and price data

---

### **src/fx.py** - Currency Conversion
**Purpose:** Handles foreign exchange rate conversion to USD

**Main Functions:**
- `convert_to_usd()` - Converts any amount to USD
- `get_fx_rate()` - Fetches FX rate with caching
- `get_fx_rate_batch()` - Batch FX rate fetching

**Supported Currencies:**
- GBP, EUR, CHF, SEK, BRL, USD

**Features:**
- Daily cache (refreshes every 24 hours)
- Handles GBp (pence) → GBP conversion
- Rate limiting to avoid API throttling

---

### **src/meta.py** - Ticker Metadata
**Purpose:** Fetches and caches ticker metadata (currency, etc.)

**Main Functions:**
- `get_ticker_meta()` - Gets metadata for single ticker
- `get_ticker_meta_batch()` - Batch metadata fetching

**Cached Data:**
- Currency code (30-day cache)
- Other metadata as needed

---

### **src/universe.py** - Universe Management
**Purpose:** Builds and manages stock universes from various sources

**Main Functions:**
- `build_universe_from_sec()` - Builds US universe from SEC data
- `build_universe_from_many_csvs()` - Combines multiple CSV sources

**Sources:**
- SEC public data (US stocks)
- Custom CSV files

---

### **src/screen_live.py** - Live Screening (Alternative)
**Purpose:** Alternative screening method that fetches prices on-the-fly (no pre-cached data)

**Main Functions:**
- `compute_metrics()` - Computes screening metrics from live data
- `_download_prices()` - Downloads prices in real-time

**Use Case:** When you don't want to maintain price cache files

---

### **scripts/build_universe_b3.py** - Brazilian Market Builder
**Purpose:** Builds B3 (Brazilian stock exchange) universe

**Main Functions:**
- `normalize_ticker()` - Normalizes Brazilian tickers (adds .SA suffix)

**Output:** `data/universe_b3.csv`

---

### **scripts/build_universe_europe.py** - European Market Builder
**Purpose:** Builds European stock universe from Wikidata

**Main Functions:**
- `fetch_wikidata_companies()` - Fetches from Wikidata SPARQL
- `validate_ticker_and_get_info()` - Validates with Yahoo Finance
- `get_price_and_market_cap_from_info()` - Extracts financial data

**Exchanges Covered:**
- SIX Swiss Exchange (.SW)
- XETRA / Germany (.DE)
- Euronext Paris (.PA)
- Nasdaq Stockholm (.ST)

**Filters:**
- Market Cap >= $400M USD
- Price >= $1.00 USD

**Output:** `data/universe_europe_filtered.txt` (converted to CSV)

---

### **run_all_universes.py** - Master Orchestrator
**Purpose:** End-to-end pipeline runner for all universes

**Main Functions:**
- `build_universe_us()` - Builds US universe
- `build_universe_europe()` - Builds European universe
- `build_universe_global()` - Combines all universes
- `build_universe_b3()` - Builds B3 universe
- `run_pipeline_for_universe()` - Runs full pipeline (fetch → screen → export)
- `export_candidates()` - Exports category matches

**Pipeline Steps:**
1. Build/update universe files
2. Fetch/update price data
3. Run screening
4. Export category candidates

**Output:** Organized results in `data/results/{universe}/` directories

---

## Key Metrics Summary

### Screening Metrics
- **Return (90D)**: Price change over last 90 trading days
- **Drawdown From Peak (5Y)**: Maximum decline from 5-year peak
- **Volatility (90D)**: Standard deviation of daily log returns
- **Avg Volume (30D)**: Average trading volume

### Fundamental Metrics (Added in Export)
- **Cash Flow Yield**: `(Operating CF - CapEx) / Enterprise Value` (at low date)
- **Enterprise Value**: `Market Cap + Net Debt` (at low date)
- **P/E Ratio**: `Market Cap / Net Income TTM` (at low date)
- **Debt Years**: `Net Debt / Free Cash Flow TTM`
- **Market Cap (USD)**: Current market capitalization
- **Net Debt (USD)**: Total debt minus cash

### Filter Thresholds
- **Minimum Market Cap**: $400M USD
- **Minimum Price**: $1.00 USD
- **Minimum Cash Flow Yield**: 3%
- **Maximum Debt Years**: 6 years (for leveraged companies)

---

## Data Flow

```
1. Universe Building
   └─> SEC / Wikidata / CSV sources
   └─> Filter by market cap & price
   └─> Output: universe CSV files

2. Price Fetching
   └─> Yahoo Finance API
   └─> Cache to CSV files
   └─> Output: price history files

3. Screening
   └─> Analyze price patterns
   └─> Compute metrics
   └─> Flag categories (A, B, C)
   └─> Output: screen results CSV

4. Export & Enrichment
   └─> Fetch fundamentals
   └─> Compute financial ratios
   └─> Apply filters (CFY, Debt Years)
   └─> Output: candidate CSV files (A, B, C)
```

---

## Output Files Structure

```
data/
├── universe/
│   ├── tickers_us.csv
│   ├── tickers_europe.csv
│   ├── tickers_global.csv
│   └── tickers_b3.csv
├── prices/
│   ├── us/          # Per-ticker CSV files
│   ├── europe/
│   ├── global/
│   └── b3/
└── results/
    ├── us/
    │   ├── candidates_A_us.csv
    │   ├── candidates_B_us.csv
    │   ├── candidates_B_only_us.csv
    │   ├── candidates_C_us.csv
    │   └── screen_run_TIMESTAMP.csv
    └── (same for europe, global, b3)
```

---

## Business Value

1. **Automated Discovery**: Finds rebound opportunities across 4 markets automatically
2. **Quality Filters**: Applies fundamental filters (CFY, debt metrics) to ensure quality
3. **Scalable**: Processes thousands of stocks efficiently
4. **Multi-Market**: Covers US, Europe, Global, and Brazilian markets
5. **Historical Analysis**: Uses 5-year price history for robust pattern detection
6. **Fundamental Integration**: Combines technical patterns with financial metrics

---

## Performance Characteristics

- **Universe Size**: 
  - US: ~5,000-8,000 stocks (after filtering)
  - Europe: ~1,000-2,000 stocks
  - B3: ~300-500 stocks
  - Global: Combined total

- **Processing Time**: 
  - Price fetching: ~1-2 hours per universe (first run)
  - Screening: ~10-30 minutes per universe
  - Export/enrichment: ~30-60 minutes per universe (API rate limits)

- **Data Storage**: 
  - Price cache: ~50-100 MB per universe
  - Results: ~1-5 MB per run

---

## Technology Stack

- **Python 3.8+**
- **pandas**: Data manipulation
- **numpy**: Numerical operations
- **yfinance**: Yahoo Finance API client
- **requests**: HTTP requests (Wikidata, SEC)

---

## Key Differentiators

1. **Multi-Category Screening**: Finds 3 distinct rebound patterns (A, B, C)
2. **Fundamental Integration**: Adds financial metrics at the low point (not current)
3. **Multi-Market Coverage**: US, Europe, Brazil in one system
4. **Automated Pipeline**: End-to-end automation from universe building to candidate export
5. **Quality Filters**: Cash flow yield and debt metrics ensure investible candidates
