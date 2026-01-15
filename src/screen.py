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
DEFAULT_WINDOW_180D = 180  # ~180 trading days
DEFAULT_WINDOW_30D = 30    # ~30 trading days
DEFAULT_REBOUND_PCT = 0.30
DEFAULT_DRAWDOWN_PCT = 0.80


@dataclass
class ScreenConfig:
    window_5y: int = DEFAULT_WINDOW_5Y
    window_52w: int = DEFAULT_WINDOW_52W
    window_90d: int = DEFAULT_WINDOW_90D
    window_180d: int = DEFAULT_WINDOW_180D
    window_30d: int = DEFAULT_WINDOW_30D
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
    # Force numeric conversion to handle dirty CSV data
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    # Clean - drop rows where date or adj_close is NaN
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
                "price_90d_ago": np.nan,
                "return_90d": np.nan,
                "qualifies_a": False,
                "qualifies_b": False,
                "qualifies_c": False,
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

    # Price guard 1: Global guard - if price_today is missing or <= 1.0, disqualify all
    if pd.isna(price_today) or price_today <= 1.0:
        out.update(
            {
                "price_90d_ago": np.nan,
                "return_90d": np.nan,
                "qualifies_a": False,
                "qualifies_b": False,
                "qualifies_c": False,
                "reason": "price_today<=1",
            }
        )
        # Set default fields to avoid missing columns
        out.update(
            {
                "peak_5y_price": np.nan,
                "peak_5y_date": "",
                "bottom_post_peak_price": np.nan,
                "bottom_post_peak_date": "",
                "bottom_drawdown_pct": np.nan,
                "hit30_from_bottom_date": "",
                "days_bottom_to_hit30": np.nan,
                "low_52w_price": np.nan,
                "low_52w_date": "",
                "hit30_from_52wlow_date": "",
                "days_52wlow_to_hit30": np.nan,
                "low180_price": np.nan,
                "low180_date": "",
                "spike30_threshold": np.nan,
                "spike30_date": "",
                "spike30_in_last30d": False,
                "has_5y_history": False,
                "has_52w_history": False,
            }
        )
        return out

    # Compute return_90d: price_today vs price ~90 trading days ago
    price_90d_ago = np.nan
    return_90d = np.nan
    if len(prices) >= cfg.window_90d:
        price_90d_ago = float(prices.iloc[-cfg.window_90d]["adj_close"])
        if price_90d_ago > 0:
            return_90d = (price_today / price_90d_ago) - 1
    out["price_90d_ago"] = price_90d_ago
    out["return_90d"] = return_90d

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

                if bottom_price > 0:
                    target = (1.0 + cfg.rebound_pct) * bottom_price
                    hit_date = _first_crossing_date(p5y, bottom_date, target)
                    if hit_date is not None:
                        out["hit30_from_bottom_date"] = _fmt_date(hit_date)
                        out["days_bottom_to_hit30"] = int((hit_date - bottom_date).days)

    # Category A: bottom_drawdown_pct >= drawdown_pct AND return_90d >= rebound_pct
    if not pd.isna(out["bottom_drawdown_pct"]) and not pd.isna(return_90d):
        qualifies_a = (
            out["bottom_drawdown_pct"] >= cfg.drawdown_pct
            and return_90d >= cfg.rebound_pct
        )

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

    # Price guard 2: 52-week low guard - if low_52w_price is missing or <= 1.0, disqualify B and C
    if pd.isna(out["low_52w_price"]) or out["low_52w_price"] <= 1.0:
        qualifies_b = False
        qualifies_c = False

    # Category B: return_90d >= rebound_pct AND has_52w_history == True
    if not pd.isna(return_90d) and not pd.isna(out["low_52w_price"]) and out["low_52w_price"] > 1.0:
        qualifies_b = (
            return_90d >= cfg.rebound_pct
            and out["has_52w_history"] == True
        )

    out["qualifies_b"] = bool(qualifies_b)

    # ---- Category C ----
    # Initialize qualifies_c based on 52-week guard (already set above if guard failed)
    # If 52-week guard passed, we'll compute Category C below
    qualifies_c = False
    spike30_in_last30d = False

    out.update(
        {
            "low180_price": np.nan,
            "low180_date": "",
            "spike30_threshold": np.nan,
            "spike30_date": "",
            "spike30_in_last30d": False,
        }
    )

    # Only compute Category C if 52-week guard passed (low_52w_price > 1.0)
    if not pd.isna(out["low_52w_price"]) and out["low_52w_price"] > 1.0:
        if len(prices) >= cfg.window_180d:
            recent180 = _last_n(prices, cfg.window_180d)
            
            # Compute 52-week low within recent180
            low180_price = float(recent180["adj_close"].min())
            low180_idx = recent180["adj_close"].idxmin()
            low180_date = pd.Timestamp(recent180.loc[low180_idx, "date"])
            out["low180_price"] = low180_price
            out["low180_date"] = _fmt_date(low180_date)

            # Price guard 3: 180-day low guard - if low180_price is missing or <= 1.0, disqualify C
            if not pd.isna(low180_price) and low180_price > 1.0:
                threshold = 1.3 * low180_price
                out["spike30_threshold"] = threshold

                # Check if price touched >= threshold after low180_date
                after_low = recent180[recent180["date"] >= low180_date]
                touched = after_low["adj_close"] >= threshold
                qualifies_c = touched.any()

                if qualifies_c:
                    spike_idx = after_low.loc[touched].index.min()
                    spike_date = pd.Timestamp(after_low.loc[spike_idx, "date"])
                    out["spike30_date"] = _fmt_date(spike_date)

                    # Check if spike occurred in last 30 days
                    recent30 = _last_n(prices, cfg.window_30d)
                    spike30_in_last30d = (recent30["adj_close"] >= threshold).any()
                    out["spike30_in_last30d"] = bool(spike30_in_last30d)

    out["qualifies_c"] = bool(qualifies_c)

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
        ac = ac[ac > 0]  # keep only positive prices
        ratio = ac / ac.shift(1)
        ratio = ratio[ratio > 0]
        rets = np.log(ratio).dropna()
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
    # Load universe CSV and create title mapping
    df_universe = pd.read_csv(universe_csv)
    if "ticker" not in df_universe.columns:
        raise ValueError("Universe CSV must contain a 'ticker' column.")
    title_map = dict(zip(df_universe["ticker"], df_universe.get("title", ""))) if "title" in df_universe.columns else {}
    
    tickers = sorted(df_universe["ticker"].dropna().unique().tolist())
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
                    "ticker_name": title_map.get(t, ""),
                    "price_today": np.nan,
                    "date_today": "",
                    "price_90d_ago": np.nan,
                    "return_90d": np.nan,
                    "qualifies_a": False,
                    "qualifies_b": False,
                    "qualifies_c": False,
                    "reason": "missing_price_file",
                }
            )
            continue

        try:
            prices = load_prices_csv(price_path)
            result = screen_ticker(prices, cfg)
            result["run_id"] = run_id
            result["ticker"] = t
            result["ticker_name"] = title_map.get(t, "")
            rows.append(result)
        except Exception as e:
            rows.append(
                {
                    "run_id": run_id,
                    "ticker": t,
                    "ticker_name": title_map.get(t, ""),
                    "price_today": np.nan,
                    "date_today": "",
                    "price_90d_ago": np.nan,
                    "return_90d": np.nan,
                    "qualifies_a": False,
                    "qualifies_b": False,
                    "qualifies_c": False,
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
        "ticker_name",
        "date_today",
        "price_today",
        "price_90d_ago",
        "return_90d",
        "qualifies_a",
        "qualifies_b",
        "qualifies_c",
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
        # C
        "low180_date",
        "low180_price",
        "spike30_threshold",
        "spike30_date",
        "spike30_in_last30d",
        # extras
        "avg_volume_30d",
        "volatility_90d",
        "has_5y_history",
        "has_52w_history",
        "reason",
    ]
    for c in preferred_cols:
        if c not in df_out.columns:
            if c in ("run_id", "ticker", "ticker_name", "date_today", "reason", "low180_date", "spike30_date"):
                df_out[c] = ""
            elif c in ("qualifies_a", "qualifies_b", "qualifies_c", "spike30_in_last30d"):
                df_out[c] = False
            else:
                df_out[c] = np.nan

    df_out = df_out[preferred_cols].copy()

    # Formatting for human readability
    price_cols = [
        "price_today",
        "price_90d_ago",
        "peak_5y_price",
        "bottom_post_peak_price",
        "low_52w_price",
        "low180_price",
        "spike30_threshold",
    ]

    # Keep prices as numeric floats (rounding applied in export_candidates.py)
    for c in price_cols:
        if c in df_out.columns:
            df_out[c] = pd.to_numeric(df_out[c], errors="coerce")

    # Keep percentage columns as raw decimals (formatting applied in export_candidates.py)
    # No multiplication by 100 here - keep return_90d and bottom_drawdown_pct as decimals (0.30, 0.80)
    for c in ["return_90d", "bottom_drawdown_pct"]:
        if c in df_out.columns:
            df_out[c] = pd.to_numeric(df_out[c], errors="coerce")

    # Ensure numeric types for volume and volatility
    if "avg_volume_30d" in df_out.columns:
        df_out["avg_volume_30d"] = pd.to_numeric(df_out["avg_volume_30d"], errors="coerce")
    if "volatility_90d" in df_out.columns:
        df_out["volatility_90d"] = pd.to_numeric(df_out["volatility_90d"], errors="coerce")

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

