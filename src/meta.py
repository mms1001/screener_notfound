# src/meta.py
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf


META_CACHE_PATH = Path("data/meta/ticker_meta.csv")
CACHE_EXPIRY_DAYS = 30  # Refresh metadata after 30 days


def _ensure_cache_dir() -> None:
    """Ensure the cache directory exists."""
    META_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_cache() -> pd.DataFrame:
    """Load existing cache or return empty DataFrame."""
    _ensure_cache_dir()
    if not META_CACHE_PATH.exists():
        return pd.DataFrame(columns=["ticker", "currency", "asof"])
    
    try:
        df = pd.read_csv(META_CACHE_PATH, parse_dates=["asof"])
        return df
    except Exception:
        # If cache is corrupted, return empty
        return pd.DataFrame(columns=["ticker", "currency", "asof"])


def _save_cache(df: pd.DataFrame) -> None:
    """Save cache to CSV."""
    _ensure_cache_dir()
    df.to_csv(META_CACHE_PATH, index=False)


def _fetch_currency_from_yfinance(ticker: str) -> str | None:
    """
    Fetch currency for a ticker using yfinance.
    
    Args:
        ticker: Stock ticker symbol
        
    Returns:
        Currency code (e.g., "GBP", "USD", "EUR") or None if unavailable
    """
    try:
        ticker_obj = yf.Ticker(ticker)
        
        # Try fast_info first (faster, less data)
        try:
            fast_info = ticker_obj.fast_info
            if hasattr(fast_info, 'currency') and fast_info.currency:
                return str(fast_info.currency).upper()
        except Exception:
            pass
        
        # Fallback to info (slower but more complete)
        try:
            info = ticker_obj.info
            if "currency" in info and info["currency"]:
                return str(info["currency"]).upper()
        except Exception:
            pass
        
        return None
    except Exception:
        return None


def get_ticker_meta(ticker: str, force_refresh: bool = False) -> dict:
    """
    Get ticker metadata (currency, etc.) with caching.
    
    Args:
        ticker: Stock ticker symbol
        force_refresh: If True, bypass cache and fetch fresh data
        
    Returns:
        Dictionary with at least {"currency": "..."} or {"currency": None} if unavailable
    """
    if not ticker or pd.isna(ticker):
        return {"currency": None}
    
    ticker = str(ticker).strip()
    if not ticker:
        return {"currency": None}
    
    # Load cache
    cache_df = _load_cache()
    
    # Check if we have cached data
    if not force_refresh:
        cached = cache_df[cache_df["ticker"] == ticker]
        if not cached.empty:
            # Check if cache is still valid
            latest = cached.sort_values("asof", ascending=False).iloc[0]
            asof_date = pd.to_datetime(latest["asof"])
            days_old = (datetime.now() - asof_date).days
            
            if days_old < CACHE_EXPIRY_DAYS:
                return {"currency": latest["currency"] if pd.notna(latest["currency"]) else None}
    
    # Fetch fresh data
    currency = _fetch_currency_from_yfinance(ticker)
    
    # Rate limiting: small delay to avoid overwhelming API
    time.sleep(0.1)
    
    # Update cache
    now = datetime.now()
    
    # Remove old entry for this ticker
    cache_df = cache_df[cache_df["ticker"] != ticker]
    
    # Add new entry
    new_row = pd.DataFrame({
        "ticker": [ticker],
        "currency": [currency],
        "asof": [now],
    })
    cache_df = pd.concat([cache_df, new_row], ignore_index=True)
    
    # Save cache
    _save_cache(cache_df)
    
    return {"currency": currency}


def get_ticker_meta_batch(tickers: list[str], force_refresh: bool = False) -> dict[str, dict]:
    """
    Get metadata for multiple tickers efficiently (uses cache when possible).
    
    Args:
        tickers: List of ticker symbols
        force_refresh: If True, bypass cache for all tickers
        
    Returns:
        Dictionary mapping ticker -> metadata dict
    """
    result = {}
    
    # Load cache once
    cache_df = _load_cache()
    now = datetime.now()
    
    # Separate tickers into cached (valid) and need-fetch
    cached_results = {}
    need_fetch = []
    
    if not force_refresh:
        for ticker in tickers:
            if not ticker or pd.isna(ticker):
                result[ticker] = {"currency": None}
                continue
            
            ticker = str(ticker).strip()
            cached = cache_df[cache_df["ticker"] == ticker]
            
            if not cached.empty:
                latest = cached.sort_values("asof", ascending=False).iloc[0]
                asof_date = pd.to_datetime(latest["asof"])
                days_old = (now - asof_date).days
                
                if days_old < CACHE_EXPIRY_DAYS:
                    cached_results[ticker] = {"currency": latest["currency"] if pd.notna(latest["currency"]) else None}
                    continue
            
            need_fetch.append(ticker)
    else:
        need_fetch = [str(t).strip() for t in tickers if t and not pd.isna(t)]
    
    # Fetch fresh data for tickers that need it
    new_entries = []
    for ticker in need_fetch:
        currency = _fetch_currency_from_yfinance(ticker)
        result[ticker] = {"currency": currency}
        
        new_entries.append({
            "ticker": ticker,
            "currency": currency,
            "asof": now,
        })
        
        # Rate limiting
        time.sleep(0.1)
    
    # Update cache with new entries
    if new_entries:
        new_df = pd.DataFrame(new_entries)
        # Remove old entries for these tickers
        cache_df = cache_df[~cache_df["ticker"].isin(need_fetch)]
        # Add new entries
        cache_df = pd.concat([cache_df, new_df], ignore_index=True)
        _save_cache(cache_df)
    
    # Add cached results
    result.update(cached_results)
    
    return result

