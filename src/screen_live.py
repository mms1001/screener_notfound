from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


def load_universe(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "ticker" not in df.columns:
        raise ValueError("Universe CSV must have a 'ticker' column.")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df[df["ticker"].str.len() > 0].drop_duplicates(subset=["ticker"])
    return df


def _download_prices(ticker: str, years_back: int) -> pd.DataFrame:
    # Yahoo suporta period="5y", então vamos usar exatamente 5 anos (ou years_back*365 via start).
    # Para evitar inconsistências, usamos start.
    start = (datetime.today() - timedelta(days=365 * years_back)).strftime("%Y-%m-%d")

    df = yf.download(
        ticker,
        start=start,
        progress=False,
        auto_adjust=False,
        actions=False,
        group_by="ticker",
        threads=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # Caso venha MultiIndex nas colunas:
    # - group_by="ticker" normalmente devolve colunas MultiIndex: (Ticker, PriceField)
    # - group_by="column" pode devolver (PriceField, Ticker)
    if isinstance(df.columns, pd.MultiIndex):
        # tenta detectar qual nível contém o ticker
        levels0 = set(df.columns.get_level_values(0))
        levels1 = set(df.columns.get_level_values(1))

        if ticker in levels0:
            # formato: (TICKER, field)
            df_t = df[ticker].copy()
        elif ticker in levels1:
            # formato: (field, TICKER)
            df_t = df.xs(ticker, level=1, axis=1).copy()
        else:
            # se não achar, pega a primeira fatia possível
            df_t = df.copy()
            # e achata tentando manter nomes simples
            df_t.columns = [c[0] if isinstance(c, tuple) else c for c in df_t.columns]
    else:
        df_t = df.copy()

    # Agora df_t deve ter colunas simples tipo 'Adj Close'/'Close' etc.
    df_t = df_t.reset_index()

    # Data
    if "Date" in df_t.columns:
        df_t = df_t.rename(columns={"Date": "date"})
    elif "index" in df_t.columns:
        df_t = df_t.rename(columns={"index": "date"})
    else:
        df_t = df_t.rename(columns={df_t.columns[0]: "date"})

    df_t["date"] = pd.to_datetime(df_t["date"], errors="coerce")

    # Preço
    if "Adj Close" in df_t.columns:
        df_t = df_t.rename(columns={"Adj Close": "adj_close"})
    elif "Close" in df_t.columns:
        df_t = df_t.rename(columns={"Close": "adj_close"})
    else:
        # tenta achar algo que pareça close
        close_cols = [c for c in df_t.columns if isinstance(c, str) and "close" in c.lower()]
        if close_cols:
            df_t = df_t.rename(columns={close_cols[0]: "adj_close"})
        else:
            return pd.DataFrame()

    df_t = df_t.dropna(subset=["date", "adj_close"]).sort_values("date").reset_index(drop=True)
    if df_t.empty:
        return pd.DataFrame()

    return df_t[["date", "adj_close"]]



def _first_crossing_date(df: pd.DataFrame, start_date: pd.Timestamp, target: float) -> Optional[pd.Timestamp]:
    after = df[df["date"] > start_date]
    if after.empty:
        return None
    hit = after[after["adj_close"] >= target]
    if hit.empty:
        return None
    return pd.Timestamp(hit.iloc[0]["date"])


def compute_metrics(prices: pd.DataFrame, rebound_pct: float, drawdown_pct: float, window_90d: int) -> dict:
    # defaults
    out = {
        "price_today": np.nan,
        "date_today": "",
        "return_90d": np.nan,
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
        "qualifies_a": False,
        "qualifies_b": False,
        "reason": "ok",
    }

    if prices.empty or len(prices) < 60:
        out["reason"] = "insufficient_data"
        return out

    # today
    last = prices.iloc[-1]
    out["price_today"] = float(last["adj_close"])
    out["date_today"] = pd.Timestamp(last["date"]).date().isoformat()

    # --- return over last 90 trading days ---
    if len(prices) >= window_90d:
        price_90d_ago = prices.iloc[-window_90d]["adj_close"]
        if price_90d_ago > 0:
            out["return_90d"] = (out["price_today"] / price_90d_ago) - 1.0
        else:
            out["return_90d"] = np.nan
    else:
        out["return_90d"] = np.nan

    # recent 90d start (for the "hit within last 90d" check)
    recent90_start = prices.iloc[-min(len(prices), window_90d)]["date"]

    # 5y peak + bottom after peak
    peak_idx = prices["adj_close"].idxmax()
    peak_row = prices.loc[peak_idx]
    peak_price = float(peak_row["adj_close"])
    peak_date = pd.Timestamp(peak_row["date"])
    out["peak_5y_price"] = peak_price
    out["peak_5y_date"] = peak_date.date().isoformat()

    post_peak = prices[prices["date"] >= peak_date].copy()
    if len(post_peak) >= 5:
        bottom_idx = post_peak["adj_close"].idxmin()
        bottom_row = post_peak.loc[bottom_idx]
        bottom_price = float(bottom_row["adj_close"])
        bottom_date = pd.Timestamp(bottom_row["date"])
        out["bottom_post_peak_price"] = bottom_price
        out["bottom_post_peak_date"] = bottom_date.date().isoformat()

        if peak_price > 0 and bottom_price > 0:
            drawdown = 1.0 - (bottom_price / peak_price)
            out["bottom_drawdown_pct"] = float(drawdown)

            if drawdown >= drawdown_pct:
                target = (1.0 + rebound_pct) * bottom_price
                hit_date = _first_crossing_date(prices, bottom_date, target)
                if hit_date is not None:
                    out["hit30_from_bottom_date"] = hit_date.date().isoformat()
                    out["days_bottom_to_hit30"] = int((hit_date - bottom_date).days)
                    out["qualifies_a"] = bool(hit_date >= recent90_start)

    # 52w low + +30%
    w52 = prices.tail(252)
    if len(w52) >= 50:
        low_idx = w52["adj_close"].idxmin()
        low_row = w52.loc[low_idx]
        low_price = float(low_row["adj_close"])
        low_date = pd.Timestamp(low_row["date"])
        out["low_52w_price"] = low_price
        out["low_52w_date"] = low_date.date().isoformat()

        if low_price > 0:
            target = (1.0 + rebound_pct) * low_price
            hit_date = _first_crossing_date(w52, low_date, target)
            if hit_date is not None:
                out["hit30_from_52wlow_date"] = hit_date.date().isoformat()
                out["days_52wlow_to_hit30"] = int((hit_date - low_date).days)
                out["qualifies_b"] = bool(hit_date >= recent90_start)

    return out


def finalize_and_write_outputs(out_df: pd.DataFrame, output_path: str):
    import numpy as np
    from pathlib import Path

    out_df = out_df.copy()

    # -----------------------------
    # HARD RULES (permanent)
    # -----------------------------
    MIN_PRICE = 10.0
    DRAW_A = 0.80
    RET_90D = 0.30
    BAD_SUFFIXES = (
        "-UN", "-WT", "-W", "-R", "-P",
        ".A", ".B", ".C", ".D",
    )

    # safety casting
    out_df["price_today"] = pd.to_numeric(out_df["price_today"], errors="coerce")
    out_df["bottom_drawdown_pct"] = pd.to_numeric(out_df["bottom_drawdown_pct"], errors="coerce")
    out_df["return_90d"] = pd.to_numeric(out_df.get("return_90d"), errors="coerce")

    # clean universe
    out_df = out_df[out_df["price_today"] >= MIN_PRICE]
    out_df = out_df[~out_df["ticker"].str.contains("|".join(BAD_SUFFIXES), regex=True)]

    # -----------------------------
    # Category A (FINAL RULE)
    # -----------------------------
    out_df["Category A"] = (
        (out_df["bottom_drawdown_pct"] >= DRAW_A) &
        (out_df["return_90d"] >= RET_90D)
    )

    # Category B = already encoded by your logic
    out_df["Category B"] = out_df["qualifies_b"].fillna(False)

    # -----------------------------
    # Rename columns (presentation layer)
    # -----------------------------
    rename_map = {
        "ticker": "Ticket",
        "market_cap": "Market Cap",
        "price_today": "Price Today",
        "date_today": "Today's Price Date",
        "peak_5y_price": "Price Peak (5Y)",
        "peak_5y_date": "Date Peak (5Y)",
        "bottom_post_peak_price": "Bottom Price After Peak (5Y)",
        "bottom_post_peak_date": "Bottom Date After Peak (5Y)",
        "bottom_drawdown_pct": "Drawdown From Peak (5Y) %",
        "low_52w_price": "52W Low Price",
        "low_52w_date": "52W Low Date",
        "return_90d": "Return (90D) %",
    }

    out_df.rename(columns=rename_map, inplace=True)

    # percentages → human readable
    for c in ["Drawdown From Peak (5Y) %", "Return (90D) %"]:
        if c in out_df.columns:
            out_df[c] = (out_df[c] * 100).round(1)

    # -----------------------------
    # Drop internal/debug columns
    # -----------------------------
    drop_cols = [
        "run_id", "reason",
        "qualifies_a", "qualifies_b",
        "hit30_from_bottom_date", "days_bottom_to_hit30",
        "hit30_from_52wlow_date", "days_52wlow_to_hit30",
    ]
    out_df.drop(columns=[c for c in drop_cols if c in out_df.columns], inplace=True)

    # -----------------------------
    # Write outputs
    # -----------------------------
    out_path = Path(output_path)
    base = out_path.stem
    parent = out_path.parent

    parent.mkdir(parents=True, exist_ok=True)

    # master (already cleaned)
    master_path = parent / f"{base}_clean.csv"
    out_df.to_csv(master_path, index=False)

    # Candidate A column order
    candidate_a_cols = [
        "Ticket",
        "Price Today",
        "Today's Price Date",
        "Price Peak (5Y)",
        "Date Peak (5Y)",
        "Bottom Price After Peak (5Y)",
        "Bottom Date After Peak (5Y)",
        "Drawdown From Peak (5Y) %",
        "52W Low Price",
        "52W Low Date",
        "Return (90D) %",
        "Market Cap",
    ]

    # Candidate B column order
    candidate_b_cols = [
        "Ticket",
        "Price Today",
        "Today's Price Date",
        "52W Low Price",
        "52W Low Date",
        "Return (90D) %",
        "Price Peak (5Y)",
        "Date Peak (5Y)",
        "Bottom Price After Peak (5Y)",
        "Bottom Date After Peak (5Y)",
        "Drawdown From Peak (5Y) %",
        "Market Cap",
    ]

    # Filter candidates and reorder columns
    candidates_a = out_df[out_df["Category A"]].copy()
    candidates_b = out_df[out_df["Category B"]].copy()

    # Drop Category A and Category B columns from candidate files
    candidates_a = candidates_a.drop(columns=["Category A", "Category B"], errors="ignore")
    candidates_b = candidates_b.drop(columns=["Category A", "Category B"], errors="ignore")

    # Reorder columns (only include columns that exist)
    candidates_a_cols_filtered = [c for c in candidate_a_cols if c in candidates_a.columns]
    candidates_b_cols_filtered = [c for c in candidate_b_cols if c in candidates_b.columns]

    # Add any remaining columns that weren't in the specified order
    remaining_a = [c for c in candidates_a.columns if c not in candidates_a_cols_filtered]
    remaining_b = [c for c in candidates_b.columns if c not in candidates_b_cols_filtered]

    candidates_a = candidates_a[candidates_a_cols_filtered + remaining_a]
    candidates_b = candidates_b[candidates_b_cols_filtered + remaining_b]

    candidates_a.to_csv(parent / "candidates_A.csv", index=False)
    candidates_b.to_csv(parent / "candidates_B.csv", index=False)

    print("✅ Clean master:", master_path)
    print("✅ Candidates A:", (out_df["Category A"].sum()))
    print("✅ Candidates B:", (out_df["Category B"].sum()))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Screen tickers live (no per-ticker CSV), output one consolidated CSV.")
    ap.add_argument("--universe", required=True, help="CSV with tickers (and optionally market_cap column).")
    ap.add_argument("--output", required=True, help="Output consolidated CSV.")
    ap.add_argument("--years-back", type=int, default=5)
    ap.add_argument("--rebound-pct", type=float, default=0.30)
    ap.add_argument("--drawdown-pct", type=float, default=0.80)
    ap.add_argument("--window-90d", type=int, default=53)
    ap.add_argument("--sleep", type=float, default=0.05, help="Sleep between requests to reduce rate limits.")
    ap.add_argument("--limit", type=int, default=None, help="Optional limit for testing.")
    return ap.parse_args()


def main():
    args = parse_args()
    uni = load_universe(Path(args.universe))
    if args.limit:
        uni = uni.head(args.limit).copy()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = []
    total = len(uni)

    for i, row in enumerate(uni.itertuples(index=False), 1):
        ticker = getattr(row, "ticker")
        market_cap = row.market_cap if "market_cap" in row._fields else np.nan

        try:
            prices = _download_prices(ticker, args.years_back)
            m = compute_metrics(prices, args.rebound_pct, args.drawdown_pct, args.window_90d)
        except Exception as e:
            print(f"[ERROR] {ticker}: {type(e).__name__}: {e}")
            m = {"reason": f"error:{type(e).__name__}:{e}"}
            # fill defaults
            m = {**compute_metrics(pd.DataFrame(), args.rebound_pct, args.drawdown_pct, args.window_90d), **m}

        m["run_id"] = run_id
        m["ticker"] = ticker
        m["market_cap"] = market_cap
        rows.append(m)

        if args.sleep:
            time.sleep(args.sleep)

        if i % 200 == 0:
            print(f"  {i:,}/{total:,} screened...")

    out_df = pd.DataFrame(rows)
    finalize_and_write_outputs(out_df, args.output)


if __name__ == "__main__":
    main()
