"""
MarketBeat analyst target price fetcher.

Scrapes analyst ratings and target prices from MarketBeat.
MarketBeat is aggressive about bot detection, so this source may
frequently return empty results (graceful degradation).

Returns list of dicts with normalized target price data.
Failures return empty list (never raise to caller).
"""

import time
import re

from utils import retry_with_backoff, rate_limit


def fetch_targets(symbol):
    """Fetch analyst target prices from MarketBeat.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').

    Returns:
        List of dicts: [{source, target_price, rating, analyst_name,
                         analyst_firm, date_posted, raw_data}, ...]
    """
    results = []

    # Try common exchange prefixes; MarketBeat may redirect
    for exchange in ['', 'NASDAQ/', 'NYSE/']:
        try:
            scraped = _scrape_marketbeat(symbol, exchange)
            if scraped:
                results.extend(scraped)
                break  # Found data, no need to try other exchanges
        except Exception as e:
            print(f"    [marketbeat] Failed for {symbol} (exchange={exchange or 'default'}): {e}")

    if not results:
        print(f"    [marketbeat] No data returned for {symbol} (may be blocked or symbol not found)")

    return results


def _scrape_marketbeat(symbol, exchange_prefix=''):
    """Scrape MarketBeat price target page for a symbol.

    Args:
        symbol: Stock ticker symbol.
        exchange_prefix: Exchange path prefix (e.g., 'NASDAQ/', 'NYSE/', or '').

    Returns list of target price dicts, or empty list on failure.
    """
    import requests
    from bs4 import BeautifulSoup

    rate_limit('marketbeat', min_interval=2.5)

    url = f"https://www.marketbeat.com/stocks/{exchange_prefix}{symbol}/price-target/"
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'),
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    def _try_scrape():
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 403:
            # Cloudflare or bot detection — do not retry
            raise _MarketBeatBlocked(f"HTTP 403 (blocked)")
        if resp.status_code == 429:
            raise Exception("HTTP 429 rate limited")
        if resp.status_code == 404:
            raise _MarketBeatBlocked(f"HTTP 404 (not found)")
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        return resp

    try:
        response = retry_with_backoff(
            _try_scrape, max_retries=1, base_delay=30.0,
            retry_on=(Exception,),
        )
    except _MarketBeatBlocked:
        return []
    except Exception:
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    results = []

    # Find the analyst ratings table
    # MarketBeat uses various table structures; try flexible selectors
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        if len(rows) < 2:
            continue

        # Check header to see if this looks like an analyst ratings table
        header_cells = rows[0].find_all(['th', 'td'])
        header_text = ' '.join(cell.get_text(strip=True).lower() for cell in header_cells)

        if 'price' not in header_text or 'target' not in header_text:
            # Also try tables that mention 'rating' or 'analyst'
            if 'rating' not in header_text and 'analyst' not in header_text:
                continue

        for row in rows[1:]:
            try:
                cells = row.find_all('td')
                if len(cells) < 3:
                    continue

                entry = _parse_rating_row(cells)
                if entry:
                    results.append(entry)
            except (ValueError, IndexError):
                continue

    return results


def _clean_marketbeat_text(text):
    """Remove MarketBeat subscription promo junk from scraped text."""
    # Remove common MarketBeat subscription promo phrases
    promo_patterns = [
        r'Subscribe to MarketBeat All Access.*',
        r'\d+ of \d+ stars.*',
    ]
    for pattern in promo_patterns:
        text = re.sub(pattern, '', text).strip()
    return text


def _parse_rating_row(cells):
    """Parse a single row from a MarketBeat analyst ratings table.

    Tries to extract: firm, rating, target price, date.
    Returns dict or None if parsing fails.
    """
    cell_texts = [_clean_marketbeat_text(cell.get_text(strip=True)) for cell in cells]

    firm = None
    rating = None
    target_price = None
    date_posted = None

    for text in cell_texts:
        # Try to find a dollar amount (target price)
        price_match = re.search(r'\$?([\d,]+\.\d{2})', text)
        if price_match and target_price is None:
            try:
                target_price = float(price_match.group(1).replace(',', ''))
            except ValueError:
                pass

        # Try to find a date
        date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', text)
        if date_match and date_posted is None:
            # Convert MM/DD/YYYY to YYYY-MM-DD
            try:
                from datetime import datetime
                dt = datetime.strptime(date_match.group(1), '%m/%d/%Y')
                date_posted = dt.strftime('%Y-%m-%d')
            except ValueError:
                pass

        # Check for known rating keywords
        text_lower = text.lower()
        known_ratings = ['strong buy', 'buy', 'outperform', 'overweight', 'hold',
                         'neutral', 'equal weight', 'underperform', 'underweight',
                         'sell', 'strong sell', 'market perform', 'sector perform']
        for kr in known_ratings:
            if kr in text_lower and rating is None:
                rating = kr.title()
                break

    # First non-empty, non-price, non-date, non-rating text is likely the firm name
    for text in cell_texts:
        text_stripped = text.strip()
        if not text_stripped:
            continue
        # Skip if it's a price, date, or rating
        if re.search(r'\$[\d,]+\.\d{2}', text):
            continue
        if re.search(r'\d{1,2}/\d{1,2}/\d{4}', text):
            continue
        if _text_lower_matches_rating(text_stripped.lower()):
            continue
        firm = text_stripped
        break

    if target_price is None:
        return None  # No target price found — not useful

    return {
        'source': 'marketbeat',
        'target_price': target_price,
        'rating': rating,
        'analyst_name': None,
        'analyst_firm': firm,
        'date_posted': date_posted,
        'raw_data': {'method': 'scraping', 'cells': cell_texts},
    }


def _text_lower_matches_rating(text_lower):
    """Check if a lowercase string matches a known analyst rating."""
    known = ['strong buy', 'buy', 'outperform', 'overweight', 'hold',
             'neutral', 'equal weight', 'underperform', 'underweight',
             'sell', 'strong sell', 'market perform', 'sector perform']
    return any(kr in text_lower for kr in known)


class _MarketBeatBlocked(Exception):
    """Raised when MarketBeat blocks the request (403/404)."""
    pass