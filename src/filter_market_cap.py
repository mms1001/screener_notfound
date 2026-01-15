from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

import logging
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)

from src.meta import get_ticker_meta_batch
from src.fx import convert_to_usd


def load_universe(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "ticker" not in df.columns:
        if "symbol" in df.columns:
            df = df.rename(columns={"symbol": "ticker"})
        else:
            raise ValueError("Universe CSV must have a 'ticker' column (or 'symbol').")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df[df["ticker"].str.len() > 0].drop_duplicates(subset=["ticker"])
    return df


def _get_market_cap(ticker: str) -> Optional[int]:
    try:
        t = yf.Ticker(ticker)

        # fast_info é mais leve e geralmente suficiente
        fi = getattr(t, "fast_info", None)
        if fi and fi.get("marketCap") is not None:
            return int(fi["marketCap"])

        # fallback
        info = t.info
        mc = info.get("marketCap")
        return int(mc) if mc is not None else None
    except Exception:
        return None


def _get_price_today(ticker: str) -> Optional[float]:
    """Get today's price using fast_info (lightweight, no full history download)."""
    try:
        t = yf.Ticker(ticker)
        fi = getattr(t, "fast_info", None)
        
        if fi:
            # Try multiple possible keys for price
            price = None
            for key in ["last_price", "lastPrice", "regularMarketPrice"]:
                if hasattr(fi, key):
                    val = getattr(fi, key)
                    if val is not None:
                        price = float(val)
                        break
                elif isinstance(fi, dict) and key in fi:
                    val = fi[key]
                    if val is not None:
                        price = float(val)
                        break
            
            if price is not None:
                return price
        
        # Fallback to info (slower but more complete)
        try:
            info = t.info
            for key in ["regularMarketPrice", "currentPrice", "previousClose"]:
                if key in info and info[key] is not None:
                    return float(info[key])
        except Exception:
            pass
        
        return None
    except Exception:
        return None


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Filter tickers by minimum market cap (USD) and price (USD) using Yahoo (yfinance).")
    ap.add_argument("--input", required=True, help="Input universe CSV with column 'ticker'.")
    ap.add_argument("--output", required=True, help="Output CSV (filtered) path.")
    ap.add_argument("--min-mcap", type=int, default=400_000_000, help="Minimum market cap in USD (default: 400,000,000 = $400M USD).")
    ap.add_argument("--min-price-usd", type=float, default=1.0, help="Minimum price in USD (default: 1.0 = $1.00 USD).")
    ap.add_argument("--cache", default="data/universe/market_caps.csv", help="Cache file path for market caps.")
    ap.add_argument("--sleep", type=float, default=0.05, help="Sleep seconds between requests.")
    ap.add_argument("--limit", type=int, default=None, help="Optional limit tickers for testing.")
    return ap.parse_args()


def main():
    args = parse_args()
    inp = Path(args.input)
    out = Path(args.output)
    cache_path = Path(args.cache)

    df = load_universe(inp)
    if args.limit:
        df = df.head(args.limit).copy()

    # Load cache if exists (non-empty)
    cache = pd.DataFrame(columns=["ticker", "market_cap", "asof"])
    if cache_path.exists() and cache_path.stat().st_size > 10:
        cache = pd.read_csv(cache_path)
        cache["ticker"] = cache["ticker"].astype(str).str.upper()

    cache_map = dict(zip(cache["ticker"], cache["market_cap"]))

    # Step 1: Fetch market caps
    results = []
    ok, miss = 0, 0

    for i, t in enumerate(df["ticker"].tolist(), 1):
        mc = cache_map.get(t)
        if mc is not None and (pd.isna(mc) or mc == ""):
            mc = None

        if mc is None:
            mc = _get_market_cap(t)
            time.sleep(args.sleep)

        if mc is None:
            miss += 1
        else:
            ok += 1

        results.append({"ticker": t, "market_cap": mc, "asof": datetime.now().date().isoformat()})

        if i % 200 == 0:
            print(f"  {i:,}/{len(df):,} market caps checked...")

    caps_df = pd.DataFrame(results)
    
    # Step 1b: Fetch prices (using fast_info for speed)
    print(f"\n💲 Fetching prices for {len(caps_df):,} tickers...")
    price_results = []
    price_ok, price_miss = 0, 0
    
    for i, t in enumerate(caps_df["ticker"].tolist(), 1):
        price = _get_price_today(t)
        if price is None:
            price_miss += 1
        else:
            price_ok += 1
        
        price_results.append({"ticker": t, "price_today_local": price})
        time.sleep(args.sleep)
        
        if i % 200 == 0:
            print(f"  {i:,}/{len(caps_df):,} prices checked...")
    
    prices_df = pd.DataFrame(price_results)
    caps_df = caps_df.merge(prices_df, on="ticker", how="left")

    # Step 2: Fetch currencies (batch for efficiency)
    print(f"\n💱 Fetching currencies for {len(caps_df):,} tickers...")
    tickers_list = caps_df["ticker"].tolist()
    meta_batch = get_ticker_meta_batch(tickers_list, force_refresh=False)
    
    # Add currency to results
    caps_df["currency"] = caps_df["ticker"].apply(lambda t: meta_batch.get(t, {}).get("currency") if t in meta_batch else None)

    # Step 3: Convert market cap to USD
    print(f"💵 Converting market caps to USD...")
    caps_df["market_cap_usd"] = caps_df.apply(
        lambda row: convert_to_usd(row["market_cap"], row["currency"]),
        axis=1
    )
    
    # Step 4: Convert prices to USD
    print(f"💵 Converting prices to USD...")
    caps_df["price_usd"] = caps_df.apply(
        lambda row: convert_to_usd(row["price_today_local"], row["currency"]),
        axis=1
    )

    # Update cache (keep newest per ticker)
    merged_cache = pd.concat([cache, caps_df[["ticker", "market_cap", "asof"]]], ignore_index=True)
    merged_cache = merged_cache.sort_values(["ticker", "asof"]).drop_duplicates(subset=["ticker"], keep="last")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    merged_cache.to_csv(cache_path, index=False)

    # Merge market cap and price data with original universe to preserve all columns (e.g., title)
    # Use left merge to keep all original columns
    df_merged = df.merge(caps_df[["ticker", "market_cap", "currency", "market_cap_usd", "price_today_local", "price_usd"]], on="ticker", how="left")
    
    # Filter by market cap and price thresholds (USD)
    # Drop tickers with missing currency, market cap, or price
    missing_currency = df_merged["currency"].isna().sum()
    missing_mcap = df_merged["market_cap"].isna().sum()
    missing_mcap_usd = df_merged["market_cap_usd"].isna().sum()
    missing_price = df_merged["price_today_local"].isna().sum()
    missing_price_usd = df_merged["price_usd"].isna().sum()
    conversion_failed_mcap = ((df_merged["market_cap"].notna()) & (df_merged["currency"].notna()) & (df_merged["market_cap_usd"].isna())).sum()
    conversion_failed_price = ((df_merged["price_today_local"].notna()) & (df_merged["currency"].notna()) & (df_merged["price_usd"].isna())).sum()
    
    # Log reasons for dropping tickers
    if missing_currency > 0:
        print(f"⚠️  Dropping {missing_currency:,} tickers: missing currency")
    if missing_mcap > 0:
        print(f"⚠️  Dropping {missing_mcap:,} tickers: missing market cap")
    if missing_price > 0:
        print(f"⚠️  Dropping {missing_price:,} tickers: missing price")
    if conversion_failed_mcap > 0:
        print(f"⚠️  Dropping {conversion_failed_mcap:,} tickers: market cap currency conversion failed")
    if conversion_failed_price > 0:
        print(f"⚠️  Dropping {conversion_failed_price:,} tickers: price currency conversion failed")
    
    # Filter: must have market_cap_usd >= threshold AND price_usd >= threshold
    filtered = df_merged[
        (df_merged["market_cap_usd"].notna()) & 
        (df_merged["market_cap_usd"] >= args.min_mcap) &
        (df_merged["price_usd"].notna()) &
        (df_merged["price_usd"] >= args.min_price_usd)
    ].copy()
    filtered = filtered.sort_values("ticker").reset_index(drop=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    # Output CSV with: ticker, currency, market_cap, market_cap_usd, price_today_local, price_usd, plus any original columns
    # Ensure these columns are present in the output
    output_columns = ["ticker", "currency", "market_cap", "market_cap_usd", "price_today_local", "price_usd"]
    # Add any other columns from original df that aren't already in output_columns
    for col in filtered.columns:
        if col not in output_columns:
            output_columns.append(col)
    
    filtered[output_columns].to_csv(out, index=False)

    print("—" * 60)
    print(f"✅ Market cap fetched: {ok:,} tickers")
    print(f"⚠️  Missing market cap: {miss:,} tickers")
    print(f"✅ Price fetched: {price_ok:,} tickers")
    print(f"⚠️  Missing price: {price_miss:,} tickers")
    print(f"✅ Filtered (market cap >= ${args.min_mcap:,} USD, price >= ${args.min_price_usd:.2f} USD): {len(filtered):,} tickers -> {out}")
    print(f"🗃  Cache updated: {cache_path}")


if __name__ == "__main__":
    main()
