# src/fetch_prices.py
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# Import currency fetching - handle both relative and absolute imports
try:
    from .meta import _fetch_currency_from_yfinance
except ImportError:
    # Fallback for when run as script
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.meta import _fetch_currency_from_yfinance


PRICE_COLS = [
    "Open",
    "High",
    "Low",
    "Close",
    "Adj Close",
    "Volume",
]

OUTPUT_COLS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


DEFAULT_YEARS_BACK = 6


def load_universe(path: Path) -> list[str]:
    df = pd.read_csv(path)
    if "ticker" not in df.columns:
        raise ValueError("Universe CSV must contain a 'ticker' column.")
    return sorted(df["ticker"].dropna().unique().tolist())


def _read_existing_prices(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    return df.sort_values("date")


def _download_prices(ticker: str, start: datetime) -> pd.DataFrame:
    df = yf.download(
        ticker,
        start=start.strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=False,
        actions=False,
    )
    if df.empty:
        return df

    df = df[PRICE_COLS].rename(columns=OUTPUT_COLS)
    df = df.reset_index().rename(columns={"Date": "date"})
    df["date"] = pd.to_datetime(df["date"])
    
    # Get currency and handle GBp scaling
    currency = _fetch_currency_from_yfinance(ticker)
    currency_upper = currency.upper() if currency else None
    
    # Check if currency is GBp (pence) - either explicitly "GBp" or "GBP" with pence-range prices
    is_gbp = currency_upper == "GBP"
    is_gbp_pence = (currency and len(currency) == 3 and 
                    currency[:2].upper() == "GB" and currency[2].lower() == 'p')
    
    if is_gbp or is_gbp_pence:
        # Check if prices are in pence range (typically > 10 for pence, < 10 for GBP)
        # If average price is > 10, likely in pence (GBp), scale down
        price_cols = ["open", "high", "low", "close", "adj_close"]
        available_prices = [df[col].dropna() for col in price_cols if col in df.columns]
        if available_prices:
            avg_price = pd.concat(available_prices).mean()
            # If explicitly GBp or average price > 10, scale to GBP
            if is_gbp_pence or avg_price > 10.0:
                for col in price_cols:
                    if col in df.columns:
                        df[col] = df[col] / 100.0
                print(f"  Scaling GBp -> GBP for {ticker}")
                # Update currency to GBP after scaling
                currency = "GBP"
    
    # Optionally add currency column
    if currency:
        df["currency"] = currency
    
    return df


def update_prices_for_ticker(
    ticker: str,
    prices_dir: Path,
    years_back: int = DEFAULT_YEARS_BACK,
) -> bool:
    out_path = prices_dir / f"{ticker}.csv"

    existing = _read_existing_prices(out_path)

    if existing is None:
        start = datetime.today() - timedelta(days=365 * years_back)
        new_data = _download_prices(ticker, start)
        if new_data.empty:
            return False
        final = new_data
    else:
        last_date = existing["date"].max()
        start = last_date + timedelta(days=1)
        if start.date() >= datetime.today().date():
            return True  # já está atualizado

        new_data = _download_prices(ticker, start)
        if new_data.empty:
            return True

        final = pd.concat([existing, new_data], ignore_index=True)
        final = final.drop_duplicates(subset=["date"]).sort_values("date")

    prices_dir.mkdir(parents=True, exist_ok=True)
    final.to_csv(out_path, index=False)
    return True


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Download and cache daily price data (CSV) for a universe of tickers."
    )
    ap.add_argument(
        "--universe",
        required=True,
        help="Path to universe CSV (must have a 'ticker' column).",
    )
    ap.add_argument(
        "--prices-dir",
        default="data/prices",
        help="Directory to store per-ticker CSV price files.",
    )
    ap.add_argument(
        "--years-back",
        type=int,
        default=DEFAULT_YEARS_BACK,
        help="How many years back to fetch on first download.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit of tickers (useful for testing).",
    )
    return ap.parse_args()


def main():
    args = parse_args()

    universe_path = Path(args.universe)
    prices_dir = Path(args.prices_dir)

    tickers = load_universe(universe_path)
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"📥 Fetching prices for {len(tickers):,} tickers")

    ok, fail = 0, 0
    for i, t in enumerate(tickers, 1):
        try:
            success = update_prices_for_ticker(t, prices_dir, args.years_back)
            if success:
                ok += 1
            else:
                fail += 1
            if i % 50 == 0:
                print(f"  {i:,}/{len(tickers):,} processed...")
        except Exception as e:
            fail += 1
            print(f"❌ {t}: {e}")

    print("—" * 50)
    print(f"✅ Success: {ok:,}")
    print(f"⚠️  Failed:  {fail:,}")
    print(f"📁 Data saved to: {prices_dir.resolve()}")


if __name__ == "__main__":
    main()

