# Cheap Rebound Scanner (Yahoo Finance)

A local stock scanner that identifies stocks showing rebound patterns across multiple markets (US, Europe, Global, B3). The scanner identifies stocks that have rebounded significantly after major declines, helping to generate large candidate lists for further filtering.

## Features

The scanner identifies stocks in three categories:

- **Category A:** Stocks that rose **+30% in the last ~90 days (63 trading days)** after falling **≥80% from their 5-year peak**
- **Category B:** Stocks that rose **+30% in the last ~90 days (63 trading days)** after hitting their **52-week low**
- **Category C:** Stocks that spiked **+30% from their 180-day low**

### Output Information

For each match, the scanner provides:
- Date/Price of the peak (5-year)
- Date/Price of the bottom (post-peak) or 52-week low
- Date when it crossed +30% from the bottom
- Current price
- Return percentage and other relevant metrics

The goal is to generate a **large list (thousands)** of candidates to feed into a **3rd filter** later.

---

## Installation

### Prerequisites

- Python 3.8 or higher
- Virtual environment (recommended)

### Setup

```bash
# Clone or navigate to the project directory
cd "barato 3 screener"

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On macOS/Linux
# or
venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

- `pandas>=2.0` - Data manipulation
- `numpy>=1.24` - Numerical operations
- `yfinance>=0.2.30` - Yahoo Finance data fetching
- `requests>=2.31` - HTTP requests

---

## Quick Start

### Run Everything (Recommended)

The easiest way to get started is to run the master script that handles all universes:

```bash
# Activate virtual environment
source venv/bin/activate

# Run everything for all universes (US, Europe, Global, B3)
python run_all_universes.py
```

This will:
1. ✅ Build/update universe files for US, Europe, Global, and B3
2. ✅ Fetch/update price data for each universe
3. ✅ Run screening for each universe
4. ✅ Export category A, B, and C matches

### Run Specific Universe

To process only one universe:

```bash
# US only
python run_all_universes.py --universe us

# Europe only
python run_all_universes.py --universe europe

# Global only
python run_all_universes.py --universe global

# B3 only
python run_all_universes.py --universe b3
```

### Skip Steps (Use Existing Data)

If you already have universe files or price data:

```bash
# Skip building universes (use existing files)
python run_all_universes.py --skip-build

# Skip fetching prices (use existing price data)
python run_all_universes.py --skip-fetch

# Skip screening (use existing results)
python run_all_universes.py --skip-screen

# Combine flags
python run_all_universes.py --skip-build --skip-fetch  # Only run screening
```

### Test with Limited Tickers

To test with a smaller number of tickers:

```bash
python run_all_universes.py --limit 100
```

---

## Project Structure

```
barato 3 screener/
├── data/
│   ├── universe/              # Universe ticker files
│   │   ├── tickers_us.csv
│   │   ├── tickers_europe.csv
│   │   ├── tickers_global.csv
│   │   └── tickers_b3.csv
│   ├── prices/                 # Historical price data
│   │   ├── us/
│   │   ├── europe/
│   │   ├── global/
│   │   └── b3/
│   └── results/                # Screening results
│       ├── us/
│       ├── europe/
│       ├── global/
│       └── b3/
├── scripts/
│   ├── build_universe_b3.py    # B3 universe builder
│   └── build_universe_europe.py # European universe builder
├── src/
│   ├── universe.py             # Universe management
│   ├── fetch_prices.py         # Price data fetching
│   ├── screen.py               # Main screening logic
│   ├── export_candidates.py    # Export category matches
│   ├── filter_market_cap.py    # Market cap filtering
│   ├── run.py                  # Pipeline runner
│   └── ...
├── run_all_universes.py        # Master script
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

---

## Output Files

After running, you'll find results in:

```
data/
├── universe/
│   ├── tickers_us.csv
│   ├── tickers_europe.csv
│   ├── tickers_global.csv
│   └── tickers_b3.csv
├── prices/
│   ├── us/          # Price data for US tickers
│   ├── europe/      # Price data for European tickers
│   ├── global/      # Price data for Global tickers
│   └── b3/          # Price data for B3 tickers
└── results/
    ├── us/
    │   ├── candidates_A_us.csv      # Category A matches
    │   ├── candidates_B_us.csv      # Category B matches
    │   ├── candidates_B_only_us.csv # Category B only (not A)
    │   ├── candidates_C_us.csv      # Category C matches
    │   └── screen_run_YYYYMMDD_HHMMSS.csv
    ├── europe/
    │   └── (same structure)
    ├── global/
    │   └── (same structure)
    └── b3/
        └── (same structure)
```

Results are timestamped, so you can keep historical runs. Category matches are sorted by return percentage (descending).

---

## Universe Building

### US Universe

Built from SEC data, filtered by:
- Market cap >= $400M USD
- Price >= $1.00 USD

```bash
python src/universe.py --source sec --output data/universe_us.csv --keep-only-valid
```

### European Universe

Built from Wikidata (SIX Swiss, XETRA, Euronext Paris, Nasdaq Stockholm), filtered by:
- Market cap >= $400M USD
- Price >= $1.00 USD

```bash
python scripts/build_universe_europe.py
```

### B3 Universe

Built from seed CSV file, filtered by:
- Market cap >= $400M USD
- Price >= $1.00 USD

```bash
python scripts/build_universe_b3.py
```

### Global Universe

Combines US, Europe, and B3 universes (all filtered by investible criteria).

---

## Manual Usage

If you prefer to run steps manually, see [USAGE.md](USAGE.md) for detailed instructions.

For building European universes specifically, see [QUICKSTART.md](QUICKSTART.md).

---

## Category Definitions

### Category A
Stocks that rose **+30% in the last ~90 days (63 trading days)** after falling **≥80% from their 5-year peak**.

These are stocks that experienced a severe decline (80%+ from peak) and are now showing signs of recovery.

### Category B
Stocks that rose **+30% in the last ~90 days (63 trading days)** after hitting their **52-week low**.

These are stocks that hit a recent low and are rebounding.

### Category C
Stocks that spiked **+30% from their 180-day low**.

These are stocks showing momentum from a longer-term low point.

---

## Troubleshooting

### Virtual Environment

If you haven't set up the virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
pip install -r requirements.txt
```

### Rate Limiting

If you encounter rate limiting from Yahoo Finance:
- The scripts include delays between requests
- You may need to wait and retry
- Consider using `--limit` to test with fewer tickers first

### Missing Dependencies

If you get import errors:

```bash
pip install pandas numpy yfinance requests
```

---

## Notes

- Building universes and fetching prices can take a while (especially for large universes)
- Price data is cached, so subsequent runs will only update recent data
- Results are timestamped, so you can keep historical runs
- Category matches are sorted by return percentage (descending)
- All universes are filtered by investible criteria (market cap >= $400M USD, price >= $1.00 USD)

---

## License

This project is for personal/educational use.

