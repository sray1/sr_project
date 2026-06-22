"""
Financial Modeling Prep (FMP) analyst target price fetcher.

Uses the FMP API to fetch analyst price targets and recommendations.
Requires FMP_API_KEY environment variable (free tier: 250 requests/day).

Returns list of dicts with normalized target price data.
Failures return empty list (never raise to caller).
"""

import time
import json

from utils import retry_with_backoff, rate_limit, get_env


# Track if FMP API key is invalid to skip future attempts
_fmp_disabled = False


def fetch_targets(symbol):
    """Fetch analyst target prices from Financial Modeling Prep API.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').

    Returns:
        List of dicts: [{source, target_price, rating, analyst_name,
                         analyst_firm, date_posted, raw_data}, ...]
    """
    global _fmp_disabled

    if _fmp_disabled:
        return []

    api_key = get_env('FMP_API_KEY')
    if not api_key:
        print(f"    [fmp] FMP_API_KEY not set — skipping. Set env var or add to .env file.")
        _fmp_disabled = True
        return []

    results = []

    # Fetch price targets
    try:
        price_targets = _fetch_price_targets(symbol, api_key)
        results.extend(price_targets)
    except Exception as e:
        print(f"    [fmp] Price targets failed for {symbol}: {e}")

    # Fetch analyst recommendations as supplementary data
    try:
        recommendations = _fetch_recommendations(symbol, api_key)
        # Only add recommendations that have price targets not already captured
        existing_firms = {r.get('analyst_firm', '') for r in results}
        for rec in recommendations:
            if rec.get('analyst_firm', '') not in existing_firms and rec.get('target_price'):
                results.append(rec)
    except Exception as e:
        print(f"    [fmp] Recommendations failed for {symbol}: {e}")

    if not results:
        print(f"    [fmp] No data returned for {symbol}")

    return results


def _fetch_price_targets(symbol, api_key):
    """Fetch analyst price targets from FMP API.

    Returns list of target price dicts.
    """
    import requests

    rate_limit('fmp', min_interval=0.3)

    url = f"https://financialmodelingprep.com/api/v4/price-target?symbol={symbol}&apikey={api_key}"

    def _try_fetch():
        resp = requests.get(url, timeout=15)
        if resp.status_code == 401 or resp.status_code == 403:
            global _fmp_disabled
            _fmp_disabled = True
            raise Exception(f"API key invalid (HTTP {resp.status_code})")
        if resp.status_code == 429:
            raise Exception("HTTP 429 rate limited")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        return resp.json()

    try:
        data = retry_with_backoff(
            _try_fetch, max_retries=2, base_delay=5.0,
            retry_on=(Exception,)
        )
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    results = []
    for entry in data:
        try:
            target_price = entry.get('targetPrice') or entry.get('target_price')
            if not target_price:
                continue

            results.append({
                'source': 'fmp',
                'target_price': float(target_price),
                'rating': entry.get('rating') or entry.get('ratingCategory'),
                'analyst_name': entry.get('analystName') or entry.get('analyst_name'),
                'analyst_firm': entry.get('analystCompany') or entry.get('analyst_firm') or entry.get('gradingCompany'),
                'date_posted': entry.get('publishedDate') or entry.get('date_posted') or entry.get('newsDate'),
                'raw_data': entry,
            })
        except (ValueError, TypeError):
            continue

    return results


def _fetch_recommendations(symbol, api_key):
    """Fetch analyst recommendations from FMP API (supplementary data).

    Returns list of recommendation dicts (only those with price targets).
    """
    import requests

    rate_limit('fmp', min_interval=0.3)

    url = f"https://financialmodelingprep.com/api/v3/analyst-estimates/{symbol}?apikey={api_key}"

    def _try_fetch():
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        return resp.json()

    try:
        data = retry_with_backoff(
            _try_fetch, max_retries=1, base_delay=3.0,
            retry_on=(Exception,)
        )
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    results = []
    for entry in data:
        try:
            target_price = entry.get('targetPrice') or entry.get('target_price')
            if not target_price:
                continue

            results.append({
                'source': 'fmp',
                'target_price': float(target_price),
                'rating': entry.get('rating') or entry.get('ratingStrongBuy'),
                'analyst_name': None,
                'analyst_firm': entry.get('analystCompany') or entry.get('gradingCompany'),
                'date_posted': entry.get('publishedDate') or entry.get('date'),
                'raw_data': {'method': 'estimates', **entry},
            })
        except (ValueError, TypeError):
            continue

    return results