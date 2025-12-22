# src/fetch_prices.py
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf


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

