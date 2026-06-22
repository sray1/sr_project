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

        # Skip fundamentals / holdings / comparable-company tables. On ETF
        # pages (e.g. MSOS) MarketBeat shows a table of *other* companies with
        # their consensus targets (columns Company / Sector / Current Price /
        # Market Cap / ...), not analyst targets for this symbol. Parsing it
        # would store each holding's name as an "analyst firm".
        if any(kw in header_text for kw in (
                'sector', 'market cap', 'p/e', 'current price',
                'portfolio', 'weight', 'constituent', 'shares')):
            continue

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

    # Reject / clean junk firm names from malformed table rows (ETF holdings
    # lists, rating-action labels leaking into the firm column).
    cls = _classify_firm(firm)
    if cls == 'holdings_junk':
        return None  # not an analyst target — a scraped holdings/price cell
    if cls == 'action_word':
        firm = None  # keep the target, but the firm wasn't really parsed
    if firm:
        # Strip a glued-on "Not Rated" suffix (e.g. "Arete ResearchNot Rated").
        firm = re.sub(r'\s*Not Rated\s*$', '', firm).strip() or None

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


# ── Junk-firm detection ─────────────────────────────────────────────────────
# MarketBeat's page structure differs for ETFs (e.g. MSOS), where the scraper
# can latch onto a holdings / related-securities table instead of the analyst
# price-target table. That produces "firm" values that are really holding
# tickers glued to company names ("AAPLApple", "NVDANVIDIA", "QQQInvesco QQQ"),
# bare prices ("16.60"), or rating-action labels ("Downgrade"). These filters
# keep such junk out of the database.

# A bare price / number ("16.60", "1,234.50").
_PURE_NUMBER = re.compile(r'^[\d.,$]+$')

# A rating-action label leaked into the firm column (exact match, so legit
# firms like "JPMorgan" or "Holdings" are never caught).
_ACTION_WORD = re.compile(
    r'^(downgrad(e|ed|es)?|upgrades?|initiated(\s+coverage)?|'
    r'reiterated(\s+rating)?|resumed?|repeated?|maintained?|coverage|'
    r'target|rating|hold|buy|sell|neutral)$',
    re.I,
)

# A fund / ETF issuer or "ETF" token — these are holdings, not analyst firms.
# (Only unambiguous tokens are used; ticker+company glue like "AAPLApple" is
# prevented upstream by the holdings-table header check, not matched here,
# since such patterns can't be reliably told apart from real firms like
# "JPMorgan".)
_FUND_KEYWORD = re.compile(r'(iShares|Invesco|Vanguard|VanEck|WisdomTree|'
                          r'ProShares|SPDR|\bETF\b)', re.I)


def _classify_firm(firm):
    """Classify a scraped analyst_firm value.

    Returns one of:
      - 'good'          : a plausible firm name (or None) — keep as-is.
      - 'holdings_junk' : a scraped holdings/price cell — drop the whole row
                          (it is not an analyst target for this symbol).
      - 'action_word'   : a rating-action label — keep the target (its price
                          may be legitimate) but drop the firm attribution.
    """
    if not firm:
        return 'good'
    f = firm.strip()
    if _PURE_NUMBER.match(f):
        return 'holdings_junk'
    if _FUND_KEYWORD.search(f):
        return 'holdings_junk'
    if _ACTION_WORD.match(f):
        return 'action_word'
    return 'good'


class _MarketBeatBlocked(Exception):
    """Raised when MarketBeat blocks the request (403/404)."""
    pass