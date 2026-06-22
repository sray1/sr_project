"""
Fetch current and historical stock prices using yfinance.

Provides functions to fetch the latest price, a specific date's price,
or a batch of prices for multiple symbols. Handles trading day vs calendar
day differences (weekends/holidays) by finding nearest prior trading day.
"""

import time
from datetime import datetime, timedelta

from utils import retry_with_backoff, rate_limit


def fetch_current_price(symbol):
    """Fetch the latest stock price for a symbol.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').

    Returns:
        Dict with price data: {symbol, price_date, open, close, high, low, volume}
        or None on failure.
    """
    import yfinance as yf

    rate_limit('yfinance_price', min_interval=0.5)

    def _try_fetch():
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist.empty:
            raise Exception(f"No price data returned for {symbol}")
        return hist

    try:
        hist = retry_with_backoff(_try_fetch, max_retries=2, base_delay=2.0,
                                   retry_on=(Exception,))
    except Exception as e:
        print(f"    Failed to fetch price for {symbol}: {e}")
        return None

    # Filter out rows with NaN close prices (non-trading days / pre-market)
    import pandas as pd
    hist = hist[hist['Close'].notna() & (hist['Close'] > 0)]

    if hist.empty:
        print(f"    No valid price data for {symbol}")
        return None

    # Get the most recent trading day
    last_row = hist.iloc[-1]
    last_date = hist.index[-1].strftime('%Y-%m-%d')

    return {
        'symbol': symbol,
        'price_date': last_date,
        'open': round(float(last_row['Open']), 2),
        'close': round(float(last_row['Close']), 2),
        'high': round(float(last_row['High']), 2),
        'low': round(float(last_row['Low']), 2),
        'volume': int(last_row['Volume']),
    }


def fetch_price_on_date(symbol, target_date, lookback_days=5):
    """Fetch the stock price on a specific date (or nearest prior trading day).

    Handles weekends and market holidays by looking back up to lookback_days.

    Args:
        symbol: Stock ticker symbol.
        target_date: Date string 'YYYY-MM-DD'.
        lookback_days: How many days to look back if target_date is a non-trading day.

    Returns:
        Dict with price data, or None on failure.
    """
    import yfinance as yf

    rate_limit('yfinance_price', min_interval=0.5)

    # Calculate date range for the lookback
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    start_dt = target_dt - timedelta(days=lookback_days)
    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = target_dt.strftime('%Y-%m-%d')

    def _try_fetch():
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=start_str, end=end_str)
        if hist.empty:
            raise Exception(f"No price data for {symbol} around {target_date}")
        return hist

    try:
        hist = retry_with_backoff(_try_fetch, max_retries=2, base_delay=2.0,
                                   retry_on=(Exception,))
    except Exception as e:
        print(f"    Failed to fetch price for {symbol} on {target_date}: {e}")
        return None

    # Get the last row (closest to target_date but not after)
    last_row = hist.iloc[-1]
    last_date = hist.index[-1].strftime('%Y-%m-%d')

    return {
        'symbol': symbol,
        'price_date': last_date,
        'open': round(float(last_row['Open']), 2),
        'close': round(float(last_row['Close']), 2),
        'high': round(float(last_row['High']), 2),
        'low': round(float(last_row['Low']), 2),
        'volume': int(last_row['Volume']),
    }


def fetch_current_prices(symbols):
    """Fetch current prices for a list of symbols.

    Args:
        symbols: List of ticker symbol strings.

    Returns:
        List of price dicts (None entries for failed fetches).
    """
    results = []
    for symbol in symbols:
        price = fetch_current_price(symbol)
        if price:
            results.append(price)
        else:
            results.append(None)
        # Small delay between symbols to avoid rate limiting
        time.sleep(0.3)
    return results


def fetch_price_history(symbol, start_date, end_date):
    """Fetch daily price history for a symbol over [start_date, end_date].

    Used to compute a stock's price range over a window (e.g. the 360 days
    after an analyst issued a target). end_date is inclusive (yfinance's end
    bound is exclusive, so it is bumped by one day internally).

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').
        start_date, end_date: 'YYYY-MM-DD' strings.

    Returns:
        List of dicts sorted ascending by date: [{price_date, low, high, close}, ...].
        Empty list on any failure (never raises to caller).
    """
    import yfinance as yf
    import pandas as pd

    try:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    except (ValueError, TypeError):
        return []
    if end_dt < start_dt:
        return []

    rate_limit('yfinance_price', min_interval=0.5)

    # yfinance's end is exclusive; bump by one day so end_date is included
    end_exclusive = (end_dt + timedelta(days=1)).strftime('%Y-%m-%d')

    def _try_fetch():
        ticker = yf.Ticker(symbol)
        return ticker.history(start=start_date, end=end_exclusive)

    try:
        hist = retry_with_backoff(_try_fetch, max_retries=1, base_delay=2.0,
                                  retry_on=(Exception,))
    except Exception:
        return []

    if hist is None or getattr(hist, 'empty', True):
        return []

    hist = hist[hist['Close'].notna() & (hist['Close'] > 0)]
    if hist.empty:
        return []

    rows = []
    for date, r in hist.iterrows():
        rows.append({
            'price_date': date.strftime('%Y-%m-%d'),
            'low': round(float(r['Low']), 2),
            'high': round(float(r['High']), 2),
            'close': round(float(r['Close']), 2),
        })
    rows.sort(key=lambda p: p['price_date'])
    return rows