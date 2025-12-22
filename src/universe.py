# src/universe.py
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


TICKER_COL_CANDIDATES = ["ticker", "symbol", "Symbols", "Symbol", "Tickers", "Ticker"]
VALID_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,15}$")


@dataclass
class UniverseConfig:
    keep_only_valid_tickers: bool = True


def _infer_ticker_col(df: pd.DataFrame) -> str:
    for c in TICKER_COL_CANDIDATES:
        if c in df.columns:
            return c
    return df.columns[0]


def _normalize_ticker(s: str) -> str:
    s = str(s).strip().upper()
    s = s.replace("/", ".").replace(" ", "")
    return s


def _is_valid_ticker(t: str) -> bool:
    return bool(VALID_TICKER_RE.match(t))


def build_universe_from_many_csvs(inputs: list[Path], cfg: UniverseConfig, output_csv: Path) -> pd.DataFrame:
    frames = []
    for p in inputs:
        df = pd.read_csv(p)
        ticker_col = _infer_ticker_col(df)
        df = df.rename(columns={ticker_col: "ticker"})
        df["ticker"] = df["ticker"].apply(_normalize_ticker)
        frames.append(df)

    merged = pd.concat(frames, ignore_index=True)
    merged = merged[merged["ticker"].astype(str).str.len() > 0]
    merged = merged.drop_duplicates(subset=["ticker"], keep="first")

    if cfg.keep_only_valid_tickers:
        merged = merged[merged["ticker"].apply(_is_valid_ticker)]

    merged = merged.sort_values("ticker").reset_index(drop=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_csv, index=False)
    return merged


def build_universe_from_sec(cfg: UniverseConfig, output_csv: Path) -> pd.DataFrame:
    """
    Pull tickers from SEC public file.
    This provides a large US-listed company universe (not perfect, but robust for MVP).
    """
    # SEC requires a User-Agent identifying your app/email (good practice; reduces blocking)
    headers = {
        "User-Agent": "cheap-rebound-screener (contact: msimpliciosilva24@example.com)"
    }

    url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    # data is dict: { "0": {"cik_str":..., "ticker":..., "title":...}, ... }
    rows = []
    for _, obj in data.items():
        t = obj.get("ticker", "")
        if t:
            rows.append(
                {
                    "ticker": _normalize_ticker(t),
                    "title": obj.get("title", ""),
                    "cik": obj.get("cik_str", None),
                }
            )

    df = pd.DataFrame(rows)
    df = df[df["ticker"].astype(str).str.len() > 0]
    df = df.drop_duplicates(subset=["ticker"], keep="first")

    if cfg.keep_only_valid_tickers:
        df = df[df["ticker"].apply(_is_valid_ticker)]

    df = df.sort_values("ticker").reset_index(drop=True)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    return df


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build a ticker universe CSV.")
    ap.add_argument("--source", choices=["csv", "sec"], default="csv", help="Universe source.")
    ap.add_argument("--input", nargs="+", help="CSV inputs when --source=csv")
    ap.add_argument("--output", required=True, help="Output CSV path, e.g. data/universe/tickers.csv")
    ap.add_argument("--keep-only-valid", action="store_true", help="Keep only tickers matching basic regex.")
    return ap.parse_args()


def main():
    args = parse_args()
    cfg = UniverseConfig(keep_only_valid_tickers=args.keep_only_valid)

    out = Path(args.output)

    if args.source == "sec":
        df = build_universe_from_sec(cfg, out)
    else:
        if not args.input:
            raise SystemExit("When --source=csv, you must pass --input <file1.csv> [file2.csv...]")
        df = build_universe_from_many_csvs([Path(p) for p in args.input], cfg, out)

    print(f"✅ Universe built: {len(df):,} tickers -> {out}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
