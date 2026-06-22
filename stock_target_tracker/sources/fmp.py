"""
Financial Modeling Prep (FMP) analyst target price fetcher.

Uses the FMP "stable" API namespace to fetch analyst price-target data.
Requires FMP_API_KEY environment variable (free tier: 250 requests/day).

As of the FMP Aug-31-2025 endpoint migration, the legacy
``/api/v4/price-target`` (individual dated analyst targets) is deprecated.
On the free tier the dated per-analyst endpoint (``/stable/price-target-news``)
is paid-only, so this fetcher uses the two free-tier target endpoints instead:

- ``/stable/price-target-consensus`` — current consensus (high/low/median/consensus).
  Stored as a single undated consensus target (like Yahoo's consensus).
- ``/stable/price-target-summary`` — average target over the last month /
  quarter / year with analyst counts. Stored as dated targets dated to the
  period midpoint (so they can feed the 30/90/180/365-day accuracy engine).

Returns list of dicts with normalized target price data.
Failures return empty list (never raise to the caller).
"""

import time
import json
from datetime import date, timedelta

from utils import retry_with_backoff, rate_limit, get_env


# Track if FMP API key is invalid (401) to skip future attempts.
# 402/403 (paid/restricted/legacy endpoint) do NOT invalidate the key — other
# endpoints may still work — so those only skip the current symbol.
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

    # 1) Current consensus target (undated) — high/low/median/consensus.
    try:
        consensus = _fetch_consensus(symbol, api_key)
        results.extend(consensus)
    except _InvalidKey:
        _fmp_disabled = True
        print(f"    [fmp] FMP_API_KEY invalid — disabling FMP for this run.")
        return []
    except Exception as e:
        print(f"    [fmp] Consensus failed for {symbol}: {e}")

    # 2) Period-averaged targets (dated to the period midpoint).
    try:
        summary = _fetch_summary(symbol, api_key)
        results.extend(summary)
    except _InvalidKey:
        _fmp_disabled = True
        print(f"    [fmp] FMP_API_KEY invalid — disabling FMP for this run.")
        return []
    except Exception as e:
        print(f"    [fmp] Summary failed for {symbol}: {e}")

    if not results:
        print(f"    [fmp] No data returned for {symbol}")

    return results


class _InvalidKey(Exception):
    """Raised on HTTP 401 — the API key itself is invalid."""


def _get_json(url):
    """GET a JSON response from FMP with retry/backoff.

    Raises _InvalidKey on 401. Returns parsed JSON (list/dict/None) on success.
    Any other non-200 / parse failure is raised as a plain Exception (caller
    logs and continues — it does not invalidate the key).
    """
    import requests

    rate_limit('fmp', min_interval=0.3)

    def _try_fetch():
        resp = requests.get(url, timeout=15)
        if resp.status_code == 401:
            raise _InvalidKey("API key invalid (HTTP 401)")
        if resp.status_code == 429:
            raise Exception("HTTP 429 rate limited")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:120]}")
        return resp.json()

    return retry_with_backoff(
        _try_fetch, max_retries=2, base_delay=5.0,
        retry_on=(Exception,)
    )


def _fetch_consensus(symbol, api_key):
    """Fetch the current price-target consensus from FMP.

    Returns a list with a single undated consensus target dict (or []).
    """
    url = (f"https://financialmodelingprep.com/stable/price-target-consensus"
           f"?symbol={symbol}&apikey={api_key}")

    data = _get_json(url)
    if not isinstance(data, list) or not data:
        return []

    entry = data[0]
    target_price = entry.get('targetConsensus') or entry.get('targetMedian')
    if not target_price:
        return []

    return [{
        'source': 'fmp',
        'target_price': float(target_price),
        'rating': 'consensus',
        'analyst_name': None,
        'analyst_firm': f"FMP consensus (high {entry.get('targetHigh')}, "
                        f"low {entry.get('targetLow')}, median {entry.get('targetMedian')})",
        'date_posted': None,  # undated consensus — like Yahoo's consensus
        'raw_data': {'method': 'consensus', **entry},
    }]


# (window_label, json_field, days_ago_at_midpoint)
_SUMMARY_WINDOWS = [
    ('last month', 'lastMonthAvgPriceTarget', 15),
    ('last quarter', 'lastQuarterAvgPriceTarget', 45),
    ('last year', 'lastYearAvgPriceTarget', 180),
]


def _fetch_summary(symbol, api_key):
    """Fetch period-averaged price targets from FMP's summary endpoint.

    Returns dated target dicts for each window that has an average + count,
    dated to the window midpoint (so they can feed the checkpoint engine).
    """
    url = (f"https://financialmodelingprep.com/stable/price-target-summary"
           f"?symbol={symbol}&apikey={api_key}")

    data = _get_json(url)
    if not isinstance(data, list) or not data:
        return []

    entry = data[0]
    today = date.today()
    results = []

    for label, field, midpoint_days in _SUMMARY_WINDOWS:
        avg = entry.get(field)
        if not avg:
            continue
        # Count fields: lastMonthCount / lastQuarterCount / lastYearCount
        count = entry.get(field.replace('AvgPriceTarget', 'Count'))
        as_of = (today - timedelta(days=midpoint_days)).isoformat()
        results.append({
            'source': 'fmp',
            'target_price': float(avg),
            'rating': 'consensus',
            'analyst_name': None,
            'analyst_firm': f"FMP avg ({label}{f', {count} analysts' if count else ''})",
            'date_posted': as_of,
            'raw_data': {'method': 'summary', 'window': label, 'count': count, **entry},
        })

    return results