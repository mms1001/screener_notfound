# src/export_candidates.py
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Import currency modules - handle both relative and absolute imports
try:
    from .fx import convert_to_usd
    from .meta import get_ticker_meta_batch
except ImportError:
    # Fallback for when run as script
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.fx import convert_to_usd
    from src.meta import get_ticker_meta_batch


# Column renaming mapping
COLUMN_RENAME = {
    "ticker": "Ticket",
    "ticker_name": "Ticket Name",
    "date_today": "Today's Price Date",
    "price_today": "Price Today",
    "low_52w_price": "52W Low Price",
    "low_52w_date": "52W Low Date",
    "price_90d_ago": "Price (90D Ago)",
    "return_90d": "Return (90D) %",
    "peak_5y_price": "Price Peak (5Y)",
    "peak_5y_date": "Date Peak (5Y)",
    "bottom_post_peak_price": "Bottom Price After Peak (5Y)",
    "bottom_post_peak_date": "Bottom Date After Peak (5Y)",
    "bottom_drawdown_pct": "Drawdown From Peak (5Y) %",
    "hit30_from_bottom_date": "Hit +30% From Bottom Date",
    "days_bottom_to_hit30": "Days Bottom → Hit30",
    "hit30_from_52wlow_date": "Hit +30% From 52W Low Date",
    "days_52wlow_to_hit30": "Days 52W Low → Hit30",
    "avg_volume_30d": "Avg Volume (30D)",
    "volatility_90d": "Volatility (90D)",
    "qualifies_a": "Qualifies A",
    "qualifies_b": "Qualifies B",
    "qualifies_c": "Qualifies C",
    "low180_price": "180D Low Price",
    "low180_date": "180D Low Date",
    "spike30_threshold": "Spike +30% Threshold",
    "spike30_date": "Spike +30% Date",
    "spike30_in_last30d": "Spike +30% In Last 30D",
}

# Columns to drop
COLUMNS_TO_DROP = [
    "run_id",
    "has_5y_history",
    "has_52w_history",
    "reason",
]

# Desired column order (only columns that exist will be kept)
COLUMN_ORDER = [
    "Ticket",
    "Ticket Name",
    "Company Description",
    "Currency",
    "Price Today",
    "Price Today (USD)",
    "Today's Price Date",
    "52W Low Price",
    "52W Low Date",
    "Return (90D) %",
    "Price Peak (5Y)",
    "Date Peak (5Y)",
    "Bottom Price After Peak (5Y)",
    "Bottom Date After Peak (5Y)",
    "Drawdown From Peak (5Y) %",
    "Avg Volume (30D)",
    "Volatility (90D)",
    "Hit +30% From Bottom Date",
    "Days Bottom → Hit30",
    "Hit +30% From 52W Low Date",
    "Days 52W Low → Hit30",
    "180D Low Price",
    "180D Low Date",
    "Spike +30% Date",
    "Spike +30% In Last 30D",
]

# Percentage columns that need conversion (multiply by 100)
PERCENTAGE_COLUMNS = [
    "Return (90D) %",
    "Drawdown From Peak (5Y) %",
]


# Cache for ticker names to avoid repeated API calls
_ticker_name_cache: dict[str, str] = {}

_fundamentals_cache: dict[str, dict] = {}

# Cache for Cash Flow Yield at low date computations
_cfy_at_low_date_cache: dict[str, float | None] = {}

# Cache for EV at low date computations
_ev_at_low_date_cache: dict[str, float | None] = {}

# Cache for P/E at low date computations
_pe_at_low_date_cache: dict[str, float | None] = {}

# Cache for Debt Years computations
_debt_years_cache: dict[str, float | None] = {}

# Cache for price at date (shared by CFY and EV computations)
_price_at_date_cache: dict[str, float | None] = {}

# Debug counters for Cash Flow Yield computation
_cfy_debug_counters = {
    "missing_ocf": 0,
    "missing_capex": 0,
    "missing_shares": 0,
    "missing_price": 0,
}


def get_ticker_name(ticker: str) -> str:
    """
    Fetch ticker name using yfinance with caching.
    
    Args:
        ticker: Stock ticker symbol
        
    Returns:
        Ticker name: prefer info['shortName'], then info['longName'], else fallback to ticker
    """
    # Check cache first
    if ticker in _ticker_name_cache:
        return _ticker_name_cache[ticker]
    
    try:
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        
        # Prefer shortName, then longName, else fallback to ticker
        name = None
        if "shortName" in info and info["shortName"]:
            name = info["shortName"]
        elif "longName" in info and info["longName"]:
            name = info["longName"]
        else:
            name = ticker
        
        # Cache the result
        _ticker_name_cache[ticker] = name
        return name
        
    except Exception:
        # On error, fallback to ticker and cache it
        _ticker_name_cache[ticker] = ticker
        return ticker


def fill_missing_ticker_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill missing Ticket Name values by fetching from yfinance.
    Only fetches for tickers with missing names and uses caching.
    
    Args:
        df: DataFrame with "Ticket" and "Ticket Name" columns
        
    Returns:
        DataFrame with missing "Ticket Name" values filled
    """
    if "Ticket" not in df.columns or "Ticket Name" not in df.columns:
        return df
    
    df = df.copy()
    
    # Find rows with missing Ticket Name
    missing_mask = df["Ticket Name"].isna() | (df["Ticket Name"].astype(str).str.strip() == "")
    
    if not missing_mask.any():
        return df
    
    # Get unique tickers that need names (only those with missing names)
    tickers_to_fetch = df.loc[missing_mask, "Ticket"].dropna().unique()
    
    # Fetch names for missing tickers (with caching)
    print(f"📝 Fetching {len(tickers_to_fetch)} missing ticker names...")
    for ticker in tickers_to_fetch:
        if pd.isna(ticker) or ticker == "":
            continue
        ticker_str = str(ticker)
        # This will use cache if already fetched
        name = get_ticker_name(ticker_str)
        # Small delay to avoid rate limiting
        time.sleep(0.1)
    
    # Fill missing values
    df.loc[missing_mask, "Ticket Name"] = df.loc[missing_mask, "Ticket"].apply(
        lambda t: get_ticker_name(str(t)) if t and not pd.isna(t) else t
    )
    
    return df


def get_yahoo_fundamentals(ticker: str) -> dict:
    """
    Fetch fundamental data (net_income_ltm, market_cap_usd, cash_flow_yield, net_debt_raw) using yfinance with caching.
    
    Args:
        ticker: Stock ticker symbol
        
    Returns:
        Dictionary with keys: "net_income_ltm" (float or None), "market_cap_usd" (float or None), "cash_flow_yield" (float or None), "net_debt_raw" (float or None)
    """
    # Check cache first
    if ticker in _fundamentals_cache:
        return _fundamentals_cache[ticker]
    
    result = {"net_income_ltm": None, "market_cap_usd": None, "cash_flow_yield": None, "net_debt_raw": None}
    
    try:
        ticker_obj = yf.Ticker(ticker)
        
        # Fetch net_income_ltm
        # Priority 1: Sum of last 4 quarters from financials
        try:
            financials = ticker_obj.financials
            if financials is not None and not financials.empty:
                # Look for "Net Income" row in financials
                if "Net Income" in financials.index:
                    net_income_row = financials.loc["Net Income"]
                    # Sum the last 4 quarters (columns are typically dates, most recent first)
                    # Take first 4 columns (most recent 4 quarters)
                    if len(net_income_row) >= 4:
                        net_income_ltm = net_income_row.iloc[:4].sum()
                        if pd.notna(net_income_ltm):
                            result["net_income_ltm"] = float(net_income_ltm)
        except Exception:
            pass
        
        # Priority 2: netIncomeToCommon from info
        if result["net_income_ltm"] is None:
            try:
                info = ticker_obj.info
                if "netIncomeToCommon" in info and info["netIncomeToCommon"] is not None:
                    result["net_income_ltm"] = float(info["netIncomeToCommon"])
            except Exception:
                pass
        
        # Fetch market_cap (raw, will convert to USD later)
        market_cap_raw = None
        # Priority 1: fast_info["marketCap"]
        try:
            fast_info = ticker_obj.fast_info
            if hasattr(fast_info, 'marketCap') and fast_info.marketCap:
                market_cap_raw = float(fast_info.marketCap)
        except Exception:
            pass
        
        # Priority 2: info["marketCap"]
        if market_cap_raw is None:
            try:
                info = ticker_obj.info
                if "marketCap" in info and info["marketCap"] is not None:
                    market_cap_raw = float(info["marketCap"])
            except Exception:
                pass
        
        # Convert market_cap to USD if we have it
        if market_cap_raw is not None:
            # Get currency for this ticker
            ticker_str = str(ticker).strip()
            meta = get_ticker_meta_batch([ticker_str])
            currency = meta.get(ticker_str, {}).get("currency") if ticker_str in meta else None
            # Convert to USD
            market_cap_usd = convert_to_usd(market_cap_raw, currency)
            result["market_cap_usd"] = market_cap_usd
        
        # Fetch Cash Flow Yield components
        operating_cf_ltm = None
        capex_ltm = None
        enterprise_value = None
        
        # Fetch Operating Cash Flow (TTM from quarterly_cashflow)
        try:
            quarterly_cashflow = ticker_obj.quarterly_cashflow
            if quarterly_cashflow is not None and not quarterly_cashflow.empty:
                # Look for "Operating Cash Flow" or "Total Cash From Operating Activities"
                operating_cf_row = None
                for row_name in ["Operating Cash Flow", "Total Cash From Operating Activities", "Cash From Operating Activities"]:
                    if row_name in quarterly_cashflow.index:
                        operating_cf_row = quarterly_cashflow.loc[row_name]
                        break
                
                if operating_cf_row is not None and len(operating_cf_row) >= 4:
                    operating_cf_ltm = operating_cf_row.iloc[:4].sum()
                    if pd.notna(operating_cf_ltm):
                        operating_cf_ltm = float(operating_cf_ltm)
                    else:
                        operating_cf_ltm = None
        except Exception:
            pass
        
        # Fallback: operatingCashflow from info
        if operating_cf_ltm is None:
            try:
                info = ticker_obj.info
                if "operatingCashflow" in info and info["operatingCashflow"] is not None:
                    operating_cf_ltm = float(info["operatingCashflow"])
            except Exception:
                pass
        
        # Fetch Capital Expenditures (TTM from quarterly_cashflow)
        try:
            quarterly_cashflow = ticker_obj.quarterly_cashflow
            if quarterly_cashflow is not None and not quarterly_cashflow.empty:
                # Look for "Capital Expenditures" (typically negative in cashflow statements)
                capex_row = None
                for row_name in ["Capital Expenditures", "Capital Expenditure", "CapitalExpenditure"]:
                    if row_name in quarterly_cashflow.index:
                        capex_row = quarterly_cashflow.loc[row_name]
                        break
                
                if capex_row is not None and len(capex_row) >= 4:
                    capex_ltm = capex_row.iloc[:4].sum()
                    if pd.notna(capex_ltm):
                        # CapEx is typically negative, so we subtract it (which means adding the negative value)
                        capex_ltm = float(capex_ltm)
                    else:
                        capex_ltm = None
        except Exception:
            pass
        
        # Fallback: capitalExpenditures from info
        if capex_ltm is None:
            try:
                info = ticker_obj.info
                if "capitalExpenditures" in info and info["capitalExpenditures"] is not None:
                    capex_ltm = float(info["capitalExpenditures"])
            except Exception:
                pass
        
        # Fetch Enterprise Value
        # Priority 1: fast_info["enterpriseValue"]
        try:
            fast_info = ticker_obj.fast_info
            if hasattr(fast_info, 'enterpriseValue') and fast_info.enterpriseValue:
                enterprise_value = float(fast_info.enterpriseValue)
        except Exception:
            pass
        
        # Priority 2: info["enterpriseValue"]
        if enterprise_value is None:
            try:
                info = ticker_obj.info
                if "enterpriseValue" in info and info["enterpriseValue"] is not None:
                    enterprise_value = float(info["enterpriseValue"])
            except Exception:
                pass
        
        # Calculate Cash Flow Yield: (Operating Cash Flow – Capital Expenditures) / Enterprise Value
        if operating_cf_ltm is not None and capex_ltm is not None and enterprise_value is not None and enterprise_value > 0:
            cash_flow_yield = (operating_cf_ltm - capex_ltm) / enterprise_value
            result["cash_flow_yield"] = float(cash_flow_yield)
        else:
            result["cash_flow_yield"] = None
        
        # Fetch Net Debt (raw, will convert to USD later)
        net_debt_raw = None
        # Priority 1: netDebt from info
        try:
            info = ticker_obj.info
            if "netDebt" in info and info["netDebt"] is not None:
                net_debt_raw = float(info["netDebt"])
        except Exception:
            pass
        
        # Priority 2: Compute from Total Debt - Cash
        if net_debt_raw is None:
            total_debt = None
            cash = None
            
            # Fetch Total Debt
            # Priority 2a: totalDebt from info
            try:
                info = ticker_obj.info
                if "totalDebt" in info and info["totalDebt"] is not None:
                    total_debt = float(info["totalDebt"])
            except Exception:
                pass
            
            # Priority 2b: From balance sheet
            if total_debt is None:
                try:
                    balance_sheet = ticker_obj.balance_sheet
                    if balance_sheet is not None and not balance_sheet.empty:
                        # Try "Total Debt" first
                        if "Total Debt" in balance_sheet.index:
                            total_debt_row = balance_sheet.loc["Total Debt"]
                            if len(total_debt_row) > 0:
                                total_debt = float(total_debt_row.iloc[0])
                        # Otherwise try "Long Term Debt" + "Short Long Term Debt"
                        elif "Long Term Debt" in balance_sheet.index and "Short Long Term Debt" in balance_sheet.index:
                            long_term = balance_sheet.loc["Long Term Debt"]
                            short_long_term = balance_sheet.loc["Short Long Term Debt"]
                            if len(long_term) > 0 and len(short_long_term) > 0:
                                total_debt = float(long_term.iloc[0]) + float(short_long_term.iloc[0])
                        # Otherwise try "Long Term Debt" + "Short Term Debt"
                        elif "Long Term Debt" in balance_sheet.index and "Short Term Debt" in balance_sheet.index:
                            long_term = balance_sheet.loc["Long Term Debt"]
                            short_term = balance_sheet.loc["Short Term Debt"]
                            if len(long_term) > 0 and len(short_term) > 0:
                                total_debt = float(long_term.iloc[0]) + float(short_term.iloc[0])
                except Exception:
                    pass
            
            # Fetch Cash
            # Priority 2a: totalCash from info
            try:
                info = ticker_obj.info
                if "totalCash" in info and info["totalCash"] is not None:
                    cash = float(info["totalCash"])
            except Exception:
                pass
            
            # Priority 2b: From balance sheet
            if cash is None:
                try:
                    balance_sheet = ticker_obj.balance_sheet
                    if balance_sheet is not None and not balance_sheet.empty:
                        # Try "Cash And Cash Equivalents" first
                        if "Cash And Cash Equivalents" in balance_sheet.index:
                            cash_row = balance_sheet.loc["Cash And Cash Equivalents"]
                            if len(cash_row) > 0:
                                cash = float(cash_row.iloc[0])
                        # Otherwise try "Cash"
                        elif "Cash" in balance_sheet.index:
                            cash_row = balance_sheet.loc["Cash"]
                            if len(cash_row) > 0:
                                cash = float(cash_row.iloc[0])
                        # Fallback: "Cash And Short Term Investments"
                        elif "Cash And Short Term Investments" in balance_sheet.index:
                            cash_row = balance_sheet.loc["Cash And Short Term Investments"]
                            if len(cash_row) > 0:
                                cash = float(cash_row.iloc[0])
                except Exception:
                    pass
            
            # Compute Net Debt = Total Debt - Cash
            if total_debt is not None and cash is not None:
                net_debt_raw = total_debt - cash
            elif total_debt is not None:
                # If we have debt but no cash, net debt = total debt
                net_debt_raw = total_debt
            elif cash is not None:
                # If we have cash but no debt, net debt = -cash (negative net debt)
                net_debt_raw = -cash
        
        result["net_debt_raw"] = net_debt_raw
        
        # Cache the result
        _fundamentals_cache[ticker] = result
        return result
        
    except Exception as e:
        # On error, cache None values and return
        result["net_debt_raw"] = None
        _fundamentals_cache[ticker] = result
        # Minimal print output only if fetch fails
        print(f"⚠️  Failed to fetch fundamentals for {ticker}: {str(e)}")
        return result


def get_company_description(ticker: str) -> str:
    """
    Fetch company description using yfinance.
    
    Args:
        ticker: Stock ticker symbol
        
    Returns:
        Company description (max 2 sentences, ~250 chars), or fallback message
    """
    try:
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        
        # Try primary source: longBusinessSummary
        description = None
        if "longBusinessSummary" in info and info["longBusinessSummary"]:
            description = info["longBusinessSummary"]
        # Fallback 1: shortName
        elif "shortName" in info and info["shortName"]:
            description = info["shortName"]
        # Fallback 2: industry
        elif "industry" in info and info["industry"]:
            description = f"Company in the {info['industry']} industry."
        # Fallback 3: sector
        elif "sector" in info and info["sector"]:
            description = f"Company in the {info['sector']} sector."
        
        if not description:
            return "Business description unavailable."
        
        # Clean and format description
        description = description.strip()
        
        # Remove excessive whitespace
        description = re.sub(r'\s+', ' ', description)
        
        # Extract sentences (preserve punctuation)
        # Pattern matches: sentence text + punctuation + optional whitespace
        sentence_pattern = r'([^.!?]+[.!?]+)'
        sentences = re.findall(sentence_pattern, description)
        
        # If no sentences found with punctuation, try splitting and adding punctuation
        if not sentences:
            # Fallback: split by punctuation and reconstruct
            parts = re.split(r'([.!?]+)', description)
            if parts:
                # Reconstruct sentences
                sentences = []
                for i in range(0, len(parts) - 1, 2):
                    if i + 1 < len(parts):
                        sent = (parts[i] + parts[i + 1]).strip()
                        if sent:
                            sentences.append(sent)
                # If still no sentences, use the whole description
                if not sentences:
                    sentences = [description.strip()]
        
        if not sentences:
            return "Business description unavailable."
        
        # Take first 1-2 sentences (prefer 1, use 2 if first is very short)
        first_sent = sentences[0].strip()
        if len(first_sent) < 30 and len(sentences) > 1:
            # First sentence is very short, include second
            result = first_sent + ' ' + sentences[1].strip()
        else:
            result = first_sent
        
        # Ensure proper punctuation
        result = result.strip()
        if not result.endswith(('.', '!', '?')):
            result += '.'
        
        # Trim to ~250 characters at sentence boundary if needed
        if len(result) > 250:
            # Try to find a sentence boundary before 250 chars
            truncated = result[:247]
            # Find last sentence boundary
            last_period = truncated.rfind('.')
            last_excl = truncated.rfind('!')
            last_quest = truncated.rfind('?')
            last_boundary = max(last_period, last_excl, last_quest)
            
            if last_boundary > 50:  # Only use if we have reasonable length
                result = result[:last_boundary + 1]
            else:
                result = truncated + "..."
        
        return result
        
    except Exception as e:
        # Silently handle errors and return fallback
        return "Business description unavailable."


def get_price_at_date_usd_or_local(ticker_obj, ticker: str, target_date, window_days: int = 7) -> float | None:
    """
    Get price at a specific date with caching, retry logic, and throttling.
    
    Args:
        ticker_obj: yfinance Ticker object
        ticker: Stock ticker symbol (for cache key)
        target_date: Target date (string or pd.Timestamp)
        window_days: Days to search around target date (default 7)
        
    Returns:
        Price at date (Close price) or None if unavailable
    """
    # Create cache key using YYYY-MM-DD format
    target_dt = pd.to_datetime(target_date, errors="coerce")
    if pd.isna(target_dt):
        return None
    cache_key = f"{ticker}_{target_dt.strftime('%Y-%m-%d')}"
    
    # Check cache first
    if cache_key in _price_at_date_cache:
        return _price_at_date_cache[cache_key]
    
    # Throttle: small delay to reduce rate limiting
    time.sleep(0.1)  # 0.1s delay per ticker
    
    # Retry logic: up to 3 attempts with exponential backoff
    for attempt in range(3):
        try:
            # On final attempt, widen window to ±30 days
            current_window = 30 if attempt == 2 else window_days
            
            start_date = (target_dt - pd.Timedelta(days=current_window)).strftime("%Y-%m-%d")
            end_date = (target_dt + pd.Timedelta(days=current_window + 1)).strftime("%Y-%m-%d")
            
            hist = ticker_obj.history(start=start_date, end=end_date, auto_adjust=False)
            
            if hist.empty:
                # Sleep and retry with exponential backoff
                if attempt < 2:
                    sleep_time = [0.5, 1.5, 3.0][attempt]
                    time.sleep(sleep_time)
                    continue
                else:
                    # Final attempt failed, cache None
                    _price_at_date_cache[cache_key] = None
                    return None
            
            # Normalize timezone-aware index to timezone-naive
            idx = hist.index
            if getattr(idx, "tz", None) is not None:
                hist.index = idx.tz_convert(None)
            hist.index = hist.index.normalize()
            
            # Convert target date to normalized datetime
            target = pd.to_datetime(target_date).normalize()
            
            # Pick closest trading day (robust across pandas versions)
            deltas = (hist.index - target).asi8  # int64 nanoseconds
            nearest_i = int(np.abs(deltas).argmin())
            price_at_date = float(hist.iloc[nearest_i]["Close"])
            
            if pd.isna(price_at_date) or price_at_date <= 0:
                if attempt < 2:
                    sleep_time = [0.5, 1.5, 3.0][attempt]
                    time.sleep(sleep_time)
                    continue
                else:
                    _price_at_date_cache[cache_key] = None
                    return None
            
            # Success: cache and return
            _price_at_date_cache[cache_key] = price_at_date
            return price_at_date
            
        except Exception:
            # On exception, retry with backoff
            if attempt < 2:
                sleep_time = [0.5, 1.5, 3.0][attempt]
                time.sleep(sleep_time)
                continue
            else:
                _price_at_date_cache[cache_key] = None
                return None
    
    # All attempts failed
    _price_at_date_cache[cache_key] = None
    return None


def compute_cash_flow_yield_at_low_date(ticker: str, low_date: str | pd.Timestamp, currency: str | None) -> float | None:
    """
    Compute Cash Flow Yield at a specific low date.
    
    Cash Flow Yield = (Operating Cash Flow TTM - CapEx TTM) / Enterprise Value at low date
    
    Args:
        ticker: Stock ticker symbol
        low_date: Low date (string in ISO format or pd.Timestamp)
        currency: Currency code for the ticker (for Net Debt conversion)
        
    Returns:
        Cash Flow Yield as decimal (e.g., 0.084 = 8.4%), or None if cannot compute
    """
    # Create cache key
    cache_key = f"{ticker}_{low_date}"
    if cache_key in _cfy_at_low_date_cache:
        return _cfy_at_low_date_cache[cache_key]
    
    try:
        # Parse low_date
        if isinstance(low_date, str):
            low_date_ts = pd.to_datetime(low_date, errors="coerce")
        else:
            low_date_ts = pd.to_datetime(low_date, errors="coerce")
        
        if pd.isna(low_date_ts):
            _cfy_at_low_date_cache[cache_key] = None
            return None
        
        ticker_obj = yf.Ticker(ticker)
        
        # 1. Get price at low date using shared helper (with caching and retry logic)
        price_at_date = get_price_at_date_usd_or_local(ticker_obj, ticker, low_date, window_days=7)
        
        if price_at_date is None or price_at_date <= 0:
            _cfy_debug_counters["missing_price"] += 1
            _cfy_at_low_date_cache[cache_key] = None
            return None
        
        # 2. Get shares outstanding
        shares_outstanding = None
        try:
            fast_info = ticker_obj.fast_info
            if hasattr(fast_info, 'shares') and fast_info.shares:
                shares_outstanding = float(fast_info.shares)
        except Exception:
            pass
        
        if shares_outstanding is None:
            try:
                info = ticker_obj.info
                if "sharesOutstanding" in info and info["sharesOutstanding"]:
                    shares_outstanding = float(info["sharesOutstanding"])
            except Exception:
                pass
        
        if shares_outstanding is None or shares_outstanding <= 0:
            _cfy_debug_counters["missing_shares"] += 1
            _cfy_at_low_date_cache[cache_key] = None
            return None
        
        # 3. Compute Market Cap at low date (in local currency, then convert to USD)
        market_cap_at_low_date_local = price_at_date * shares_outstanding
        market_cap_at_low_date_usd = convert_to_usd(market_cap_at_low_date_local, currency)
        
        if market_cap_at_low_date_usd is None or market_cap_at_low_date_usd <= 0:
            _cfy_at_low_date_cache[cache_key] = None
            return None
        
        # 4. Get Net Debt (USD)
        net_debt_usd = None
        
        # Try to get from existing computation if available (from fundamentals cache)
        if ticker in _fundamentals_cache:
            net_debt_raw = _fundamentals_cache[ticker].get("net_debt_raw")
            if net_debt_raw is not None:
                net_debt_usd = convert_to_usd(net_debt_raw, currency)
        
        # If not available, compute from Yahoo
        if net_debt_usd is None:
            try:
                info = ticker_obj.info
                # Try netDebt first
                if "netDebt" in info and info["netDebt"] is not None:
                    net_debt_raw = float(info["netDebt"])
                    net_debt_usd = convert_to_usd(net_debt_raw, currency)
                else:
                    # Compute from totalDebt - totalCash
                    total_debt = None
                    total_cash = None
                    
                    if "totalDebt" in info and info["totalDebt"] is not None:
                        total_debt = float(info["totalDebt"])
                    if "totalCash" in info and info["totalCash"] is not None:
                        total_cash = float(info["totalCash"])
                    
                    if total_debt is not None or total_cash is not None:
                        net_debt_raw = (total_debt or 0) - (total_cash or 0)
                        net_debt_usd = convert_to_usd(net_debt_raw, currency)
            except Exception:
                pass
        
        # 5. Compute Enterprise Value at low date
        if net_debt_usd is None:
            net_debt_usd = 0.0  # Assume zero if unavailable
        
        enterprise_value_at_low_date = market_cap_at_low_date_usd + net_debt_usd
        
        if enterprise_value_at_low_date <= 0:
            _cfy_at_low_date_cache[cache_key] = None
            return None
        
        # 6. Get Operating Cash Flow TTM and CapEx TTM with robust fallbacks
        operating_cf_ttm = None
        capex_ttm = None
        
        # Priority 1: Use quarterly_cashflow (sum most recent 4 columns)
        try:
            quarterly_cashflow = ticker_obj.quarterly_cashflow
            if quarterly_cashflow is not None and not quarterly_cashflow.empty:
                # Get quarters with period end date <= low_date
                # Columns are typically dates (most recent first)
                eligible_quarters = []
                for col in quarterly_cashflow.columns:
                    col_date = pd.to_datetime(col, errors="coerce")
                    if not pd.isna(col_date) and col_date <= low_date_ts:
                        eligible_quarters.append(col)
                
                # Take the 4 most recent eligible quarters (or all if less than 4)
                if len(eligible_quarters) >= 4:
                    eligible_quarters = eligible_quarters[:4]
                
                if len(eligible_quarters) > 0:
                    # Get Operating Cash Flow - try multiple row name variations
                    ocf_row = None
                    ocf_row_names = [
                        "Operating Cash Flow",
                        "Total Cash From Operating Activities",
                        "Cash From Operating Activities",
                        "Cash Flow From Operating Activities",
                        "Operating Activities, Cash Flow",
                    ]
                    for row_name in ocf_row_names:
                        if row_name in quarterly_cashflow.index:
                            ocf_row = quarterly_cashflow.loc[row_name, eligible_quarters]
                            break
                    
                    if ocf_row is not None:
                        operating_cf_ttm = ocf_row.sum()
                        if pd.notna(operating_cf_ttm):
                            operating_cf_ttm = float(operating_cf_ttm)
                        else:
                            operating_cf_ttm = None
                    
                    # Get Capital Expenditures - try multiple row name variations
                    capex_row = None
                    capex_row_names = [
                        "Capital Expenditures",
                        "Capital Expenditure",
                        "CapitalExpenditure",
                        "Capital Expenditure, Net",
                        "Purchase of Fixed Assets",
                    ]
                    for row_name in capex_row_names:
                        if row_name in quarterly_cashflow.index:
                            capex_row = quarterly_cashflow.loc[row_name, eligible_quarters]
                            break
                    
                    if capex_row is not None:
                        capex_ttm = capex_row.sum()
                        if pd.notna(capex_ttm):
                            # CapEx is often negative in Yahoo, use absolute value
                            capex_ttm = abs(float(capex_ttm))
                        else:
                            capex_ttm = None
        except Exception:
            pass
        
        # Priority 2: Fallback to annual cashflow (use most recent column as proxy)
        if operating_cf_ttm is None or capex_ttm is None:
            try:
                annual_cashflow = ticker_obj.cashflow
                if annual_cashflow is not None and not annual_cashflow.empty:
                    # Use most recent column (first column is typically most recent)
                    most_recent_col = annual_cashflow.columns[0] if len(annual_cashflow.columns) > 0 else None
                    
                    if most_recent_col is not None:
                        # Get Operating Cash Flow
                        if operating_cf_ttm is None:
                            ocf_row_names = [
                                "Operating Cash Flow",
                                "Total Cash From Operating Activities",
                                "Cash From Operating Activities",
                                "Cash Flow From Operating Activities",
                                "Operating Activities, Cash Flow",
                            ]
                            for row_name in ocf_row_names:
                                if row_name in annual_cashflow.index:
                                    ocf_value = annual_cashflow.loc[row_name, most_recent_col]
                                    if pd.notna(ocf_value):
                                        operating_cf_ttm = float(ocf_value)
                                        break
                        
                        # Get Capital Expenditures
                        if capex_ttm is None:
                            capex_row_names = [
                                "Capital Expenditures",
                                "Capital Expenditure",
                                "CapitalExpenditure",
                                "Capital Expenditure, Net",
                                "Purchase of Fixed Assets",
                            ]
                            for row_name in capex_row_names:
                                if row_name in annual_cashflow.index:
                                    capex_value = annual_cashflow.loc[row_name, most_recent_col]
                                    if pd.notna(capex_value):
                                        # CapEx is often negative in Yahoo, use absolute value
                                        capex_ttm = abs(float(capex_value))
                                        break
            except Exception:
                pass
        
        # Priority 3: Fallback to info (only for OCF, not CapEx as it's often None)
        if operating_cf_ttm is None:
            try:
                info = ticker_obj.info
                if "operatingCashflow" in info and info["operatingCashflow"] is not None:
                    operating_cf_ttm = float(info["operatingCashflow"])
            except Exception:
                pass
        
        # Update debug counters (only for data we tried to get but couldn't)
        if operating_cf_ttm is None:
            _cfy_debug_counters["missing_ocf"] += 1
        if capex_ttm is None:
            _cfy_debug_counters["missing_capex"] += 1
        
        # 7. Compute Cash Flow Yield: (OCF_TTM - CapEx_TTM_abs) / EV at low date
        if operating_cf_ttm is not None and capex_ttm is not None and enterprise_value_at_low_date > 0:
            # Free Cash Flow = OCF - abs(CapEx) (in local currency)
            free_cash_flow_local = operating_cf_ttm - capex_ttm
            # Convert to USD before dividing by enterprise value (which is in USD)
            free_cash_flow_usd = convert_to_usd(free_cash_flow_local, currency)
            if free_cash_flow_usd is None:
                _cfy_at_low_date_cache[cache_key] = None
                return None
            cfy = free_cash_flow_usd / enterprise_value_at_low_date
            result = float(cfy)
            _cfy_at_low_date_cache[cache_key] = result
            return result
        else:
            _cfy_at_low_date_cache[cache_key] = None
            return None
            
    except Exception as e:
        _cfy_at_low_date_cache[cache_key] = None
        return None


def add_cash_flow_yield_at_low_date(df: pd.DataFrame, low_date_column: str) -> pd.DataFrame:
    """
    Add Cash Flow Yield column computed at the low date.
    
    Args:
        df: DataFrame with "Ticket", "Currency", and low_date_column
        low_date_column: Name of the column containing the low date (e.g., "52W Low Date")
        
    Returns:
        DataFrame with "Cash Flow Yield" column added (no filtering applied)
    """
    if "Ticket" not in df.columns or low_date_column not in df.columns:
        return df
    
    df = df.copy()
    
    # Reset debug counters
    _cfy_debug_counters["missing_ocf"] = 0
    _cfy_debug_counters["missing_capex"] = 0
    _cfy_debug_counters["missing_shares"] = 0
    _cfy_debug_counters["missing_price"] = 0
    
    # Get unique tickers to avoid duplicate API calls
    unique_tickers = df["Ticket"].dropna().unique().tolist()
    
    print(f"💰 Computing Cash Flow Yield at low date for {len(unique_tickers)} tickers...")
    
    # Compute Cash Flow Yield for each row
    def compute_cfy_for_row(row):
        ticker = row.get("Ticket")
        low_date = row.get(low_date_column)
        currency = row.get("Currency")
        
        if pd.isna(ticker) or pd.isna(low_date):
            return None
        
        return compute_cash_flow_yield_at_low_date(str(ticker), low_date, currency)
    
    df["Cash Flow Yield"] = df.apply(compute_cfy_for_row, axis=1)
    
    # Small delay to avoid rate limiting
    time.sleep(0.1)
    
    # Print debug counters
    print(f"  Debug counters: missing_OCF={_cfy_debug_counters['missing_ocf']}, "
          f"missing_CapEx={_cfy_debug_counters['missing_capex']}, "
          f"missing_shares={_cfy_debug_counters['missing_shares']}, "
          f"missing_price={_cfy_debug_counters['missing_price']}")
    
    # Do NOT filter here. Filtering is applied later after EV is added.
    return df


def compute_ev_at_low_date(ticker: str, low_date: str | pd.Timestamp, currency: str | None) -> float | None:
    """
    Compute Enterprise Value at a specific low date.
    
    EV = Market Cap at Low Date + Net Debt (USD)
    
    Args:
        ticker: Stock ticker symbol
        low_date: Low date (string in ISO format or pd.Timestamp)
        currency: Currency code for the ticker (for Net Debt conversion)
        
    Returns:
        Enterprise Value in USD, or None if cannot compute
    """
    # Create cache key
    cache_key = f"{ticker}_{low_date}"
    if cache_key in _ev_at_low_date_cache:
        return _ev_at_low_date_cache[cache_key]
    
    try:
        # Parse low_date
        if isinstance(low_date, str):
            low_date_ts = pd.to_datetime(low_date, errors="coerce")
        else:
            low_date_ts = pd.to_datetime(low_date, errors="coerce")
        
        if pd.isna(low_date_ts):
            _ev_at_low_date_cache[cache_key] = None
            return None
        
        ticker_obj = yf.Ticker(ticker)
        
        # 1. Get price at low date using shared helper (with caching and retry logic)
        price_at_date = get_price_at_date_usd_or_local(ticker_obj, ticker, low_date, window_days=7)
        
        if price_at_date is None or price_at_date <= 0:
            _ev_at_low_date_cache[cache_key] = None
            return None
        
        # 2. Get shares outstanding
        shares_outstanding = None
        try:
            fast_info = ticker_obj.fast_info
            if hasattr(fast_info, 'shares') and fast_info.shares:
                shares_outstanding = float(fast_info.shares)
        except Exception:
            pass
        
        if shares_outstanding is None:
            try:
                info = ticker_obj.info
                if "sharesOutstanding" in info and info["sharesOutstanding"]:
                    shares_outstanding = float(info["sharesOutstanding"])
            except Exception:
                pass
        
        if shares_outstanding is None or shares_outstanding <= 0:
            _ev_at_low_date_cache[cache_key] = None
            return None
        
        # 3. Compute Market Cap at low date (in local currency, then convert to USD)
        market_cap_at_low_date_local = price_at_date * shares_outstanding
        market_cap_at_low_date_usd = convert_to_usd(market_cap_at_low_date_local, currency)
        
        if market_cap_at_low_date_usd is None or market_cap_at_low_date_usd <= 0:
            _ev_at_low_date_cache[cache_key] = None
            return None
        
        # 4. Get Net Debt (USD)
        net_debt_usd = None
        
        # Try to get from existing computation if available (from fundamentals cache)
        if ticker in _fundamentals_cache:
            net_debt_raw = _fundamentals_cache[ticker].get("net_debt_raw")
            if net_debt_raw is not None:
                net_debt_usd = convert_to_usd(net_debt_raw, currency)
        
        # If not available, compute from Yahoo
        if net_debt_usd is None:
            try:
                info = ticker_obj.info
                # Try netDebt first
                if "netDebt" in info and info["netDebt"] is not None:
                    net_debt_raw = float(info["netDebt"])
                    net_debt_usd = convert_to_usd(net_debt_raw, currency)
                else:
                    # Compute from totalDebt - totalCash
                    total_debt = None
                    total_cash = None
                    
                    if "totalDebt" in info and info["totalDebt"] is not None:
                        total_debt = float(info["totalDebt"])
                    if "totalCash" in info and info["totalCash"] is not None:
                        total_cash = float(info["totalCash"])
                    
                    if total_debt is not None or total_cash is not None:
                        net_debt_raw = (total_debt or 0) - (total_cash or 0)
                        net_debt_usd = convert_to_usd(net_debt_raw, currency)
            except Exception:
                pass
        
        # 5. Compute Enterprise Value at low date
        if net_debt_usd is None:
            net_debt_usd = 0.0  # Assume zero if unavailable
        
        enterprise_value_at_low_date = market_cap_at_low_date_usd + net_debt_usd
        
        if enterprise_value_at_low_date <= 0:
            _ev_at_low_date_cache[cache_key] = None
            return None
        
        result = float(enterprise_value_at_low_date)
        _ev_at_low_date_cache[cache_key] = result
        return result
            
    except Exception as e:
        _ev_at_low_date_cache[cache_key] = None
        return None


def add_ev_at_low_date(df: pd.DataFrame, low_date_column: str) -> pd.DataFrame:
    """
    Add EV at Low Date (USD) column computed at the low date.
    
    Args:
        df: DataFrame with "Ticket", "Currency", and low_date_column
        low_date_column: Name of the column containing the low date (e.g., "52W Low Date")
        
    Returns:
        DataFrame with "EV at Low Date (USD)" column appended at the end
    """
    if "Ticket" not in df.columns or low_date_column not in df.columns:
        return df
    
    df = df.copy()
    
    # Get unique tickers to avoid duplicate API calls
    unique_tickers = df["Ticket"].dropna().unique().tolist()
    
    print(f"💼 Computing EV at low date for {len(unique_tickers)} tickers...")
    
    # Compute EV at Low Date for each row
    def compute_ev_for_row(row):
        ticker = row.get("Ticket")
        low_date = row.get(low_date_column)
        currency = row.get("Currency")
        
        if pd.isna(ticker) or pd.isna(low_date):
            return None
        
        return compute_ev_at_low_date(str(ticker), low_date, currency)
    
    df["EV at Low Date (USD)"] = df.apply(compute_ev_for_row, axis=1)
    
    # Small delay to avoid rate limiting
    time.sleep(0.1)
    
    return df


def compute_pe_at_low_date(ticker: str, low_date: str | pd.Timestamp, currency: str | None) -> float | None:
    """
    Compute P/E at Low Date.
    
    P/E at Low Date = Market Cap at Low Date / Net Income (TTM)
    
    Args:
        ticker: Stock ticker symbol
        low_date: Low date (string in ISO format or pd.Timestamp)
        currency: Currency code for the ticker (for Market Cap conversion)
        
    Returns:
        P/E ratio, or None if cannot compute
    """
    # Create cache key
    cache_key = f"{ticker}_{low_date}"
    if cache_key in _pe_at_low_date_cache:
        return _pe_at_low_date_cache[cache_key]
    
    try:
        # Parse low_date
        if isinstance(low_date, str):
            low_date_ts = pd.to_datetime(low_date, errors="coerce")
        else:
            low_date_ts = pd.to_datetime(low_date, errors="coerce")
        
        if pd.isna(low_date_ts):
            _pe_at_low_date_cache[cache_key] = None
            return None
        
        ticker_obj = yf.Ticker(ticker)
        
        # 1. Get price at low date using shared helper (with caching and retry logic)
        price_at_date = get_price_at_date_usd_or_local(ticker_obj, ticker, low_date, window_days=7)
        
        if price_at_date is None or price_at_date <= 0:
            _pe_at_low_date_cache[cache_key] = None
            return None
        
        # 2. Get shares outstanding
        shares_outstanding = None
        try:
            fast_info = ticker_obj.fast_info
            if hasattr(fast_info, 'shares') and fast_info.shares:
                shares_outstanding = float(fast_info.shares)
        except Exception:
            pass
        
        if shares_outstanding is None:
            try:
                info = ticker_obj.info
                if "sharesOutstanding" in info and info["sharesOutstanding"]:
                    shares_outstanding = float(info["sharesOutstanding"])
            except Exception:
                pass
        
        if shares_outstanding is None or shares_outstanding <= 0:
            _pe_at_low_date_cache[cache_key] = None
            return None
        
        # 3. Compute Market Cap at low date (in local currency, then convert to USD)
        market_cap_at_low_date_local = price_at_date * shares_outstanding
        market_cap_at_low_date_usd = convert_to_usd(market_cap_at_low_date_local, currency)
        
        if market_cap_at_low_date_usd is None or market_cap_at_low_date_usd <= 0:
            _pe_at_low_date_cache[cache_key] = None
            return None
        
        # 4. Get Net Income TTM
        net_income_ttm = None
        
        # Priority 1: Use quarterly income statement (sum last 4 quarters ≤ low_date)
        try:
            quarterly_financials = ticker_obj.quarterly_financials
            if quarterly_financials is not None and not quarterly_financials.empty:
                # Get quarters with period end date <= low_date
                eligible_quarters = []
                for col in quarterly_financials.columns:
                    col_date = pd.to_datetime(col, errors="coerce")
                    if not pd.isna(col_date) and col_date <= low_date_ts:
                        eligible_quarters.append(col)
                
                # Take the 4 most recent eligible quarters (or all if less than 4)
                if len(eligible_quarters) >= 4:
                    eligible_quarters = eligible_quarters[:4]
                
                if len(eligible_quarters) > 0:
                    # Look for "Net Income" row
                    if "Net Income" in quarterly_financials.index:
                        net_income_row = quarterly_financials.loc["Net Income", eligible_quarters]
                        net_income_ttm = net_income_row.sum()
                        if pd.notna(net_income_ttm):
                            net_income_ttm = float(net_income_ttm)
                        else:
                            net_income_ttm = None
        except Exception:
            pass
        
        # Priority 2: Fallback to annual income statement (most recent column)
        if net_income_ttm is None:
            try:
                annual_financials = ticker_obj.financials
                if annual_financials is not None and not annual_financials.empty:
                    # Use most recent column (first column is typically most recent)
                    most_recent_col = annual_financials.columns[0] if len(annual_financials.columns) > 0 else None
                    
                    if most_recent_col is not None:
                        if "Net Income" in annual_financials.index:
                            net_income_value = annual_financials.loc["Net Income", most_recent_col]
                            if pd.notna(net_income_value):
                                net_income_ttm = float(net_income_value)
            except Exception:
                pass
        
        # Priority 3: Fallback to netIncomeToCommon from info
        if net_income_ttm is None:
            try:
                info = ticker_obj.info
                if "netIncomeToCommon" in info and info["netIncomeToCommon"] is not None:
                    net_income_ttm = float(info["netIncomeToCommon"])
            except Exception:
                pass
        
        # 5. Compute P/E = Market Cap at Low Date (USD) / Net Income TTM (USD)
        if net_income_ttm is None or net_income_ttm <= 0:
            _pe_at_low_date_cache[cache_key] = None
            return None
        
        # Convert net income to USD before dividing by market cap (which is in USD)
        net_income_ttm_usd = convert_to_usd(net_income_ttm, currency)
        if net_income_ttm_usd is None or net_income_ttm_usd <= 0:
            _pe_at_low_date_cache[cache_key] = None
            return None
        
        pe_ratio = market_cap_at_low_date_usd / net_income_ttm_usd
        result = float(pe_ratio)
        _pe_at_low_date_cache[cache_key] = result
        return result
            
    except Exception as e:
        _pe_at_low_date_cache[cache_key] = None
        return None


def add_pe_at_low_date(df: pd.DataFrame, low_date_column: str) -> pd.DataFrame:
    """
    Add P/E at Low Date column computed at the low date.
    
    Args:
        df: DataFrame with "Ticket", "Currency", and low_date_column
        low_date_column: Name of the column containing the low date (e.g., "52W Low Date")
        
    Returns:
        DataFrame with "P/E at Low Date" column appended at the end
    """
    if "Ticket" not in df.columns or low_date_column not in df.columns:
        return df
    
    df = df.copy()
    
    # Get unique tickers to avoid duplicate API calls
    unique_tickers = df["Ticket"].dropna().unique().tolist()
    
    print(f"📈 Computing P/E at low date for {len(unique_tickers)} tickers...")
    
    # Compute P/E at Low Date for each row
    def compute_pe_for_row(row):
        ticker = row.get("Ticket")
        low_date = row.get(low_date_column)
        currency = row.get("Currency")
        
        if pd.isna(ticker) or pd.isna(low_date):
            return None
        
        return compute_pe_at_low_date(str(ticker), low_date, currency)
    
    df["P/E at Low Date"] = df.apply(compute_pe_for_row, axis=1)
    
    # Small delay to avoid rate limiting
    time.sleep(0.1)
    
    return df


def compute_debt_years(ticker: str, currency: str | None) -> float | None:
    """
    Compute Debt Years.
    
    Debt Years = Net Debt (USD) / (Operating Cash Flow - Capital Expenditures)
    
    Args:
        ticker: Stock ticker symbol
        currency: Currency code for the ticker (for Net Debt conversion)
        
    Returns:
        Debt Years, or None if cannot compute
    """
    # Create cache key
    cache_key = f"{ticker}"
    if cache_key in _debt_years_cache:
        return _debt_years_cache[cache_key]
    
    try:
        ticker_obj = yf.Ticker(ticker)
        
        # 1. Get Net Debt (USD) - reuse the same logic from EV computation
        net_debt_usd = None
        
        # Try to get from existing computation if available (from fundamentals cache)
        if ticker in _fundamentals_cache:
            net_debt_raw = _fundamentals_cache[ticker].get("net_debt_raw")
            if net_debt_raw is not None:
                net_debt_usd = convert_to_usd(net_debt_raw, currency)
        
        # If not available, compute from Yahoo
        if net_debt_usd is None:
            try:
                info = ticker_obj.info
                # Try netDebt first
                if "netDebt" in info and info["netDebt"] is not None:
                    net_debt_raw = float(info["netDebt"])
                    net_debt_usd = convert_to_usd(net_debt_raw, currency)
                else:
                    # Compute from totalDebt - totalCash
                    total_debt = None
                    total_cash = None
                    
                    if "totalDebt" in info and info["totalDebt"] is not None:
                        total_debt = float(info["totalDebt"])
                    if "totalCash" in info and info["totalCash"] is not None:
                        total_cash = float(info["totalCash"])
                    
                    if total_debt is not None or total_cash is not None:
                        net_debt_raw = (total_debt or 0) - (total_cash or 0)
                        net_debt_usd = convert_to_usd(net_debt_raw, currency)
            except Exception:
                pass
        
        if net_debt_usd is None:
            _debt_years_cache[cache_key] = None
            return None
        
        # 2. Get Free Cash Flow TTM = Operating Cash Flow - Capital Expenditures
        # Reuse the same logic from Cash Flow Yield computation
        operating_cf_ttm = None
        capex_ttm = None
        
        # Priority 1: Use quarterly_cashflow (sum most recent 4 columns)
        try:
            quarterly_cashflow = ticker_obj.quarterly_cashflow
            if quarterly_cashflow is not None and not quarterly_cashflow.empty:
                # Take first 4 columns (most recent 4 quarters)
                if len(quarterly_cashflow.columns) >= 4:
                    eligible_quarters = quarterly_cashflow.columns[:4]
                else:
                    eligible_quarters = quarterly_cashflow.columns
                
                if len(eligible_quarters) > 0:
                    # Get Operating Cash Flow - try multiple row name variations
                    ocf_row = None
                    ocf_row_names = [
                        "Operating Cash Flow",
                        "Total Cash From Operating Activities",
                        "Cash From Operating Activities",
                        "Cash Flow From Operating Activities",
                        "Operating Activities, Cash Flow",
                    ]
                    for row_name in ocf_row_names:
                        if row_name in quarterly_cashflow.index:
                            ocf_row = quarterly_cashflow.loc[row_name, eligible_quarters]
                            break
                    
                    if ocf_row is not None:
                        operating_cf_ttm = ocf_row.sum()
                        if pd.notna(operating_cf_ttm):
                            operating_cf_ttm = float(operating_cf_ttm)
                        else:
                            operating_cf_ttm = None
                    
                    # Get Capital Expenditures - try multiple row name variations
                    capex_row = None
                    capex_row_names = [
                        "Capital Expenditures",
                        "Capital Expenditure",
                        "CapitalExpenditure",
                        "Capital Expenditure, Net",
                        "Purchase of Fixed Assets",
                    ]
                    for row_name in capex_row_names:
                        if row_name in quarterly_cashflow.index:
                            capex_row = quarterly_cashflow.loc[row_name, eligible_quarters]
                            break
                    
                    if capex_row is not None:
                        capex_ttm = capex_row.sum()
                        if pd.notna(capex_ttm):
                            # CapEx is often negative in Yahoo, use absolute value
                            capex_ttm = abs(float(capex_ttm))
                        else:
                            capex_ttm = None
        except Exception:
            pass
        
        # Priority 2: Fallback to annual cashflow (use most recent column as proxy)
        if operating_cf_ttm is None or capex_ttm is None:
            try:
                annual_cashflow = ticker_obj.cashflow
                if annual_cashflow is not None and not annual_cashflow.empty:
                    # Use most recent column (first column is typically most recent)
                    most_recent_col = annual_cashflow.columns[0] if len(annual_cashflow.columns) > 0 else None
                    
                    if most_recent_col is not None:
                        # Get Operating Cash Flow
                        if operating_cf_ttm is None:
                            ocf_row_names = [
                                "Operating Cash Flow",
                                "Total Cash From Operating Activities",
                                "Cash From Operating Activities",
                                "Cash Flow From Operating Activities",
                                "Operating Activities, Cash Flow",
                            ]
                            for row_name in ocf_row_names:
                                if row_name in annual_cashflow.index:
                                    ocf_value = annual_cashflow.loc[row_name, most_recent_col]
                                    if pd.notna(ocf_value):
                                        operating_cf_ttm = float(ocf_value)
                                        break
                        
                        # Get Capital Expenditures
                        if capex_ttm is None:
                            capex_row_names = [
                                "Capital Expenditures",
                                "Capital Expenditure",
                                "CapitalExpenditure",
                                "Capital Expenditure, Net",
                                "Purchase of Fixed Assets",
                            ]
                            for row_name in capex_row_names:
                                if row_name in annual_cashflow.index:
                                    capex_value = annual_cashflow.loc[row_name, most_recent_col]
                                    if pd.notna(capex_value):
                                        # CapEx is often negative in Yahoo, use absolute value
                                        capex_ttm = abs(float(capex_value))
                                        break
            except Exception:
                pass
        
        # Priority 3: Fallback to info (only for OCF, not CapEx as it's often None)
        if operating_cf_ttm is None:
            try:
                info = ticker_obj.info
                if "operatingCashflow" in info and info["operatingCashflow"] is not None:
                    operating_cf_ttm = float(info["operatingCashflow"])
            except Exception:
                pass
        
        # 3. Compute Free Cash Flow TTM = OCF - abs(CapEx) (in local currency)
        if operating_cf_ttm is None or capex_ttm is None:
            _debt_years_cache[cache_key] = None
            return None
        
        free_cash_flow_ttm_local = operating_cf_ttm - capex_ttm
        # Convert to USD before dividing by Net Debt (which is in USD)
        free_cash_flow_ttm_usd = convert_to_usd(free_cash_flow_ttm_local, currency)
        if free_cash_flow_ttm_usd is None or free_cash_flow_ttm_usd <= 0:
            _debt_years_cache[cache_key] = None
            return None
        
        # 4. Compute Debt Years = Net Debt (USD) / Free Cash Flow TTM (USD)
        debt_years = net_debt_usd / free_cash_flow_ttm_usd
        result = float(debt_years)
        _debt_years_cache[cache_key] = result
        return result
            
    except Exception as e:
        _debt_years_cache[cache_key] = None
        return None


def add_debt_years(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add Debt Years column.
    
    Args:
        df: DataFrame with "Ticket" and "Currency" columns
        
    Returns:
        DataFrame with "Debt Years" column appended at the end
    """
    if "Ticket" not in df.columns:
        return df
    
    df = df.copy()
    
    # Get unique tickers to avoid duplicate API calls
    unique_tickers = df["Ticket"].dropna().unique().tolist()
    
    print(f"💳 Computing Debt Years for {len(unique_tickers)} tickers...")
    
    # Compute Debt Years for each row
    def compute_debt_years_for_row(row):
        ticker = row.get("Ticket")
        currency = row.get("Currency")
        
        if pd.isna(ticker):
            return None
        
        return compute_debt_years(str(ticker), currency)
    
    df["Debt Years"] = df.apply(compute_debt_years_for_row, axis=1)
    
    # Small delay to avoid rate limiting
    time.sleep(0.1)
    
    return df


def add_company_descriptions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add Company Description column to dataframe after Ticket Name.
    
    Args:
        df: DataFrame with "Ticket" column
        
    Returns:
        DataFrame with "Company Description" column inserted after "Ticket Name"
    """
    if "Ticket" not in df.columns:
        return df
    
    # Get unique tickers to avoid duplicate API calls
    unique_tickers = df["Ticket"].unique()
    
    # Create description mapping with rate limiting
    description_map = {}
    for ticker in unique_tickers:
        if pd.isna(ticker) or ticker == "":
            description_map[ticker] = "Business description unavailable."
        else:
            description_map[ticker] = get_company_description(str(ticker))
            # Small delay to avoid rate limiting
            time.sleep(0.1)
    
    # Add descriptions to dataframe
    df = df.copy()
    df["Company Description"] = df["Ticket"].map(description_map)
    
    # Reorder columns: insert "Company Description" after "Ticket Name"
    if "Ticket Name" in df.columns:
        cols = list(df.columns)
        # Remove Company Description from current position
        cols.remove("Company Description")
        # Find index of "Ticket Name"
        ticket_name_idx = cols.index("Ticket Name")
        # Insert after "Ticket Name"
        cols.insert(ticket_name_idx + 1, "Company Description")
        df = df[cols]
    
    return df


def add_currency_and_usd_price(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add Currency column and optional Price Today (USD) column to dataframe.
    
    Args:
        df: DataFrame with "Ticket" and "Price Today" columns
        
    Returns:
        DataFrame with "Currency" and "Price Today (USD)" columns added
    """
    if "Ticket" not in df.columns:
        return df
    
    df = df.copy()
    
    # Get unique tickers to avoid duplicate API calls
    unique_tickers = df["Ticket"].dropna().unique().tolist()
    
    # Fetch metadata for all tickers in batch
    print("💱 Fetching currency metadata...")
    meta_map = get_ticker_meta_batch(unique_tickers)
    
    # Map currencies to dataframe
    df["Currency"] = df["Ticket"].map(lambda t: meta_map.get(str(t), {}).get("currency") if t and not pd.isna(t) else None)
    
    # Convert Price Today to USD if available
    if "Price Today" in df.columns:
        print("💱 Converting prices to USD...")
        df["Price Today (USD)"] = df.apply(
            lambda row: convert_to_usd(row.get("Price Today"), row.get("Currency")),
            axis=1
        )
    else:
        df["Price Today (USD)"] = None
    
    # Reorder columns: insert Currency and Price Today (USD) after Company Description
    if "Company Description" in df.columns:
        cols = list(df.columns)
        # Remove Currency and Price Today (USD) from current positions
        for col in ["Currency", "Price Today (USD)"]:
            if col in cols:
                cols.remove(col)
        
        # Find index of Company Description
        company_desc_idx = cols.index("Company Description")
        # Insert Currency and Price Today (USD) after Company Description
        cols.insert(company_desc_idx + 1, "Currency")
        if "Price Today (USD)" in df.columns:
            # Insert Price Today (USD) after Currency
            currency_idx = cols.index("Currency")
            cols.insert(currency_idx + 1, "Price Today (USD)")
        df = df[cols]
    elif "Ticket Name" in df.columns:
        # Fallback: insert after Ticket Name if Company Description doesn't exist
        cols = list(df.columns)
        for col in ["Currency", "Price Today (USD)"]:
            if col in cols:
                cols.remove(col)
        
        ticket_name_idx = cols.index("Ticket Name")
        cols.insert(ticket_name_idx + 1, "Currency")
        if "Price Today (USD)" in df.columns:
            currency_idx = cols.index("Currency")
            cols.insert(currency_idx + 1, "Price Today (USD)")
        df = df[cols]
    
    return df


def add_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add fundamental data columns (net_income_ltm, market_cap_usd, net_debt_usd) to dataframe.
    Columns are appended at the END of the dataframe.
    Note: Cash Flow Yield is computed separately at low date, so not included here.
    
    Args:
        df: DataFrame with "Ticket" and "Currency" columns
        
    Returns:
        DataFrame with "net_income_ltm", "market_cap_usd", and "Net Debt (USD)" columns appended at the end
    """
    if "Ticket" not in df.columns:
        return df
    
    df = df.copy()
    
    # Get unique tickers to avoid duplicate API calls
    unique_tickers = df["Ticket"].dropna().unique().tolist()
    
    # Fetch fundamentals for all unique tickers (with caching)
    print("📊 Fetching fundamental data...")
    fundamentals_map = {}
    for ticker in unique_tickers:
        if pd.isna(ticker) or ticker == "":
            fundamentals_map[ticker] = {"net_income_ltm": None, "market_cap_usd": None, "cash_flow_yield": None, "net_debt_raw": None}
        else:
            ticker_str = str(ticker)
            # This will use cache if already fetched
            fundamentals_map[ticker_str] = get_yahoo_fundamentals(ticker_str)
            # Small delay to avoid rate limiting
            time.sleep(0.1)
    
    # Map fundamentals to dataframe
    df["net_income_ltm"] = df["Ticket"].apply(
        lambda t: fundamentals_map.get(str(t), {}).get("net_income_ltm") if t and not pd.isna(t) else None
    )
    df["market_cap_usd"] = df["Ticket"].apply(
        lambda t: fundamentals_map.get(str(t), {}).get("market_cap_usd") if t and not pd.isna(t) else None
    )
    # Note: Cash Flow Yield is computed separately at low date, so not added here
    
    # Convert Net Debt to USD using Currency column
    if "Currency" in df.columns:
        df["Net Debt (USD)"] = df.apply(
            lambda row: convert_to_usd(
                fundamentals_map.get(str(row.get("Ticket")), {}).get("net_debt_raw") if row.get("Ticket") and not pd.isna(row.get("Ticket")) else None,
                row.get("Currency")
            ),
            axis=1
        )
    else:
        # If Currency column doesn't exist, set to None
        df["Net Debt (USD)"] = None
    
    # Rename columns immediately after adding them
    df = df.rename(columns={
        "net_income_ltm": "Net Income LTM",
        "market_cap_usd": "Market Cap (USD)"
    })
    
    # Columns are already appended at the end (pandas map adds them at the end)
    return df


def format_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Format all numeric columns with proper types and rounding.
    This function applies ALL human-friendly formatting.
    """
    df = df.copy()
    
    # Percentage columns: multiply by 100 (convert from decimal) and round to 1 decimal
    for col in PERCENTAGE_COLUMNS:
        if col in df.columns:
            df[col] = (pd.to_numeric(df[col], errors="coerce") * 100).round(1)
    
    # Price columns: round to 2 decimals
    price_cols = [
        "Price Today",
        "Price Today (USD)",
        "52W Low Price",
        "Price (90D Ago)",
        "Price Peak (5Y)",
        "Bottom Price After Peak (5Y)",
        "180D Low Price",
        "Spike +30% Threshold",
    ]
    for col in price_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)
    
    # Avg Volume: convert to integer (no decimals)
    if "Avg Volume (30D)" in df.columns:
        df["Avg Volume (30D)"] = pd.to_numeric(df["Avg Volume (30D)"], errors="coerce").fillna(0).astype(int)
    
    # Volatility: round to 4 decimals
    if "Volatility (90D)" in df.columns:
        df["Volatility (90D)"] = pd.to_numeric(df["Volatility (90D)"], errors="coerce").round(4)
    
    # Ensure boolean columns remain boolean (not converted to strings)
    if "Spike +30% In Last 30D" in df.columns:
        df["Spike +30% In Last 30D"] = df["Spike +30% In Last 30D"].astype(bool)
    
    # Format fundamental columns: rounded whole-dollar values as nullable integers
    if "Net Income LTM" in df.columns:
        df["Net Income LTM"] = pd.to_numeric(df["Net Income LTM"], errors="coerce").round(0).astype("Int64")
    if "Market Cap (USD)" in df.columns:
        df["Market Cap (USD)"] = pd.to_numeric(df["Market Cap (USD)"], errors="coerce").round(0).astype("Int64")
    
    # Cash Flow Yield: round to 4 decimal places (keep as float, not percentage)
    if "Cash Flow Yield" in df.columns:
        df["Cash Flow Yield"] = pd.to_numeric(df["Cash Flow Yield"], errors="coerce").round(4)
    
    # Net Debt (USD): round to 0 decimals and use nullable integer dtype
    if "Net Debt (USD)" in df.columns:
        df["Net Debt (USD)"] = pd.to_numeric(df["Net Debt (USD)"], errors="coerce").round(0).astype("Int64")
    
    # EV at Low Date (USD): round to 0 decimals and use nullable integer dtype
    if "EV at Low Date (USD)" in df.columns:
        df["EV at Low Date (USD)"] = pd.to_numeric(df["EV at Low Date (USD)"], errors="coerce").round(0).astype("Int64")
    
    # P/E at Low Date: round to 2 decimal places
    if "P/E at Low Date" in df.columns:
        df["P/E at Low Date"] = pd.to_numeric(df["P/E at Low Date"], errors="coerce").round(2)
    
    # Debt Years: round to 2 decimal places
    if "Debt Years" in df.columns:
        df["Debt Years"] = pd.to_numeric(df["Debt Years"], errors="coerce").round(2)
    
    return df


def log_export_sanity(df: pd.DataFrame, category: str) -> None:
    """
    Log sanity checks for exported dataframe.
    """
    print(f"\n Sanity check for Category {category}:")
    print(f"  Total rows: {len(df):,}")
    
    if "Return (90D) %" in df.columns:
        valid_returns = df["Return (90D) %"].dropna()
        if len(valid_returns) > 0:
            print(f"  Return (90D) %: min={valid_returns.min():.1f}, max={valid_returns.max():.1f}, mean={valid_returns.mean():.1f}")
    
    if "Drawdown From Peak (5Y) %" in df.columns:
        valid_drawdowns = df["Drawdown From Peak (5Y) %"].dropna()
        if len(valid_drawdowns) > 0:
            print(f"  Drawdown %: min={valid_drawdowns.min():.1f}, max={valid_drawdowns.max():.1f}, mean={valid_drawdowns.mean():.1f}")
    
    if "Price Today" in df.columns:
        valid_prices = df["Price Today"].dropna()
        if len(valid_prices) > 0:
            print(f"  Price Today: min={valid_prices.min():.2f}, max={valid_prices.max():.2f}, mean={valid_prices.mean():.2f}")
    
    if "Avg Volume (30D)" in df.columns:
        valid_volumes = df["Avg Volume (30D)"].dropna()
        if len(valid_volumes) > 0:
            print(f"  Avg Volume: min={valid_volumes.min():,}, max={valid_volumes.max():,}, mean={valid_volumes.mean():,.0f}")
    
    if "Volatility (90D)" in df.columns:
        valid_volatility = df["Volatility (90D)"].dropna()
        if len(valid_volatility) > 0:
            print(f"  Volatility: min={valid_volatility.min():.4f}, max={valid_volatility.max():.4f}, mean={valid_volatility.mean():.4f}")


def _resolve_low_date_column(df: pd.DataFrame, snake_case: str, formatted: str) -> str:
    """
    Resolve low date column name by checking for snake_case first, then formatted name.
    
    Args:
        df: DataFrame to check
        snake_case: Snake case column name (e.g., "low_52w_date")
        formatted: Formatted column name (e.g., "52W Low Date")
        
    Returns:
        Column name that exists in df, or formatted name as fallback
    """
    if snake_case in df.columns:
        return snake_case
    elif formatted in df.columns:
        return formatted
    else:
        # Fallback to formatted name (will cause error if truly missing, but preserves existing behavior)
        return formatted


def clean_and_export(
    input_csv: Path,
    output_dir: Path,
    create_b_only: bool = False,
) -> None:
    """
    Read screen output CSV, clean it, and export candidates for A, B, C.
    """
    # Read input
    df = pd.read_csv(input_csv)
    
    # Drop unwanted columns
    cols_to_drop = [c for c in COLUMNS_TO_DROP if c in df.columns]
    df = df.drop(columns=cols_to_drop)
    
    # Rename columns
    rename_map = {k: v for k, v in COLUMN_RENAME.items() if k in df.columns}
    df = df.rename(columns=rename_map)
    
    # Filter and export Category A
    if "Qualifies A" in df.columns:
        df_a = df[df["Qualifies A"].eq(True)].copy()
        if not df_a.empty:
            # Fill missing Ticket Name values
            df_a = fill_missing_ticker_names(df_a)
            # Add company descriptions
            print("Fetching company descriptions for Category A...")
            df_a = add_company_descriptions(df_a)
            # Add currency and USD price
            df_a = add_currency_and_usd_price(df_a)
            # Resolve low date column (check for snake_case first, then formatted)
            low_date_col_a = _resolve_low_date_column(df_a, "low_52w_date", "52W Low Date")
            # Add Cash Flow Yield at low date
            df_a = add_cash_flow_yield_at_low_date(df_a, low_date_col_a)
            # Add EV at low date
            df_a = add_ev_at_low_date(df_a, low_date_col_a)
            # Add P/E at low date
            df_a = add_pe_at_low_date(df_a, low_date_col_a)
            # Add Debt Years
            df_a = add_debt_years(df_a)
            # Add fundamentals (needed for leverage filter)
            df_a = add_fundamentals(df_a)
            # Filter: keep only rows where Cash Flow Yield >= 0.03
            before = len(df_a)
            df_a = df_a[df_a["Cash Flow Yield"].notna() & (df_a["Cash Flow Yield"] >= 0.03)].copy()
            after = len(df_a)
            print(f"Dropped {before - after} rows with Cash Flow Yield < 3% or missing (kept {after})")
            # Apply leverage filter: Cash Flow Yield >= 0.03, Net Debt > 0, Debt Years < 6
            before_leverage = len(df_a)
            df_a = df_a[
                df_a["Cash Flow Yield"].notna() & (df_a["Cash Flow Yield"] >= 0.03) &
                df_a["EV at Low Date (USD)"].notna() &
                df_a["Debt Years"].notna() &
                df_a["Net Debt (USD)"].notna() & (df_a["Net Debt (USD)"] > 0) &
                (df_a["Debt Years"] < 6)
            ].copy()
            after_leverage = len(df_a)
            print(f"  Dropped {before_leverage - after_leverage} rows due to Debt Years / leverage filter (kept {after_leverage})")
            # Format numeric columns
            df_a = format_numeric_columns(df_a)
            # Sort by Return (90D) % descending
            if "Return (90D) %" in df_a.columns:
                try:
                    df_a = df_a.sort_values("Return (90D) %", ascending=False, na_position="last")
                except (KeyError, ValueError):
                    # Skip sorting if column is missing or invalid
                    pass
            # Reorder columns (preserve fundamentals columns to append at end)
            fundamentals_cols = []
            if "Net Income LTM" in df_a.columns:
                fundamentals_cols.append("Net Income LTM")
            if "Market Cap (USD)" in df_a.columns:
                fundamentals_cols.append("Market Cap (USD)")
            if "Cash Flow Yield" in df_a.columns:
                fundamentals_cols.append("Cash Flow Yield")
            if "Net Debt (USD)" in df_a.columns:
                fundamentals_cols.append("Net Debt (USD)")
            if "EV at Low Date (USD)" in df_a.columns:
                fundamentals_cols.append("EV at Low Date (USD)")
            if "P/E at Low Date" in df_a.columns:
                fundamentals_cols.append("P/E at Low Date")
            if "Debt Years" in df_a.columns:
                fundamentals_cols.append("Debt Years")
            cols_to_keep = [c for c in COLUMN_ORDER if c in df_a.columns]
            # Append fundamentals columns at the end
            cols_to_keep.extend(fundamentals_cols)
            df_a = df_a[cols_to_keep]
            # Export
            output_path = output_dir / "candidates_A.csv"
            df_a.to_csv(output_path, index=False)
            print(f" Exported {len(df_a):,} Category A candidates -> {output_path}")
            log_export_sanity(df_a, "A")
        else:
            print("  No Category A candidates found")
    
    # Filter and export Category B
    if "Qualifies B" in df.columns:
        df_b = df[df["Qualifies B"].eq(True)].copy()
        if not df_b.empty:
            # Fill missing Ticket Name values
            df_b = fill_missing_ticker_names(df_b)
            # Add company descriptions
            print(" Fetching company descriptions for Category B...")
            df_b = add_company_descriptions(df_b)
            # Add currency and USD price
            df_b = add_currency_and_usd_price(df_b)
            # Resolve low date column (check for snake_case first, then formatted)
            low_date_col_b = _resolve_low_date_column(df_b, "bottom_post_peak_date", "Bottom Date After Peak (5Y)")
            # Add Cash Flow Yield at low date
            df_b = add_cash_flow_yield_at_low_date(df_b, low_date_col_b)
            # Add EV at low date
            df_b = add_ev_at_low_date(df_b, low_date_col_b)
            # Add P/E at low date
            df_b = add_pe_at_low_date(df_b, low_date_col_b)
            # Add Debt Years
            df_b = add_debt_years(df_b)
            # Add fundamentals (needed for leverage filter)
            df_b = add_fundamentals(df_b)
            # Filter: keep only rows where Cash Flow Yield >= 0.03
            before = len(df_b)
            df_b = df_b[df_b["Cash Flow Yield"].notna() & (df_b["Cash Flow Yield"] >= 0.03)].copy()
            after = len(df_b)
            print(f" Dropped {before - after} rows with Cash Flow Yield < 3% or missing (kept {after})")
            # Apply leverage filter: Cash Flow Yield >= 0.03, Net Debt > 0, Debt Years < 6
            before_leverage = len(df_b)
            df_b = df_b[
                df_b["Cash Flow Yield"].notna() & (df_b["Cash Flow Yield"] >= 0.03) &
                df_b["EV at Low Date (USD)"].notna() &
                df_b["Debt Years"].notna() &
                df_b["Net Debt (USD)"].notna() & (df_b["Net Debt (USD)"] > 0) &
                (df_b["Debt Years"] < 6)
            ].copy()
            after_leverage = len(df_b)
            print(f"  Dropped {before_leverage - after_leverage} rows due to Debt Years / leverage filter (kept {after_leverage})")
            # Format numeric columns
            df_b = format_numeric_columns(df_b)
            # Sort by Return (90D) % descending
            if "Return (90D) %" in df_b.columns:
                try:
                    df_b = df_b.sort_values("Return (90D) %", ascending=False, na_position="last")
                except (KeyError, ValueError):
                    # Skip sorting if column is missing or invalid
                    pass
            # Reorder columns (preserve fundamentals columns to append at end)
            fundamentals_cols = []
            if "Net Income LTM" in df_b.columns:
                fundamentals_cols.append("Net Income LTM")
            if "Market Cap (USD)" in df_b.columns:
                fundamentals_cols.append("Market Cap (USD)")
            if "Cash Flow Yield" in df_b.columns:
                fundamentals_cols.append("Cash Flow Yield")
            if "Net Debt (USD)" in df_b.columns:
                fundamentals_cols.append("Net Debt (USD)")
            if "EV at Low Date (USD)" in df_b.columns:
                fundamentals_cols.append("EV at Low Date (USD)")
            if "P/E at Low Date" in df_b.columns:
                fundamentals_cols.append("P/E at Low Date")
            if "Debt Years" in df_b.columns:
                fundamentals_cols.append("Debt Years")
            cols_to_keep = [c for c in COLUMN_ORDER if c in df_b.columns]
            # Append fundamentals columns at the end
            cols_to_keep.extend(fundamentals_cols)
            df_b = df_b[cols_to_keep]
            # Export
            output_path = output_dir / "candidates_B.csv"
            df_b.to_csv(output_path, index=False)
            print(f" Exported {len(df_b):,} Category B candidates -> {output_path}")
            log_export_sanity(df_b, "B")
            
            # Optional: B_only (B True and A not True)
            if create_b_only and "Qualifies A" in df_b.columns:
                df_b_only = df_b[~df_b["Qualifies A"].eq(True)].copy()
                if not df_b_only.empty:
                    output_path_b_only = output_dir / "candidates_B_only.csv"
                    df_b_only.to_csv(output_path_b_only, index=False)
                    print(f" Exported {len(df_b_only):,} Category B-only candidates -> {output_path_b_only}")
        else:
            print("  No Category B candidates found")
    
    # Filter and export Category C
    if "Qualifies C" in df.columns:
        df_c = df[df["Qualifies C"].eq(True)].copy()
        if not df_c.empty:
            # Fill missing Ticket Name values
            df_c = fill_missing_ticker_names(df_c)
            # Add company descriptions
            print(" Fetching company descriptions for Category C...")
            df_c = add_company_descriptions(df_c)
            # Add currency and USD price
            df_c = add_currency_and_usd_price(df_c)
            # Resolve low date column (check for snake_case first, then formatted)
            low_date_col_c = _resolve_low_date_column(df_c, "low180_date", "180D Low Date")
            # Add Cash Flow Yield at low date
            df_c = add_cash_flow_yield_at_low_date(df_c, low_date_col_c)
            # Add EV at low date
            df_c = add_ev_at_low_date(df_c, low_date_col_c)
            # Add P/E at low date
            df_c = add_pe_at_low_date(df_c, low_date_col_c)
            # Add Debt Years
            df_c = add_debt_years(df_c)
            # Add fundamentals (needed for leverage filter)
            df_c = add_fundamentals(df_c)
            # Filter: keep only rows where Cash Flow Yield >= 0.03
            before = len(df_c)
            df_c = df_c[df_c["Cash Flow Yield"].notna() & (df_c["Cash Flow Yield"] >= 0.03)].copy()
            after = len(df_c)
            print(f" Dropped {before - after} rows with Cash Flow Yield < 3% or missing (kept {after})")
            # Apply leverage filter: Cash Flow Yield >= 0.03, Net Debt > 0, Debt Years < 6
            before_leverage = len(df_c)
            df_c = df_c[
                df_c["Cash Flow Yield"].notna() & (df_c["Cash Flow Yield"] >= 0.03) &
                df_c["EV at Low Date (USD)"].notna() &
                df_c["Debt Years"].notna() &
                df_c["Net Debt (USD)"].notna() & (df_c["Net Debt (USD)"] > 0) &
                (df_c["Debt Years"] < 6)
            ].copy()
            after_leverage = len(df_c)
            print(f"  Dropped {before_leverage - after_leverage} rows due to Debt Years / leverage filter (kept {after_leverage})")
            # Format numeric columns (before sorting to ensure proper types)
            df_c = format_numeric_columns(df_c)
            # Sort by Return (90D) % descending, or Spike +30% Date descending if available
            if "Spike +30% Date" in df_c.columns:
                try:
                    # Convert date column to datetime for sorting (keep original ISO string format)
                    date_series = pd.to_datetime(df_c["Spike +30% Date"], errors="coerce")
                    df_c = df_c.copy()
                    df_c["_sort_date"] = date_series
                    df_c = df_c.sort_values("_sort_date", ascending=False, na_position="last")
                    df_c = df_c.drop(columns=["_sort_date"])
                    # Original date format (ISO string) is preserved
                except (KeyError, ValueError):
                    # Skip sorting if column is missing or invalid
                    pass
            elif "Return (90D) %" in df_c.columns:
                try:
                    df_c = df_c.sort_values("Return (90D) %", ascending=False, na_position="last")
                except (KeyError, ValueError):
                    # Skip sorting if column is missing or invalid
                    pass
            # Reorder columns (preserve fundamentals columns to append at end)
            fundamentals_cols = []
            if "Net Income LTM" in df_c.columns:
                fundamentals_cols.append("Net Income LTM")
            if "Market Cap (USD)" in df_c.columns:
                fundamentals_cols.append("Market Cap (USD)")
            if "Cash Flow Yield" in df_c.columns:
                fundamentals_cols.append("Cash Flow Yield")
            if "Net Debt (USD)" in df_c.columns:
                fundamentals_cols.append("Net Debt (USD)")
            if "EV at Low Date (USD)" in df_c.columns:
                fundamentals_cols.append("EV at Low Date (USD)")
            if "P/E at Low Date" in df_c.columns:
                fundamentals_cols.append("P/E at Low Date")
            if "Debt Years" in df_c.columns:
                fundamentals_cols.append("Debt Years")
            cols_to_keep = [c for c in COLUMN_ORDER if c in df_c.columns]
            # Append fundamentals columns at the end
            cols_to_keep.extend(fundamentals_cols)
            df_c = df_c[cols_to_keep]
            # Export
            output_path = output_dir / "candidates_C.csv"
            df_c.to_csv(output_path, index=False)
            print(f" Exported {len(df_c):,} Category C candidates -> {output_path}")
            log_export_sanity(df_c, "C")
        else:
            print("  No Category C candidates found")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Export cleaned candidate files from screen output CSV."
    )
    ap.add_argument(
        "--input",
        required=True,
        help="Path to screen output CSV (e.g. data/results/screen_run_final.csv)",
    )
    ap.add_argument(
        "--outdir",
        default="data/results",
        help="Output directory for candidate files (default: data/results)",
    )
    ap.add_argument(
        "--b-only",
        action="store_true",
        help="Also create candidates_B_only.csv (B True and A not True)",
    )
    return ap.parse_args()


def main():
    args = parse_args()
    input_csv = Path(args.input)
    output_dir = Path(args.outdir)
    
    if not input_csv.exists():
        print(f" Error: Input file not found: {input_csv}")
        return
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    clean_and_export(input_csv, output_dir, create_b_only=args.b_only)
    
    print("—" * 60)
    print(" Export complete!")


if __name__ == "__main__":
    main()

