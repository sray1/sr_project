"""
Yahoo Finance analyst target price fetcher.

Primary: Uses yfinance library for analyst_price_targets + recommendations.
Fallback: Scrapes finance.yahoo.com/quote/{symbol}/analysis/ via requests + BeautifulSoup.

Returns list of dicts with normalized target price data.
Failures return empty list (never raise to caller).
"""

import time
import json

from utils import retry_with_backoff, rate_limit


def fetch_targets(symbol):
    """Fetch analyst target prices from Yahoo Finance.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').

    Returns:
        List of dicts: [{source, target_price, rating, analyst_name,
                         analyst_firm, date_posted, raw_data}, ...]
    """
    results = []

    # Primary: yfinance library
    try:
        yf_results = _fetch_via_yfinance(symbol)
        if yf_results:
            results.extend(yf_results)
            return results
    except Exception as e:
        print(f"    [yahoo_finance] yfinance lib failed for {symbol}: {e}")

    # Fallback: web scraping
    try:
        scrape_results = _fetch_via_scraping(symbol)
        if scrape_results:
            results.extend(scrape_results)
    except Exception as e:
        print(f"    [yahoo_finance] scraping fallback failed for {symbol}: {e}")

    if not results:
        print(f"    [yahoo_finance] No data returned for {symbol}")

    return results


def _fetch_via_yfinance(symbol):
    """Fetch target prices using the yfinance library.

    Returns list of target price dicts, or empty list on failure.
    """
    import yfinance as yf

    rate_limit('yahoo_finance', min_interval=0.5)

    def _try_fetch():
        ticker = yf.Ticker(symbol)
        return ticker.analyst_price_targets

    try:
        targets_data = retry_with_backoff(
            _try_fetch, max_retries=2, base_delay=2.0,
            retry_on=(Exception,)
        )
    except Exception:
        return []

    if not targets_data:
        return []

    results = []

    # yfinance returns a dict with keys like 'current', 'low', 'mean', 'high'
    if isinstance(targets_data, dict):
        # Consensus target prices
        mean_target = targets_data.get('mean')
        if mean_target:
            results.append({
                'source': 'yahoo_finance',
                'target_price': float(mean_target),
                'rating': 'consensus_mean',
                'analyst_name': None,
                'analyst_firm': 'Yahoo Finance Consensus',
                'date_posted': None,
                'raw_data': targets_data,
            })

        high_target = targets_data.get('high')
        if high_target and high_target != mean_target:
            results.append({
                'source': 'yahoo_finance',
                'target_price': float(high_target),
                'rating': 'consensus_high',
                'analyst_name': None,
                'analyst_firm': 'Yahoo Finance Consensus',
                'date_posted': None,
                'raw_data': {'type': 'high', 'value': high_target},
            })

        low_target = targets_data.get('low')
        if low_target and low_target != mean_target:
            results.append({
                'source': 'yahoo_finance',
                'target_price': float(low_target),
                'rating': 'consensus_low',
                'analyst_name': None,
                'analyst_firm': 'Yahoo Finance Consensus',
                'date_posted': None,
                'raw_data': {'type': 'low', 'value': low_target},
            })

    return results


def _fetch_via_scraping(symbol):
    """Fetch target prices by scraping Yahoo Finance analysis page.

    Fallback when yfinance lib returns empty data.

    Returns list of target price dicts, or empty list on failure.
    """
    import requests
    from bs4 import BeautifulSoup

    rate_limit('yahoo_finance_scrape', min_interval=1.0)

    url = f"https://finance.yahoo.com/quote/{symbol}/analysis/"
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'),
        'Accept': 'text/html,application/xhtml+xml',
    }

    def _try_scrape():
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 429:
            raise Exception(f"HTTP 429 rate limited")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        return resp

    try:
        response = retry_with_backoff(
            _try_scrape, max_retries=2, base_delay=5.0,
            retry_on=(Exception,)
        )
    except Exception:
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    results = []

    # Try to find the analyst price targets section
    # Yahoo Finance structure changes frequently; try multiple selectors
    target_section = (
        soup.find('div', {'data-testid': 'analyst-price-targets'})
        or soup.find('section', {'data-testid': 'analyst-price-targets'})
        or soup.find('div', string=lambda s: s and 'Price Target' in str(s))
    )

    if not target_section:
        return []

    # Try to extract target prices from tables or data attributes
    tables = target_section.find_all('table') if target_section else []
    for table in tables:
        rows = table.find_all('tr')
        for row in rows[1:]:  # skip header
            try:
                cells = row.find_all('td')
                if len(cells) >= 2:
                    firm = cells[0].get_text(strip=True)
                    target_text = cells[-1].get_text(strip=True).replace('$', '').replace(',', '')
                    target_price = float(target_text)
                    results.append({
                        'source': 'yahoo_finance',
                        'target_price': target_price,
                        'rating': None,
                        'analyst_name': None,
                        'analyst_firm': firm,
                        'date_posted': None,
                        'raw_data': {'method': 'scraping'},
                    })
            except (ValueError, IndexError):
                continue

    return results