# src/fx.py
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf


FX_CACHE_PATH = Path("data/meta/fx_cache.csv")
CACHE_EXPIRY_DAYS = 1  # Refresh FX rates daily (they change frequently)

# Supported currency pairs: local -> USD
SUPPORTED_CURRENCIES = {
    "GBP": "GBPUSD=X",
    "EUR": "EURUSD=X",
    "CHF": "CHFUSD=X",
    "SEK": "SEKUSD=X",
    "BRL": "BRLUSD=X",
    # USD to USD is 1.0
    "USD": None,  # Special case, no conversion needed
}


def _ensure_cache_dir() -> None:
    """Ensure the cache directory exists."""
    FX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_cache() -> pd.DataFrame:
    """Load existing cache or return empty DataFrame."""
    _ensure_cache_dir()
    if not FX_CACHE_PATH.exists():
        return pd.DataFrame(columns=["currency", "rate", "asof"])
    
    try:
        df = pd.read_csv(FX_CACHE_PATH, parse_dates=["asof"])
        return df
    except Exception:
        # If cache is corrupted, return empty
        return pd.DataFrame(columns=["currency", "rate", "asof"])


def _save_cache(df: pd.DataFrame) -> None:
    """Save cache to CSV."""
    _ensure_cache_dir()
    df.to_csv(FX_CACHE_PATH, index=False)


def _fetch_fx_rate_from_yfinance(fx_ticker: str) -> float | None:
    """
    Fetch FX rate from yfinance.
    
    Args:
        fx_ticker: Yahoo Finance FX ticker (e.g., "GBPUSD=X")
        
    Returns:
        Exchange rate (e.g., 1.25 for GBP->USD) or None if unavailable
    """
    try:
        ticker_obj = yf.Ticker(fx_ticker)
        
        # Try fast_info first
        try:
            fast_info = ticker_obj.fast_info
            if hasattr(fast_info, 'lastPrice') and fast_info.lastPrice:
                return float(fast_info.lastPrice)
        except Exception:
            pass
        
        # Fallback to recent price data
        try:
            hist = ticker_obj.history(period="1d")
            if not hist.empty and "Close" in hist.columns:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        
        # Fallback to info
        try:
            info = ticker_obj.info
            if "regularMarketPrice" in info and info["regularMarketPrice"]:
                return float(info["regularMarketPrice"])
        except Exception:
            pass
        
        return None
    except Exception:
        return None


def get_fx_rate(currency: str, force_refresh: bool = False) -> float | None:
    """
    Get FX rate for converting currency to USD.
    
    Args:
        currency: Currency code (e.g., "GBP", "EUR", "USD")
        force_refresh: If True, bypass cache and fetch fresh data
        
    Returns:
        Exchange rate (e.g., 1.25 for GBP->USD), 1.0 for USD, or None if unavailable
    """
    if not currency:
        return None
    
    currency = str(currency).upper().strip()
    
    # USD to USD is always 1.0
    if currency == "USD":
        return 1.0
    
    # Check if currency is supported
    if currency not in SUPPORTED_CURRENCIES:
        return None
    
    fx_ticker = SUPPORTED_CURRENCIES[currency]
    if fx_ticker is None:
        return 1.0  # Already handled USD case above
    
    # Load cache
    cache_df = _load_cache()
    
    # Check if we have cached data
    if not force_refresh:
        cached = cache_df[cache_df["currency"] == currency]
        if not cached.empty:
            # Check if cache is still valid
            latest = cached.sort_values("asof", ascending=False).iloc[0]
            asof_date = pd.to_datetime(latest["asof"])
            days_old = (datetime.now() - asof_date).days
            
            if days_old < CACHE_EXPIRY_DAYS:
                rate = latest["rate"]
                if pd.notna(rate):
                    return float(rate)
    
    # Fetch fresh data
    rate = _fetch_fx_rate_from_yfinance(fx_ticker)
    
    # Rate limiting
    time.sleep(0.1)
    
    # Update cache
    now = datetime.now()
    
    # Remove old entry for this currency
    cache_df = cache_df[cache_df["currency"] != currency]
    
    # Add new entry
    new_row = pd.DataFrame({
        "currency": [currency],
        "rate": [rate],
        "asof": [now],
    })
    cache_df = pd.concat([cache_df, new_row], ignore_index=True)
    
    # Save cache
    _save_cache(cache_df)
    
    return rate


def convert_to_usd(amount: float | None, currency: str | None) -> float | None:
    """
    Convert an amount from local currency to USD.
    
    Args:
        amount: Amount in local currency (can be None/NaN)
        currency: Currency code (e.g., "GBP", "EUR", "GBp")
        
    Returns:
        Amount in USD, or None if conversion unavailable
    """
    if amount is None or pd.isna(amount):
        return None
    
    if currency is None or pd.isna(currency):
        return None
    
    currency = str(currency).strip()
    currency_upper = currency.upper()
    
    # Handle GBp (UK pence): scale by 100 first, then treat as GBP
    # Note: "GBp" is pence (needs scaling), "GBP" is pounds (already scaled in fetch_prices.py)
    # Check if currency is "GBp" (case-insensitive: "GBp", "gbp", "GBP" with lowercase 'p')
    if len(currency) == 3 and currency[:2].upper() == "GB" and currency[2].lower() == 'p':
        # Scale pence to pounds: divide by 100
        amount = float(amount) / 100.0
        currency_upper = "GBP"  # Use GBP for FX lookup
    
    # Get FX rate (now using GBP for GBp)
    rate = get_fx_rate(currency_upper)
    if rate is None:
        return None
    
    try:
        return float(amount) * float(rate)
    except (ValueError, TypeError):
        return None


def get_fx_rate_batch(currencies: list[str], force_refresh: bool = False) -> dict[str, float | None]:
    """
    Get FX rates for multiple currencies efficiently (uses cache when possible).
    
    Args:
        currencies: List of currency codes
        force_refresh: If True, bypass cache for all currencies
        
    Returns:
        Dictionary mapping currency -> rate (or None if unavailable)
    """
    result = {}
    
    # Load cache once
    cache_df = _load_cache()
    now = datetime.now()
    
    # Separate currencies into cached (valid) and need-fetch
    cached_results = {}
    need_fetch = []
    
    if not force_refresh:
        for currency in currencies:
            if not currency or pd.isna(currency):
                result[currency] = None
                continue
            
            currency = str(currency).upper().strip()
            
            # USD is always 1.0
            if currency == "USD":
                result[currency] = 1.0
                continue
            
            # Check if supported
            if currency not in SUPPORTED_CURRENCIES:
                result[currency] = None
                continue
            
            cached = cache_df[cache_df["currency"] == currency]
            
            if not cached.empty:
                latest = cached.sort_values("asof", ascending=False).iloc[0]
                asof_date = pd.to_datetime(latest["asof"])
                days_old = (now - asof_date).days
                
                if days_old < CACHE_EXPIRY_DAYS:
                    rate = latest["rate"]
                    cached_results[currency] = float(rate) if pd.notna(rate) else None
                    continue
            
            need_fetch.append(currency)
    else:
        need_fetch = [str(c).upper().strip() for c in currencies if c and not pd.isna(c) and str(c).upper().strip() != "USD"]
    
    # Fetch fresh data for currencies that need it
    new_entries = []
    for currency in need_fetch:
        if currency not in SUPPORTED_CURRENCIES:
            result[currency] = None
            continue
        
        fx_ticker = SUPPORTED_CURRENCIES[currency]
        if fx_ticker is None:
            result[currency] = 1.0
            continue
        
        rate = _fetch_fx_rate_from_yfinance(fx_ticker)
        result[currency] = rate
        
        new_entries.append({
            "currency": currency,
            "rate": rate,
            "asof": now,
        })
        
        # Rate limiting
        time.sleep(0.1)
    
    # Update cache with new entries
    if new_entries:
        new_df = pd.DataFrame(new_entries)
        # Remove old entries for these currencies
        cache_df = cache_df[~cache_df["currency"].isin(need_fetch)]
        # Add new entries
        cache_df = pd.concat([cache_df, new_df], ignore_index=True)
        _save_cache(cache_df)
    
    # Add cached results
    result.update(cached_results)
    
    return result

