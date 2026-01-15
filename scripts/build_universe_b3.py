#!/usr/bin/env python3
"""
Build B3 (Brazil) stock universe from seed CSV file.

Reads data/seed_b3.csv, normalizes tickers, and writes data/universe_b3.csv.
No market cap filtering is applied at this stage.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def normalize_ticker(ticker: str) -> str:
    """
    Normalize ticker: strip whitespace, uppercase, ensure .SA suffix.
    
    Args:
        ticker: Raw ticker string
        
    Returns:
        Normalized ticker (e.g., "PETR4.SA")
    """
    ticker = ticker.strip().upper()
    if not ticker.endswith(".SA"):
        ticker += ".SA"
    return ticker


def main():
    parser = argparse.ArgumentParser(
        description="Build B3 stock universe from seed CSV"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of tickers to process (for testing)",
    )
    args = parser.parse_args()
    
    # File paths
    seed_path = Path("data/seed_b3.csv")
    output_path = Path("data/universe_b3.csv")
    
    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if seed file exists
    if not seed_path.exists():
        raise FileNotFoundError(f"Seed file not found: {seed_path}")
    
    # Read seed CSV
    print(f"📖 Reading seed file: {seed_path}")
    df = pd.read_csv(seed_path)
    
    if "ticker" not in df.columns:
        raise ValueError(f"Expected 'ticker' column in {seed_path}")
    
    # Normalize tickers
    print("🔄 Normalizing tickers...")
    df["ticker"] = df["ticker"].apply(normalize_ticker)
    
    # Remove duplicates
    df = df.drop_duplicates(subset=["ticker"]).reset_index(drop=True)
    total_count = len(df)
    
    # Apply limit if specified
    if args.limit:
        df = df.head(args.limit)
        print(f"⚠️  Limited to {len(df):,} tickers for testing")
    
    # Write output
    df[["ticker"]].to_csv(output_path, index=False)
    
    # Log counts
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"Total tickers read:     {total_count:,}")
    print(f"Tickers written:       {len(df):,}")
    print("=" * 60)
    print(f"✅ B3 universe saved to: {output_path}")


if __name__ == "__main__":
    main()

