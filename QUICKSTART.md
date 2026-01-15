# Quick Start Guide

## Building European Stock Universe

The `scripts/build_universe_europe.py` script builds a European stock universe by:
1. Fetching company listings from Wikidata (SIX Swiss, XETRA, Euronext Paris, Nasdaq Stockholm)
2. Converting tickers to Yahoo Finance format
3. Validating tickers exist in Yahoo Finance
4. Filtering by price >= $10 USD and market cap >= $200M USD

### Running the Script

```bash
# Activate virtual environment (if using one)
source venv/bin/activate  # On macOS/Linux
# or
venv\Scripts\activate  # On Windows

# Run the universe builder
python scripts/build_universe_europe.py
```

This will generate two output files:
- `data/universe_europe_all.txt` - All validated Yahoo Finance tickers
- `data/universe_europe_filtered.txt` - Tickers that pass price and market cap filters

### Options

```bash
# Limit number of tickers for testing
python scripts/build_universe_europe.py --limit 100

# Custom output paths
python scripts/build_universe_europe.py \
    --output-all data/custom_all.txt \
    --output-filtered data/custom_filtered.txt
```

### Using the Generated Ticker File

The script outputs `.txt` files with one ticker per line. To use them with the existing pipeline, convert to CSV format:

#### Option 1: Convert to CSV manually

```bash
# Convert .txt to CSV with 'ticker' column
python -c "
import pandas as pd
df = pd.read_csv('data/universe_europe_filtered.txt', header=None, names=['ticker'])
df.to_csv('data/universe_europe_filtered.csv', index=False)
"
```

#### Option 2: Use with the existing pipeline

```bash
# Build universe from the CSV
python src/universe.py \
    --input data/universe_europe_filtered.csv \
    --output data/universe/tickers.csv

# Fetch prices
python src/fetch_prices.py \
    --universe data/universe/tickers.csv \
    --prices-dir data/prices \
    --years-back 6

# Run screening
python src/screen.py \
    --universe data/universe/tickers.csv \
    --prices-dir data/prices \
    --output data/results/screen_results.csv
```

#### Option 3: Use the end-to-end runner

```bash
# First convert .txt to CSV
python -c "
import pandas as pd
df = pd.read_csv('data/universe_europe_filtered.txt', header=None, names=['ticker'])
df.to_csv('data/universe_europe_filtered.csv', index=False)
"

# Then run the full pipeline
python src/run.py \
    --universe-input data/universe_europe_filtered.csv \
    --universe-output data/universe/tickers.csv \
    --prices-dir data/prices \
    --years-back 6 \
    --results-dir data/results
```

### Notes

- The script includes rate limiting to avoid throttling from Wikidata and Yahoo Finance
- FX rates are cached for the run (EUR, CHF, SEK to USD)
- Validation and filtering may take some time depending on the number of tickers
- Missing price or market cap data will be logged in the summary

