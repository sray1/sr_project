"""
Fetch current and historical stock prices using yfinance.

Provides functions to fetch the latest price, a specific date's price,
or a batch of prices for multiple symbols. Handles trading day vs calendar
day differences (weekends/holidays) by finding nearest prior trading day.

The *_batch functions fetch all symbols in a single yfinance request
(yf.download) instead of one request per symbol, which removes the
per-symbol rate-limit floor from multi-symbol runs.
"""

from datetime import datetime, timedelta

from utils import retry_with_backoff, rate_limit


def _today_str():
    return datetime.now().strftime('%Y-%m-%d')


def _price_dict_from_row(symbol, date, row):
    """Build the standard price dict from one pandas history row."""
    return {
        'symbol': symbol,
        'price_date': date.strftime('%Y-%m-%d'),
        'open': round(float(row['Open']), 2),
        'close': round(float(row['Close']), 2),
        'high': round(float(row['High']), 2),
        'low': round(float(row['Low']), 2),
        'volume': int(row['Volume']),
    }


def _history_rows_from_df(df):
    """Extract history rows from a per-symbol OHLCV DataFrame.

    Filters out rows with a NaN/zero Close (non-trading days), and returns
    [{price_date, open, high, low, close, volume}, ...] sorted ascending.
    """
    import pandas as pd

    df = df[df['Close'].notna() & (df['Close'] > 0)]
    if df.empty:
        return []
    rows = []
    for date, r in df.iterrows():
        rows.append({
            'price_date': date.strftime('%Y-%m-%d'),
            'open': round(float(r['Open']), 2),
            'high': round(float(r['High']), 2),
            'low': round(float(r['Low']), 2),
            'close': round(float(r['Close']), 2),
            'volume': int(r['Volume']),
        })
    rows.sort(key=lambda p: p['price_date'])
    return rows


def _download_histories(symbols, start_date):
    """One yf.download for all symbols over [start_date, today].

    Returns {symbol: [history rows]} (same row format as
    fetch_price_history). Symbols with no data are absent from the dict.
    Raises on download failure so callers can fall back per-symbol.
    """
    import yfinance as yf
    import pandas as pd

    end_exclusive = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    data = yf.download(tickers=list(symbols), start=start_date, end=end_exclusive,
                       group_by='ticker', threads=True, progress=False)
    if data is None or data.empty:
        return {}

    results = {}
    for symbol in symbols:
        # group_by='ticker' gives (ticker, field) columns for multiple
        # tickers; a single-ticker download returns plain field columns.
        # A ticker yfinance couldn't resolve can be absent from the index
        # entirely — skip it (the per-symbol fallback covers it) instead of
        # letting the KeyError kill the whole batch.
        if isinstance(data.columns, pd.MultiIndex):
            if symbol not in data.columns.get_level_values(0):
                continue
            sub = data[symbol]
        else:
            sub = data
        try:
            rows = _history_rows_from_df(sub)
        except Exception:
            rows = []
        if rows:
            results[symbol] = rows
    return results


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
    hist = hist[hist['Close'].notna() & (hist['Close'] > 0)]

    if hist.empty:
        print(f"    No valid price data for {symbol}")
        return None

    # Get the most recent trading day
    last_row = hist.iloc[-1]
    last_date = hist.index[-1]

    return _price_dict_from_row(symbol, last_date, last_row)


def fetch_current_prices_batch(symbols):
    """Fetch current prices for many symbols in ONE yfinance request.

    Args:
        symbols: List of ticker symbol strings.

    Returns:
        Dict {symbol: price_dict} (same shape as fetch_current_price) for
        each symbol that returned data. Symbols missing from the batch
        result fall back to an individual fetch, so a delisted or
        unrecognized ticker doesn't take the whole batch down.
    """
    if not symbols:
        return {}
    if len(symbols) == 1:
        price = fetch_current_price(symbols[0])
        return {symbols[0]: price} if price else {}

    rate_limit('yfinance_price', min_interval=0.5)
    try:
        # 5 calendar days back always covers the latest trading day.
        batch = _download_histories(symbols, (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d'))
    except Exception as e:
        print(f"    Batch price download failed ({e}); falling back to per-symbol fetches")
        batch = {}

    results = {}
    for symbol in symbols:
        rows = batch.get(symbol)
        if rows:
            last = rows[-1]
            results[symbol] = {
                'symbol': symbol,
                'price_date': last['price_date'],
                'open': last['open'],
                'close': last['close'],
                'high': last['high'],
                'low': last['low'],
                'volume': last['volume'],
            }
        else:
            price = fetch_current_price(symbol)
            if price:
                results[symbol] = price
    return results


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
    last_date = hist.index[-1]

    return _price_dict_from_row(symbol, last_date, last_row)


def fetch_price_history(symbol, start_date, end_date):
    """Fetch daily price history for a symbol over [start_date, end_date].

    Used to compute a stock's price range over a window (e.g. the 360 days
    after an analyst issued a target). end_date is inclusive (yfinance's end
    bound is exclusive, so it is bumped by one day internally).

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').
        start_date, end_date: 'YYYY-MM-DD' strings.

    Returns:
        List of dicts sorted ascending by date: [{price_date, open, high,
        low, close, volume}, ...]. Empty list on any failure (never raises
        to caller).
    """
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
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        return ticker.history(start=start_date, end=end_exclusive)

    try:
        hist = retry_with_backoff(_try_fetch, max_retries=1, base_delay=2.0,
                                  retry_on=(Exception,))
    except Exception:
        return []

    if hist is None or getattr(hist, 'empty', True):
        return []

    return _history_rows_from_df(hist)


def fetch_price_histories(requests):
    """Fetch daily price histories for many symbols in ONE yfinance request.

    Args:
        requests: Dict {symbol: start_date}; every history runs from its
                  start_date to today. All symbols share one download
                  (fetched from the earliest start_date), which is one
                  network call instead of one per symbol.

    Returns:
        Dict {symbol: [history rows]} where rows match fetch_price_history's
        format. Symbols whose data is missing from the batch fall back to an
        individual fetch; symbols that fail entirely are absent (or map to
        an empty list, never raising).
    """
    if not requests:
        return {}
    if len(requests) == 1:
        symbol, start_date = next(iter(requests.items()))
        return {symbol: fetch_price_history(symbol, start_date, _today_str())}

    rate_limit('yfinance_price', min_interval=0.5)
    try:
        results = _download_histories(list(requests), min(requests.values()))
    except Exception as e:
        print(f"    Batch history download failed ({e}); "
              f"falling back to per-symbol fetches")
        results = {}

    for symbol, start_date in requests.items():
        if symbol not in results:
            rows = fetch_price_history(symbol, start_date, _today_str())
            if rows:
                results[symbol] = rows
    return results