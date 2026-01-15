# How to Run the Project

This guide explains how to update all universes (US, Europe, Global) and generate category A, B, and C matches.

## Quick Start

### Option 1: Run Everything (Recommended)

Run the master script to update all universes and generate matches:

```bash
# Activate virtual environment (if using one)
source venv/bin/activate

# Run everything for all universes
python run_all_universes.py
```

This will:
1. ✅ Build/update universe files for US, Europe, and Global
2. ✅ Fetch/update price data for each universe
3. ✅ Run screening for each universe
4. ✅ Export category A, B, and C matches

### Option 2: Run Specific Universe

To run only one universe:

```bash
# US only
python run_all_universes.py --universe us

# Europe only
python run_all_universes.py --universe europe

# Global only
python run_all_universes.py --universe global
```

### Option 3: Skip Steps (Use Existing Data)

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

### Option 4: Test with Limited Tickers

To test with a smaller number of tickers:

```bash
python run_all_universes.py --limit 100
```

## Output Files

After running, you'll find results in:

```
data/
├── universe/
│   ├── tickers_us.csv
│   ├── tickers_europe.csv
│   └── tickers_global.csv
├── prices/
│   ├── us/          # Price data for US tickers
│   ├── europe/      # Price data for European tickers
│   └── global/      # Price data for Global tickers
└── results/
    ├── us/
    │   ├── candidates_A_us.csv      # Category A matches
    │   ├── candidates_B_us.csv      # Category B matches
    │   ├── candidates_B_only_us.csv # Category B only (not A)
    │   └── candidates_C_us.csv      # Category C matches
    ├── europe/
    │   └── (same structure)
    └── global/
        └── (same structure)
```

## Category Definitions

- **Category A**: Stocks that rose +30% in the last ~90 days after falling ≥80% from their 5-year peak
- **Category B**: Stocks that rose +30% in the last ~90 days after hitting their 52-week low
- **Category C**: Stocks that spiked +30% from their 180-day low

## Manual Steps (Alternative)

If you prefer to run steps manually:

### 1. Build US Universe

```bash
python src/universe.py --source sec --output data/universe_us.csv --keep-only-valid
```

### 2. Build Europe Universe

```bash
python scripts/build_universe_europe.py
# Then convert to CSV:
python -c "import pandas as pd; df = pd.read_csv('data/universe_europe_filtered.txt', header=None, names=['ticker']); df.to_csv('data/universe_europe_filtered.csv', index=False)"
```

### 3. Build Global Universe

```bash
python -c "
import pandas as pd
df_us = pd.read_csv('data/universe_us.csv')
df_europe = pd.read_csv('data/universe_europe_filtered.csv')
df_global = pd.concat([df_us[['ticker']], df_europe[['ticker']]], ignore_index=True)
df_global = df_global.drop_duplicates(subset=['ticker']).sort_values('ticker')
df_global.to_csv('data/universe_global.csv', index=False)
"
```

### 4. Run Pipeline for Each Universe

```bash
# US
python src/run.py \
    --universe-input data/universe_us.csv \
    --universe-output data/universe/tickers_us.csv \
    --prices-dir data/prices/us \
    --results-dir data/results/us

# Europe
python src/run.py \
    --universe-input data/universe_europe_filtered.csv \
    --universe-output data/universe/tickers_europe.csv \
    --prices-dir data/prices/europe \
    --results-dir data/results/europe

# Global
python src/run.py \
    --universe-input data/universe_global.csv \
    --universe-output data/universe/tickers_global.csv \
    --prices-dir data/prices/global \
    --results-dir data/results/global
```

### 5. Export Category Matches

```bash
# For each universe, find the latest screen result and export:
python src/export_candidates.py \
    --input data/results/us/screen_run_YYYYMMDD_HHMMSS.csv \
    --outdir data/results/us \
    --b-only
```

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

## Notes

- Building universes and fetching prices can take a while (especially for large universes)
- Price data is cached, so subsequent runs will only update recent data
- Results are timestamped, so you can keep historical runs
- Category matches are sorted by return percentage (descending)

