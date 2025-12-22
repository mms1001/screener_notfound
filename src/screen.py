# src/screen.py
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd


# ---- Defaults (market-convention friendly) ----
DEFAULT_WINDOW_5Y = 1260   # ~5y trading days
DEFAULT_WINDOW_52W = 252   # ~52w trading days
DEFAULT_WINDOW_90D = 63    # ~90 calendar days ≈ 63 trading days
DEFAULT_REBOUND_PCT = 0.30
DEFAULT_DRAWDOWN_PCT = 0.80


@dataclass
class ScreenConfig:
    window_5y: int = DEFAULT_WINDOW_5Y
    window_52w: int = DEFAULT_WINDOW_52W
    window_90d: int = DEFAULT_WINDOW_90D
    rebound_pct: float = DEFAULT_REBOUND_PCT
    drawdown_pct: float = DEFAULT_DRAWDOWN_PCT


# ----------------- IO helpers -----------------
def load_universe(universe_csv: Path) -> list[str]:
    df = pd.read_csv(universe_csv)
    if "ticker" not in df.columns:
        raise ValueError("Universe CSV must contain a 'ticker' column.")
    return sorted(df["ticker"].dropna().unique().tolist())


def load_prices_csv(prices_path: Path) -> pd.DataFrame:
    """
    Expected columns (from fetch_prices.py):
      date, open, high, low, close, adj_close, volume
    """
    df = pd.read_csv(prices_path, parse_dates=["date"])
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    # Ensure required column exists
    if "adj_close" not in df.columns:
        raise ValueError(f"Missing 'adj_close' in {prices_path.name}")
    # Clean
    df = df.dropna(subset=["date", "adj_close"])
    return df


def _last_n(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if df.empty:
        return df
    return df.iloc[-min(len(df), n):].copy()


def _fmt_date(dt: Optional[pd.Timestamp]) -> str:
    if dt is None or pd.isna(dt):
        return ""
    return pd.Timestamp(dt).date().isoformat()


# ----------------- Core logic -----------------
def _argmax(series: pd.Series) -> Tuple[float, pd.Timestamp]:
    idx = series.idxmax()
    return float(series.loc[idx]), pd.Timestamp(idx)


def _argmin(series: pd.Series) -> Tuple[float, pd.Timestamp]:
    idx = series.idxmin()
    return float(series.loc[idx]), pd.Timestamp(idx)


def _first_crossing_date(
    df: pd.DataFrame,
    start_date: pd.Timestamp,
    target: float,
) -> Optional[pd.Timestamp]:
    """
    Find first date after start_date where adj_close >= target.
    """
    if df.empty:
        return None
    after = df[df["date"] > start_date]
    if after.empty:
        return None
    hit = after[after["adj_close"] >= target]
    if hit.empty:
        return None
    return pd.Timestamp(hit.iloc[0]["date"])


def screen_ticker(prices: pd.DataFrame, cfg: ScreenConfig) -> dict:
    """
    Returns a dict with all output fields for a single ticker.
    Caller adds ticker/run_id.
    """
    out: dict = {}

    if prices.empty or len(prices) < max(50, cfg.window_90d):  # minimal sanity
        out.update(
            {
                "price_today": np.nan,
                "date_today": "",
                "qualifies_a": False,
                "qualifies_b": False,
                "reason": "insufficient_data",
            }
        )
        return out

    # Today
    last_row = prices.iloc[-1]
    price_today = float(last_row["adj_close"])
    date_today = pd.Timestamp(last_row["date"])
    out["price_today"] = price_today
    out["date_today"] = _fmt_date(date_today)

    # Rolling windows
    p5y = _last_n(prices, cfg.window_5y)
    p52w = _last_n(prices, cfg.window_52w)
    recent90 = _last_n(prices, cfg.window_90d)

    out["has_5y_history"] = len(p5y) >= min(cfg.window_5y, 600)
    out["has_52w_history"] = len(p52w) >= min(cfg.window_52w, 200)

    # ---- Category A ----
    qualifies_a = False

    # Default A fields (so CSV is consistent)
    out.update(
        {
            "peak_5y_price": np.nan,
            "peak_5y_date": "",
            "bottom_post_peak_price": np.nan,
            "bottom_post_peak_date": "",
            "bottom_drawdown_pct": np.nan,
            "hit30_from_bottom_date": "",
            "days_bottom_to_hit30": np.nan,
        }
    )

    if not p5y.empty:
        # Compute peak in 5y
        peak_price, peak_date = _argmax(p5y.set_index("date")["adj_close"])
        out["peak_5y_price"] = peak_price
        out["peak_5y_date"] = _fmt_date(peak_date)

        # Bottom after peak (critical to avoid "bottom before peak" bug)
        post_peak = p5y[p5y["date"] >= peak_date]
        if len(post_peak) >= 5:
            bottom_price, bottom_date = _argmin(post_peak.set_index("date")["adj_close"])
            out["bottom_post_peak_price"] = bottom_price
            out["bottom_post_peak_date"] = _fmt_date(bottom_date)

            if peak_price > 0:
                drawdown = 1.0 - (bottom_price / peak_price)
                out["bottom_drawdown_pct"] = drawdown

                if drawdown >= cfg.drawdown_pct and bottom_price > 0:
                    target = (1.0 + cfg.rebound_pct) * bottom_price
                    hit_date = _first_crossing_date(p5y, bottom_date, target)
                    if hit_date is not None:
                        out["hit30_from_bottom_date"] = _fmt_date(hit_date)
                        out["days_bottom_to_hit30"] = int((hit_date - bottom_date).days)

                        # "Recent" rule: the crossing must occur within the last 90d window
                        qualifies_a = hit_date >= recent90.iloc[0]["date"]

    out["qualifies_a"] = bool(qualifies_a)

    # ---- Category B ----
    qualifies_b = False

    out.update(
        {
            "low_52w_price": np.nan,
            "low_52w_date": "",
            "hit30_from_52wlow_date": "",
            "days_52wlow_to_hit30": np.nan,
        }
    )

    if not p52w.empty:
        low_price, low_date = _argmin(p52w.set_index("date")["adj_close"])
        out["low_52w_price"] = low_price
        out["low_52w_date"] = _fmt_date(low_date)

        if low_price > 0:
            target = (1.0 + cfg.rebound_pct) * low_price
            hit_date = _first_crossing_date(p52w, low_date, target)
            if hit_date is not None:
                out["hit30_from_52wlow_date"] = _fmt_date(hit_date)
                out["days_52wlow_to_hit30"] = int((hit_date - low_date).days)
                qualifies_b = hit_date >= recent90.iloc[0]["date"]

    out["qualifies_b"] = bool(qualifies_b)

    # ---- Optional extras (useful for 3rd filter) ----
    # Avg volume 30d (if volume exists)
    if "volume" in prices.columns:
        v30 = _last_n(prices.dropna(subset=["volume"]), 30)
        out["avg_volume_30d"] = float(v30["volume"].mean()) if not v30.empty else np.nan
    else:
        out["avg_volume_30d"] = np.nan

    # Volatility 90d (stdev of daily log returns using adj_close)
    if len(recent90) >= 20:
        ac = recent90["adj_close"].astype(float)
        rets = np.log(ac / ac.shift(1)).dropna()
        out["volatility_90d"] = float(rets.std(ddof=0)) if len(rets) else np.nan
    else:
        out["volatility_90d"] = np.nan

    out["reason"] = "ok"
    return out


# ----------------- Batch runner -----------------
def run_screen(
    universe_csv: Path,
    prices_dir: Path,
    output_csv: Path,
    cfg: ScreenConfig,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    tickers = load_universe(universe_csv)
    if limit:
        tickers = tickers[:limit]

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = []

    for i, t in enumerate(tickers, 1):
        price_path = prices_dir / f"{t}.csv"
        if not price_path.exists():
            rows.append(
                {
                    "run_id": run_id,
                    "ticker": t,
                    "price_today": np.nan,
                    "date_today": "",
                    "qualifies_a": False,
                    "qualifies_b": False,
                    "reason": "missing_price_file",
                }
            )
            continue

        try:
            prices = load_prices_csv(price_path)
            result = screen_ticker(prices, cfg)
            result["run_id"] = run_id
            result["ticker"] = t
            rows.append(result)
        except Exception as e:
            rows.append(
                {
                    "run_id": run_id,
                    "ticker": t,
                    "price_today": np.nan,
                    "date_today": "",
                    "qualifies_a": False,
                    "qualifies_b": False,
                    "reason": f"error:{type(e).__name__}",
                }
            )

        if i % 250 == 0:
            print(f"  {i:,}/{len(tickers):,} screened...")

    df_out = pd.DataFrame(rows)

    # Consistent column order (nice for Oliver/Excel)
    preferred_cols = [
        "run_id",
        "ticker",
        "date_today",
        "price_today",
        "qualifies_a",
        "qualifies_b",
        # A
        "peak_5y_date",
        "peak_5y_price",
        "bottom_post_peak_date",
        "bottom_post_peak_price",
        "bottom_drawdown_pct",
        "hit30_from_bottom_date",
        "days_bottom_to_hit30",
        # B
        "low_52w_date",
        "low_52w_price",
        "hit30_from_52wlow_date",
        "days_52wlow_to_hit30",
        # extras
        "avg_volume_30d",
        "volatility_90d",
        "has_5y_history",
        "has_52w_history",
        "reason",
    ]
    for c in preferred_cols:
        if c not in df_out.columns:
            df_out[c] = np.nan if c not in ("run_id", "ticker", "date_today", "reason") else ""

    df_out = df_out[preferred_cols].copy()

    MIN_PRICE = 10.0
    df_out = df_out[df_out["price_today"] >= MIN_PRICE]

    # Formatting for human readability
    price_cols = [
        "price_today",
        "price_90d_ago",
        "peak_5y_price",
        "bottom_post_peak_price",
        "low_52w_price",
    ]

    for c in price_cols:
        if c in df_out.columns:
            df_out[c] = df_out[c].astype(float).round(2)

    pct_cols = [
        "return_90d",
        "bottom_drawdown_pct",
        "rebound_from_bottom",
        "rebound_from_52wlow",
    ]

    for c in pct_cols:
        if c in df_out.columns:
            df_out[c] = (df_out[c].astype(float) * 100).round(1)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(output_csv, index=False)
    return df_out


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Screen tickers for 'cheap rebound' patterns (Category A & B)."
    )
    ap.add_argument("--universe", required=True, help="Path to data/universe/tickers.csv")
    ap.add_argument("--prices-dir", default="data/prices", help="Directory with per-ticker price CSVs")
    ap.add_argument("--output", required=True, help="Output results CSV path")
    ap.add_argument("--limit", type=int, default=None, help="Limit tickers for testing")
    ap.add_argument("--window-5y", type=int, default=DEFAULT_WINDOW_5Y)
    ap.add_argument("--window-52w", type=int, default=DEFAULT_WINDOW_52W)
    ap.add_argument("--window-90d", type=int, default=DEFAULT_WINDOW_90D)
    ap.add_argument("--rebound-pct", type=float, default=DEFAULT_REBOUND_PCT)
    ap.add_argument("--drawdown-pct", type=float, default=DEFAULT_DRAWDOWN_PCT)
    return ap.parse_args()


def main():
    args = parse_args()
    cfg = ScreenConfig(
        window_5y=args.window_5y,
        window_52w=args.window_52w,
        window_90d=args.window_90d,
        rebound_pct=args.rebound_pct,
        drawdown_pct=args.drawdown_pct,
    )

    df = run_screen(
        universe_csv=Path(args.universe),
        prices_dir=Path(args.prices_dir),
        output_csv=Path(args.output),
        cfg=cfg,
        limit=args.limit,
    )

    print("—" * 60)
    print(f"✅ Wrote results: {len(df):,} rows -> {Path(args.output).resolve()}")
    print("Top counts:")
    print(df[["qualifies_a", "qualifies_b"]].value_counts(dropna=False).head(10).to_string())


if __name__ == "__main__":
    main()

