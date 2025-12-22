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


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Filter tickers by minimum market cap using Yahoo (yfinance).")
    ap.add_argument("--input", required=True, help="Input universe CSV with column 'ticker'.")
    ap.add_argument("--output", required=True, help="Output CSV (filtered) path.")
    ap.add_argument("--min-mcap", type=int, default=200_000_000, help="Minimum market cap in USD (default 200M).")
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

    # Update cache (keep newest per ticker)
    merged_cache = pd.concat([cache, caps_df], ignore_index=True)
    merged_cache = merged_cache.sort_values(["ticker", "asof"]).drop_duplicates(subset=["ticker"], keep="last")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    merged_cache.to_csv(cache_path, index=False)

    filtered = caps_df[caps_df["market_cap"].fillna(0) >= args.min_mcap].copy()
    filtered = filtered.sort_values("ticker").reset_index(drop=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    filtered[["ticker", "market_cap"]].to_csv(out, index=False)

    print("—" * 60)
    print(f"✅ Market cap fetched: {ok:,} tickers")
    print(f"⚠️  Missing market cap: {miss:,} tickers")
    print(f"✅ Filtered (>= {args.min_mcap:,}): {len(filtered):,} tickers -> {out}")
    print(f"🗃  Cache updated: {cache_path}")


if __name__ == "__main__":
    main()
