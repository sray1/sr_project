"""
Equibase entries source for Horse Race Predictor.

Fetches the horse list (entries) for a race from Equibase's free static entry
pages. Equibase is the most stable free target for entries/results since it is
the official data provider for US thoroughbred racing.

URL format: Equibase publishes per-card static entry pages of the form
    https://www.equibase.com/static/entry/{TRACK}{DATE}.html
The exact DATE token format has varied over time (YYYYMMDD vs MMDDYY), so this
fetcher tries each candidate URL and accepts the first that returns race
content. Selectors for the per-race horse rows are best-effort: Equibase's HTML
layout is not officially stable and may need tuning after live testing. The
parser is isolated in _parse_entries_html() so it can be adjusted without
touching the HTTP plumbing.

Failures return an empty list (never raise to the caller), matching the
graceful-degradation convention used across the project's source modules.
"""

import re

from utils import retry_with_backoff, rate_limit


# Candidate URL formats tried in order. {track} is the Equibase track code,
# {date} is rendered per-format from the race date.
_URL_TEMPLATES = [
    "https://www.equibase.com/static/entry/{track}{date}.html",  # date YYYYMMDD
]

# Date token formats to render into {date}, tried against _URL_TEMPLATES.
_DATE_FORMATS = ["%Y%m%d", "%m%d%y"]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _candidate_urls(track_code, race_date):
    """Yield candidate entry-page URLs for the track/date."""
    from datetime import datetime
    d = datetime.strptime(race_date, "%Y-%m-%d")
    for tmpl in _URL_TEMPLATES:
        for fmt in _DATE_FORMATS:
            yield tmpl.format(track=track_code, date=d.strftime(fmt))


def fetch_entries(race):
    """Fetch entries (horse list) for a race from Equibase.

    Args:
        race: Race dataclass with track_code, race_number, race_date.

    Returns:
        List of normalized entry dicts:
        [{program_number, horse_name, jockey, trainer, morning_line_odds,
          post_position, scratched}, ...]
        Empty list if the page can't be reached or the race isn't found.
    """
    for url in _candidate_urls(race.track_code, race.race_date):
        try:
            html = _fetch_html(url)
        except Exception as e:
            print(f"    [equibase] GET failed for {url}: {e}")
            continue
        if not html:
            continue
        entries = _parse_entries_html(html, race.race_number)
        if entries:
            return entries
        # Page loaded but race not found on it - try next URL format
    print(f"    [equibase] No entries found for {race.track_code} R{race.race_number} "
          f"on {race.race_date} (card may be unavailable or layout changed)")
    return []


def _fetch_html(url):
    """GET a URL with retries + rate limiting. Returns HTML text or ''."""
    import requests

    rate_limit("equibase", min_interval=1.0)

    def _get():
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return resp.text

    return retry_with_backoff(_get, max_retries=2, base_delay=1.0)


def _parse_entries_html(html, race_number):
    """Parse Equibase's entry page HTML for one race's horse list.

    Best-effort: locates the section for the requested race number, then
    extracts horse rows (program number, horse name, jockey, trainer,
    morning-line odds). Returns [] if the race section isn't found.

    The parser tolerates layout drift by anchoring on stable text cues
    ("Race N") and table structure rather than brittle class names.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ")

    # Confirm the card mentions the requested race
    race_header = re.search(rf"\bRace\s*{int(race_number)}\b", text)
    if not race_header:
        return []

    # Find the race's table region: Equibase renders each race as a table.
    # We pick the table whose visible text contains "Race N" and collect rows.
    target_table = None
    for table in soup.find_all("table"):
        if re.search(rf"\bRace\s*{int(race_number)}\b", table.get_text(" ")):
            target_table = table
            break

    rows = []
    if target_table:
        rows = _extract_rows_from_table(target_table)

    # Fallback: scan all tables and take rows that look like entry rows up to
    # the next race header. Kept simple - primary path above usually suffices.
    if not rows:
        rows = _extract_rows_loose(soup, race_number)

    return rows


def _extract_rows_from_table(table):
    """Extract entry dicts from a single race <table>."""
    entries = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        entry = _row_to_entry(cells)
        if entry:
            entries.append(entry)
    return entries


def _extract_rows_loose(soup, race_number):
    """Fallback row extractor scanning all tables for entry-shaped rows."""
    # Stop at the next race header after ours
    next_race = race_number + 1
    collecting = False
    out = []
    for table in soup.find_all("table"):
        txt = table.get_text(" ")
        if re.search(rf"\bRace\s*{int(race_number)}\b", txt):
            collecting = True
            out.extend(_extract_rows_from_table(table))
            continue
        if collecting and re.search(rf"\bRace\s*{int(next_race)}\b", txt):
            break
        if collecting:
            out.extend(_extract_rows_from_table(table))
    return out


def _row_to_entry(cells):
    """Heuristically convert a table row's cell texts to an entry dict.

    An entry row typically starts with a program number (1..99 or like "1A"),
    followed by the horse name, then jockey/trainer, with morning-line odds
    as a token like "5-2" or "12-1" near the end. Non-matching rows return None.
    """
    if len(cells) < 3:
        return None
    prog = cells[0].strip()
    if not re.match(r"^\d{1,2}[A-Za-z]?$", prog):
        return None

    # Find the morning-line odds token (e.g. "5-2", "12-1", "3-1")
    mlo = None
    for c in cells:
        m = re.search(r"\b(\d{1,2})-(\d{1,2})$", c)
        if m:
            num, den = int(m.group(1)), int(m.group(2))
            if den > 0:
                mlo = round(num / den, 2)
            break

    # Horse name is usually the second cell; jockey/trainer follow.
    horse_name = cells[1].strip() if len(cells) > 1 else ""
    jockey = ""
    trainer = ""
    if len(cells) > 2:
        jockey = cells[2].strip()
    if len(cells) > 3:
        trainer = cells[3].strip()

    if not horse_name:
        return None

    post = None
    m = re.match(r"^(\d{1,2})", prog)
    if m:
        post = int(m.group(1))

    return {
        "program_number": prog,
        "horse_name": horse_name,
        "jockey": jockey,
        "trainer": trainer,
        "morning_line_odds": mlo,
        "post_position": post,
        "scratched": False,
    }