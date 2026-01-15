#!/usr/bin/env python3
"""
Build European stock universe from Wikidata SPARQL queries.

Fetches companies from:
- SIX Swiss Exchange (.SW)
- XETRA / Germany (.DE)
- Euronext Paris (.PA)
- Nasdaq Stockholm (.ST)

Validates tickers with Yahoo Finance and filters by price >= $1 USD
and market cap >= $400M USD (defaults, configurable via CLI flags).
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

# Wikidata SPARQL endpoint
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"

# Exchange mapping: Wikidata QID -> Yahoo suffix
EXCHANGE_MAP = {
    "Q661834": ".SW",   # SIX Swiss Exchange
    "Q819468": ".DE",   # Xetra
    "Q2385849": ".PA",  # Euronext Paris
    "Q1019992": ".ST",  # Nasdaq Stockholm
}

# Currency mapping: Exchange -> Yahoo FX ticker
CURRENCY_MAP = {
    ".SW": "CHFUSD=X",   # Swiss Franc
    ".DE": "EURUSD=X",   # Euro
    ".PA": "EURUSD=X",   # Euro
    ".ST": "SEKUSD=X",   # Swedish Krona
}

# Default filter thresholds (can be overridden via CLI)
DEFAULT_MIN_PRICE_USD = 1.0
DEFAULT_MIN_MARKET_CAP_USD = 400_000_000

# Rate limiting
YFINANCE_DELAY = 0.5  # seconds between yfinance requests
BATCH_SIZE = 10  # validate in batches with delays

# ISIN pattern: 2 letters + 10 alphanumeric characters
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")


def fetch_wikidata_companies() -> pd.DataFrame:
    """Fetch companies from Wikidata using SPARQL query."""
    query = """
    SELECT ?company ?companyLabel ?exchange ?exchangeLabel ?ticker WHERE {
      VALUES ?exchange {
        wd:Q661834   # SIX Swiss Exchange
        wd:Q819468   # Xetra
        wd:Q2385849  # Euronext Paris
        wd:Q1019992  # Nasdaq Stockholm
      }
      ?company p:P414 ?listingStatement .
      ?listingStatement ps:P414 ?exchange .
      ?listingStatement pq:P249 ?ticker .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    """
    
    print("📡 Fetching companies from Wikidata...")
    headers = {
        "User-Agent": "European-Stock-Universe-Builder/1.0 (contact: msimpliciosilva24@example.com)",
        "Accept": "application/sparql-results+json",
    }
    
    params = {"query": query, "format": "json"}
    
    try:
        response = requests.get(WIKIDATA_ENDPOINT, params=params, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch from Wikidata: {e}")
    
    # Parse SPARQL JSON results
    bindings = data.get("results", {}).get("bindings", [])
    rows = []
    
    for binding in bindings:
        company_qid = binding.get("company", {}).get("value", "").split("/")[-1]
        company_label = binding.get("companyLabel", {}).get("value", "")
        exchange_qid = binding.get("exchange", {}).get("value", "").split("/")[-1]
        exchange_label = binding.get("exchangeLabel", {}).get("value", "")
        ticker = binding.get("ticker", {}).get("value", "").strip().upper()
        
        if ticker and exchange_qid in EXCHANGE_MAP:
            rows.append({
                "company_qid": company_qid,
                "company_label": company_label,
                "exchange_qid": exchange_qid,
                "exchange_label": exchange_label,
                "ticker_raw": ticker,
            })
    
    df = pd.DataFrame(rows)
    print(f"✅ Fetched {len(df):,} company listings from Wikidata")
    return df


def is_isin_like(ticker: str) -> bool:
    """Check if ticker looks like an ISIN (2 letters + 10 alphanumeric)."""
    return bool(ISIN_PATTERN.match(ticker))


def convert_to_yahoo_ticker(ticker_raw: str, exchange_qid: str) -> Optional[str]:
    """Convert raw ticker to Yahoo Finance format."""
    if exchange_qid not in EXCHANGE_MAP:
        return None
    
    suffix = EXCHANGE_MAP[exchange_qid]
    # Clean ticker: remove spaces, normalize (keep hyphens for Swedish tickers like VOLV-B.ST)
    ticker_clean = ticker_raw.replace(" ", "").upper()
    
    # Skip ISIN-like symbols (they're not tickers)
    if is_isin_like(ticker_clean):
        return None
    
    # If already has suffix, don't duplicate
    if ticker_clean.endswith(suffix):
        return ticker_clean
    
    return f"{ticker_clean}{suffix}"


def validate_ticker_and_get_info(ticker: str) -> tuple[bool, Optional[dict]]:
    """
    Validate ticker exists in Yahoo Finance and fetch info in one call.
    Returns (is_valid, info_dict) where info_dict may be None if unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        # Try to get info first (faster and contains price/market cap)
        info = stock.info
        if info and len(info) > 0:
            # If we have info, ticker is valid
            return (True, info)
        # Fallback: try history if info is missing/empty
        hist = stock.history(period="5d", raise_errors=True)
        if not hist.empty:
            return (True, None)  # Valid but no info available
        return (False, None)
    except Exception:
        return (False, None)


def get_fx_rate(fx_ticker: str) -> float:
    """Get current FX rate from Yahoo Finance."""
    try:
        fx = yf.Ticker(fx_ticker)
        info = fx.info
        # Try different possible fields
        rate = info.get("regularMarketPrice") or info.get("previousClose") or info.get("ask")
        if rate:
            return float(rate)
        # Fallback: try history
        hist = fx.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        return 1.0
    except Exception as e:
        print(f"⚠️  Warning: Could not fetch FX rate for {fx_ticker}, using 1.0: {e}")
        return 1.0


def get_price_and_market_cap_from_info(info: dict, fx_rate: float) -> tuple[Optional[float], Optional[float]]:
    """
    Extract price and market cap from info dict and convert to USD.
    Returns (price_usd, market_cap_usd) or (None, None) if unavailable.
    """
    if not info or len(info) == 0:
        return (None, None)
    
    try:
        # Get price (try multiple fields)
        price = (
            info.get("currentPrice") or
            info.get("regularMarketPrice") or
            info.get("previousClose") or
            info.get("ask") or
            info.get("bid")
        )
        
        # Get market cap (only use marketCap, not enterpriseValue)
        market_cap = info.get("marketCap")
        
        # If price/marketCap are in local currency, convert to USD
        # Note: Yahoo Finance typically returns values in the stock's trading currency
        currency = info.get("currency", "").upper()
        if currency and currency != "USD":
            if price is not None:
                price = price * fx_rate
            if market_cap is not None:
                market_cap = market_cap * fx_rate
        
        return (float(price) if price is not None else None,
                float(market_cap) if market_cap is not None else None)
    
    except Exception:
        return (None, None)


def main():
    parser = argparse.ArgumentParser(
        description="Build European stock universe from Wikidata"
    )
    parser.add_argument(
        "--output-all",
        default="data/universe_europe_all.txt",
        help="Output file for all validated tickers",
    )
    parser.add_argument(
        "--output-filtered",
        default="data/universe_europe_filtered.txt",
        help="Output file for filtered tickers (price >= $1 USD, market cap >= $400M USD by default)",
    )
    parser.add_argument(
        "--min-mcap-usd",
        type=int,
        default=DEFAULT_MIN_MARKET_CAP_USD,
        help=f"Minimum market cap in USD (default: {DEFAULT_MIN_MARKET_CAP_USD:,})",
    )
    parser.add_argument(
        "--min-price-usd",
        type=float,
        default=DEFAULT_MIN_PRICE_USD,
        help=f"Minimum price in USD (default: {DEFAULT_MIN_PRICE_USD:.2f})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of tickers to process (for testing)",
    )
    args = parser.parse_args()
    
    # Use CLI-provided thresholds
    min_price_usd = args.min_price_usd
    min_market_cap_usd = args.min_mcap_usd
    
    # Create output directories
    output_all = Path(args.output_all)
    output_filtered = Path(args.output_filtered)
    output_all.parent.mkdir(parents=True, exist_ok=True)
    output_filtered.parent.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Fetch from Wikidata
    df_wikidata = fetch_wikidata_companies()
    total_listings_fetched = len(df_wikidata)
    
    if df_wikidata.empty:
        print("❌ No companies found in Wikidata")
        return
    
    # Step 2: Convert to Yahoo tickers (filtering out ISIN-like symbols)
    print("\n🔄 Converting tickers to Yahoo Finance format (filtering ISIN-like symbols)...")
    df_wikidata["yahoo_ticker"] = df_wikidata.apply(
        lambda row: convert_to_yahoo_ticker(row["ticker_raw"], row["exchange_qid"]),
        axis=1
    )
    isin_filtered_count = df_wikidata["yahoo_ticker"].isna().sum()
    df_wikidata = df_wikidata[df_wikidata["yahoo_ticker"].notna()]
    df_wikidata = df_wikidata.drop_duplicates(subset=["yahoo_ticker"])
    total_unique_yahoo = len(df_wikidata)
    
    print(f"✅ Converted to {len(df_wikidata):,} unique Yahoo tickers")
    if isin_filtered_count > 0:
        print(f"   └─ Filtered out {isin_filtered_count:,} ISIN-like symbols")
    
    if args.limit:
        df_wikidata = df_wikidata.head(args.limit)
        print(f"⚠️  Limited to {len(df_wikidata):,} tickers for testing")
    
    # Step 3: Validate tickers and cache info to avoid redundant calls
    print("\n🔍 Validating tickers with Yahoo Finance...")
    validated_tickers = []
    info_cache = {}  # ticker -> info dict (or None if valid but no info)
    invalid_count = 0
    
    for i, (_, row) in enumerate(df_wikidata.iterrows(), start=1):
        ticker = row["yahoo_ticker"]
        is_valid, info = validate_ticker_and_get_info(ticker)
        if is_valid:
            validated_tickers.append(ticker)
            info_cache[ticker] = info
        else:
            invalid_count += 1
        
        if i % BATCH_SIZE == 0:
            print(f"  Validated {i:,}/{len(df_wikidata):,}... (valid: {len(validated_tickers):,}, invalid: {invalid_count:,})")
            time.sleep(YFINANCE_DELAY)
        else:
            time.sleep(YFINANCE_DELAY / BATCH_SIZE)
    
    print(f"✅ Validated: {len(validated_tickers):,} valid, {invalid_count:,} invalid")
    
    # Write all validated tickers
    with open(output_all, "w") as f:
        for ticker in sorted(validated_tickers):
            f.write(f"{ticker}\n")
    print(f"📄 Wrote {len(validated_tickers):,} validated tickers to {output_all}")
    
    # Step 4: Filter by price and market cap
    print(f"\n💰 Filtering by price >= ${min_price_usd:.2f} USD and market cap >= ${min_market_cap_usd/1e6:.0f}M USD...")
    
    # Cache FX rates (currency -> FX ticker mapping)
    CURRENCY_TO_FX = {
        "EUR": "EURUSD=X",
        "CHF": "CHFUSD=X",
        "SEK": "SEKUSD=X",
    }
    fx_rates = {}
    # Pre-fetch common FX rates
    for suffix, fx_ticker in CURRENCY_MAP.items():
        if fx_ticker not in fx_rates:
            print(f"  Fetching FX rate for {fx_ticker}...")
            fx_rates[fx_ticker] = get_fx_rate(fx_ticker)
            time.sleep(YFINANCE_DELAY)
    
    filtered_tickers = []
    missing_info = 0
    missing_price = 0
    missing_market_cap = 0
    below_price_threshold = 0
    below_market_cap_threshold = 0
    
    # Track samples for logging
    samples_missing_info = []
    samples_missing_price = []
    samples_missing_market_cap = []
    samples_below_price = []
    samples_below_market_cap = []
    
    for i, ticker in enumerate(validated_tickers, start=1):
        # Use cached info if available, otherwise skip (shouldn't happen often)
        info = info_cache.get(ticker)
        if info is None:
            # No info available, skip filtering (keep in "all" but not in "filtered")
            missing_info += 1
            if len(samples_missing_info) < 5:
                samples_missing_info.append(ticker)
            continue
        
        # Determine FX rate: prefer currency field, fallback to suffix
        fx_rate = 1.0
        currency = (info or {}).get("currency", "").upper()
        
        if currency == "USD":
            fx_rate = 1.0
        elif currency in CURRENCY_TO_FX:
            fx_ticker = CURRENCY_TO_FX[currency]
            if fx_ticker not in fx_rates:
                print(f"  Fetching FX rate for {fx_ticker}...")
                fx_rates[fx_ticker] = get_fx_rate(fx_ticker)
                time.sleep(YFINANCE_DELAY)
            fx_rate = fx_rates.get(fx_ticker, 1.0)
        else:
            # Fallback to suffix-based method
            for suffix, fx_ticker in CURRENCY_MAP.items():
                if ticker.endswith(suffix):
                    fx_rate = fx_rates.get(fx_ticker, 1.0)
                    break
        
        price_usd, market_cap_usd = get_price_and_market_cap_from_info(info, fx_rate)
        
        if price_usd is None:
            missing_price += 1
            if len(samples_missing_price) < 5:
                samples_missing_price.append(ticker)
        elif market_cap_usd is None:
            missing_market_cap += 1
            if len(samples_missing_market_cap) < 5:
                samples_missing_market_cap.append(ticker)
        elif price_usd < min_price_usd:
            below_price_threshold += 1
            if len(samples_below_price) < 5:
                samples_below_price.append(ticker)
        elif market_cap_usd < min_market_cap_usd:
            below_market_cap_threshold += 1
            if len(samples_below_market_cap) < 5:
                samples_below_market_cap.append(ticker)
        else:
            filtered_tickers.append(ticker)
        
        if i % BATCH_SIZE == 0:
            print(f"  Filtered {i:,}/{len(validated_tickers):,}... (passed: {len(filtered_tickers):,})")
            time.sleep(YFINANCE_DELAY)
        else:
            time.sleep(YFINANCE_DELAY / BATCH_SIZE)
    
    # Write filtered tickers
    with open(output_filtered, "w") as f:
        for ticker in sorted(filtered_tickers):
            f.write(f"{ticker}\n")
    
    # Summary with detailed counts and samples
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"Total listings fetched from Wikidata: {total_listings_fetched:,}")
    print(f"Total unique Yahoo tickers (post-dedupe): {total_unique_yahoo:,}")
    print(f"Total validated with Yahoo:         {len(validated_tickers):,}")
    print(f"  └─ Invalid (not found):           {invalid_count:,}")
    print(f"\nFiltering results:")
    print(f"  └─ Passed all filters:            {len(filtered_tickers):,}")
    print(f"  └─ Excluded - missing info:      {missing_info:,}")
    if samples_missing_info:
        print(f"      Sample: {', '.join(samples_missing_info[:5])}")
    print(f"  └─ Excluded - missing price:     {missing_price:,}")
    if samples_missing_price:
        print(f"      Sample: {', '.join(samples_missing_price[:5])}")
    print(f"  └─ Excluded - missing marketCap: {missing_market_cap:,}")
    if samples_missing_market_cap:
        print(f"      Sample: {', '.join(samples_missing_market_cap[:5])}")
    print(f"  └─ Excluded - price < ${min_price_usd:.2f} USD: {below_price_threshold:,}")
    if samples_below_price:
        print(f"      Sample: {', '.join(samples_below_price[:5])}")
    print(f"  └─ Excluded - mcap < ${min_market_cap_usd/1e6:.0f}M USD: {below_market_cap_threshold:,}")
    if samples_below_market_cap:
        print(f"      Sample: {', '.join(samples_below_market_cap[:5])}")
    print("=" * 60)
    print(f"✅ All validated tickers:  {output_all}")
    print(f"✅ Filtered tickers:      {output_filtered}")


if __name__ == "__main__":
    main()

