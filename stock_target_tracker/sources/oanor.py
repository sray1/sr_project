"""
oanor analyst target price fetcher.

Uses the oanor Analyst API (https://api.oanor.com/analyst-api), which serves
Nasdaq-sourced analyst data. Requires the OANOR_API_KEY environment variable,
sent via the ``x-oanor-key`` header.

Endpoints used:

- ``/v1/target``     — current low/mean/high consensus target + current price +
  implied upside. Stored as a single undated consensus target (like the Yahoo
  and FMP consensus).
- ``/v1/history``    — month-by-month price target + buy/hold/sell split.
  Stored as DATED targets (one per month), which feed the 30/90/180/365-day
  accuracy engine. This is the value-add over the consensus-only sources.
- ``/v1/consensus``  — buy/hold/sell counts + mean rating (supplementary; used
  to label the consensus target's rating).

The confirmed response schema (enveloped as ``{"success":..., "data": {...}}``):

- ``/v1/consensus`` → ``data.{mean_rating, analysts, buy, hold, sell, buy_pct}``
- ``/v1/target``    → ``data.{price_target_low, price_target_mean, price_target_high,
  current_price, implied_upside_pct}``
- ``/v1/history``   → ``data.history[]`` each ``{date:"MM/DD/YYYY", price_target,
  buy, hold, sell, consensus}``

Parsers prefer these confirmed fields but keep a few fallbacks for robustness.
Set ``OANOR_DEBUG=1`` to dump the raw JSON of each response to stderr.

Free tier; targets change slowly, so caching in the DB means few calls needed.
Failures return empty list (never raise to caller).
"""

import os
import json

from utils import rate_limit, get_env, retry_with_backoff


# Track if the oanor key is invalid (401) to skip future attempts this run.
_oanor_disabled = False


def fetch_targets(symbol):
    """Fetch analyst target prices from the oanor API.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').

    Returns:
        List of dicts: [{source, target_price, rating, analyst_name,
                         analyst_firm, date_posted, raw_data}, ...]
    """
    global _oanor_disabled

    if _oanor_disabled:
        return []

    api_key = get_env('OANOR_API_KEY')
    if not api_key:
        print("    [oanor] OANOR_API_KEY not set — skipping. "
              "Set env var or add OANOR_API_KEY to .env file.")
        _oanor_disabled = True
        return []

    debug = bool(get_env('OANOR_DEBUG'))
    results = []

    # 1) /v1/consensus — buy/hold/sell counts (used to label the consensus rating).
    consensus_counts = {}
    try:
        cdata = _get_json(symbol, api_key, 'consensus', debug)
        consensus_counts = _parse_consensus_counts(cdata)
    except _InvalidKey:
        _oanor_disabled = True
        print("    [oanor] OANOR_API_KEY invalid — disabling oanor for this run.")
        return []
    except Exception as e:
        print(f"    [oanor] Consensus failed for {symbol}: {e}")

    # 2) /v1/target — current consensus target (undated).
    try:
        tdata = _get_json(symbol, api_key, 'target', debug)
        tgt = _parse_target(tdata, consensus_counts)
        if tgt:
            results.append(tgt)
    except _InvalidKey:
        _oanor_disabled = True
        print("    [oanor] OANOR_API_KEY invalid — disabling oanor for this run.")
        return []
    except Exception as e:
        print(f"    [oanor] Target failed for {symbol}: {e}")

    # 3) /v1/history — month-by-month dated targets.
    try:
        hdata = _get_json(symbol, api_key, 'history', debug)
        results.extend(_parse_history(hdata))
    except _InvalidKey:
        _oanor_disabled = True
        print("    [oanor] OANOR_API_KEY invalid — disabling oanor for this run.")
        return []
    except Exception as e:
        print(f"    [oanor] History failed for {symbol}: {e}")

    if not results:
        print(f"    [oanor] No data returned for {symbol}")

    return results


class _InvalidKey(Exception):
    """Raised on HTTP 401 — the API key itself is invalid."""


def _get_json(symbol, api_key, endpoint, debug=False):
    """GET a JSON response from an oanor endpoint.

    Raises _InvalidKey on 401. Returns parsed JSON on success.
    """
    import requests

    rate_limit('oanor', min_interval=1.0)  # be conservative on free tier

    url = f"https://api.oanor.com/analyst-api/v1/{endpoint}?symbol={symbol}"

    def _try_fetch():
        resp = requests.get(url, headers={'x-oanor-key': api_key}, timeout=15)
        if resp.status_code == 401:
            raise _InvalidKey("API key invalid (HTTP 401)")
        if resp.status_code == 429:
            raise Exception("HTTP 429 rate limited")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:160]}")
        return resp.json()

    data = retry_with_backoff(
        _try_fetch, max_retries=2, base_delay=5.0,
        retry_on=(Exception,)
    )

    if debug:
        import sys
        print(f"    [oanor][debug] {endpoint} raw: "
              f"{json.dumps(data)[:600]}", file=sys.stderr)
    return data


def _first(d, *keys, default=None):
    """Return the first present, non-None value among ``keys`` in dict ``d``."""
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v not in (None, '', []):
            return v
    return default


def _unwrap(resp):
    """Extract the payload from the oanor envelope.

    Responses look like ``{"success": true, "data": {...}, "meta": {...}}``.
    Returns ``resp['data']`` if present, else ``resp`` itself.
    """
    if isinstance(resp, dict) and 'data' in resp:
        return resp['data']
    return resp


def _parse_consensus_counts(data):
    """Extract buy/hold/sell counts + total + mean rating from /v1/consensus."""
    entry = _unwrap(data)
    if isinstance(entry, list) and entry:
        entry = entry[0]
    if not isinstance(entry, dict):
        return {}

    buy = _first(entry, 'buy', 'buyCount', 'strongBuy', 'strong_buys')
    hold = _first(entry, 'hold', 'holdCount', 'holds')
    sell = _first(entry, 'sell', 'sellCount', 'strongSell', 'sells')
    total = _first(entry, 'analysts', 'total', 'numberOfAnalysts', 'count')
    rating = _first(entry, 'mean_rating', 'meanRating', 'rating', 'consensus')

    if total is None and any(v is not None for v in (buy, hold, sell)):
        nums = [v for v in (buy, hold, sell) if v is not None]
        total = sum(nums)

    return {
        'buy': buy, 'hold': hold, 'sell': sell,
        'total': total, 'rating': rating,
    }


def _rating_from_counts(counts):
    """Derive a Buy/Hold/Sell label from consensus counts."""
    if not counts:
        return 'consensus'
    rating = counts.get('rating')
    if isinstance(rating, str) and rating:
        return rating
    buy = counts.get('buy') or 0
    sell = counts.get('sell') or 0
    hold = counts.get('hold') or 0
    if buy > sell and buy >= hold:
        return 'Buy'
    if sell > buy and sell >= hold:
        return 'Sell'
    if buy or sell or hold:
        return 'Hold'
    return 'consensus'


def _parse_target(data, counts):
    """Parse /v1/target into a single undated consensus target dict."""
    entry = _unwrap(data)
    if isinstance(entry, list) and entry:
        entry = entry[0]
    if not isinstance(entry, dict):
        return None

    target_price = _first(entry, 'price_target_mean', 'targetMean', 'target_mean',
                         'mean', 'meanTarget', 'priceTarget', 'targetPrice')
    if not target_price:
        return None

    low = _first(entry, 'price_target_low', 'targetLow', 'target_low', 'low')
    high = _first(entry, 'price_target_high', 'targetHigh', 'target_high', 'high')
    current = _first(entry, 'current_price', 'currentPrice', 'price', 'lastPrice')
    upside = _first(entry, 'implied_upside_pct', 'impliedUpside', 'upside')

    firm = "oanor consensus (Nasdaq)"
    if low or high:
        firm = f"oanor consensus (low {low}, high {high})"

    return {
        'source': 'oanor',
        'target_price': float(target_price),
        'rating': _rating_from_counts(counts),
        'analyst_name': None,
        'analyst_firm': firm,
        'date_posted': None,  # undated consensus
        'raw_data': {'method': 'target', 'low': low, 'high': high,
                     'currentPrice': current, 'upside': upside,
                     'consensus_counts': counts},
    }


def _parse_history(data):
    """Parse /v1/history into dated monthly target dicts.

    The payload is ``data.history`` — a list of monthly entries each with a
    ``date`` (MM/DD/YYYY), ``price_target``, and buy/hold/sell counts. Each
    entry becomes one dated target.
    """
    payload = _unwrap(data)
    if not isinstance(payload, dict):
        return []
    history = payload.get('history')
    if not isinstance(history, list):
        return []

    results = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        target_price = _first(entry, 'price_target', 'priceTarget', 'targetPrice',
                             'mean', 'targetMean', 'meanTarget')
        if not target_price:
            continue

        date_str = _first(entry, 'date', 'month', 'period', 'asOf')
        date_posted = _normalize_month(date_str)
        if not date_posted:
            continue

        buy = _first(entry, 'buy', 'buyCount')
        hold = _first(entry, 'hold', 'holdCount')
        sell = _first(entry, 'sell', 'sellCount')
        consensus = _first(entry, 'consensus', 'mean_rating')

        results.append({
            'source': 'oanor',
            'target_price': float(target_price),
            'rating': consensus or _rating_from_counts({'buy': buy, 'hold': hold, 'sell': sell}),
            'analyst_name': None,
            'analyst_firm': f"oanor (Nasdaq consensus, {date_posted[:7]})",
            'date_posted': date_posted,
            'raw_data': {'method': 'history', 'date': date_str,
                         'buy': buy, 'hold': hold, 'sell': sell},
        })

    return results


def _normalize_month(value):
    """Normalize a month/date value to an ISO date string (YYYY-MM-DD).

    Accepts 'MM/DD/YYYY', 'YYYY-MM-DD', 'YYYY-MM', or 'YYYY-MM-DDTHH:...'.
    Returns None if it can't be parsed.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    # MM/DD/YYYY (oanor's history format)
    if '/' in s:
        parts = s.split('/')
        try:
            m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:  # 2-digit year
                y += 2000 if y < 70 else 1900
            if 1 <= m <= 12 and 1 <= d <= 31 and y >= 1900:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    # ISO-ish: YYYY-MM-DD or YYYY-MM
    s = s[:10]
    parts = s.split('-')
    try:
        y = int(parts[0])
        m = int(parts[1])
        if y < 1900 or not (1 <= m <= 12):
            return None
        d = int(parts[2]) if len(parts) >= 3 else 1
        return f"{y:04d}-{m:02d}-{d:02d}"
    except (ValueError, IndexError):
        return None


