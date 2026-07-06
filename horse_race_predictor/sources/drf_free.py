"""
Daily Racing Form (DRF) free expert picks source.

Scrapes DRF's free per-card picks page for the requested race. DRF exposes a
public picks page per track/day; the exact URL pattern and HTML structure are
not officially stable and may need selector tuning after live testing. The
parser is isolated in _parse_picks_html() so it can be adjusted without
touching the HTTP plumbing.

Returns a list of normalized pick dicts ordered by the source's stated
preference (rank 1 = top pick, 2 = second, 3 = third). Failures return an empty
list (never raise to the caller), matching the graceful-degradation convention.
"""

import re

from utils import retry_with_backoff, rate_limit

# Candidate free-picks URL patterns. {track} is the Equibase track code lowercased,
# {date} rendered per format. These are best-effort; adjust if DRF reorganizes.
_URL_TEMPLATES = [
    "https://www.drf.com/horse-racing/tracks/{track}/entries-results",
    "https://www.drf.com/picks/{track}/{date}",
]
_DATE_FORMATS = ["%Y-%m-%d", "%Y%m%d"]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _candidate_urls(track_code, race_date):
    from datetime import datetime
    d = datetime.strptime(race_date, "%Y-%m-%d")
    track_l = track_code.lower()
    for tmpl in _URL_TEMPLATES:
        for fmt in _DATE_FORMATS:
            yield tmpl.format(track=track_l, date=d.strftime(fmt))


def fetch_picks(race):
    """Fetch DRF free picks for a race.

    Args:
        race: Race dataclass with track_code, race_number, race_date.

    Returns:
        List of normalized pick dicts:
        [{source, horse_name, program_number, rank, comment, raw_data}, ...]
        ordered by rank (1 = top pick). Empty list on any failure.
    """
    for url in _candidate_urls(race.track_code, race.race_date):
        try:
            html = _fetch_html(url)
        except Exception as e:
            print(f"    [drf_free] GET failed for {url}: {e}")
            continue
        if not html:
            continue
        picks = _parse_picks_html(html, race.race_number)
        if picks:
            for p in picks:
                p["source"] = "drf_free"
            return picks
    print(f"    [drf_free] No picks found for {race.track_code} R{race.race_number} "
          f"on {race.race_date} (page unavailable or layout changed)")
    return []


def _fetch_html(url):
    import requests
    rate_limit("drf_free", min_interval=1.0)

    def _get():
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return resp.text

    return retry_with_backoff(_get, max_retries=2, base_delay=1.0)


def _parse_picks_html(html, race_number):
    """Parse DRF free picks HTML for one race's top selections.

    Best-effort: locates the section for the requested race number and extracts
    the handicapper's ranked selections. Anchors on a "Race N" header and
    collects subsequent horse mentions until the next race header.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    if not re.search(rf"\bRace\s*{int(race_number)}\b", text):
        return []

    # Slice the text to the window for this race
    start_match = re.search(rf"\bRace\s*{int(race_number)}\b", text)
    start = start_match.start()
    next_race = re.search(rf"\bRace\s*{int(race_number) + 1}\b", text[start + 1:])
    end = start + 1 + next_race.start() if next_race else len(text)
    window = text[start:end]

    picks = []
    # A pick line commonly looks like: "<#1> Horse Name - comment" or "1 Horse Name"
    # Capture program number + horse name; rank by order of appearance.
    for m in re.finditer(r"(?:#?(\d{1,2}[A-Za-z]?)\s+)?([A-Z][A-Za-z0-9'\- ]{2,40})", window):
        prog = m.group(1) or ""
        name = m.group(2).strip().rstrip("--:.,")
        if not _looks_like_horse_name(name):
            continue
        # De-dup by horse name within this source's race window
        if any(normalize(name) == normalize(p["horse_name"]) for p in picks):
            continue
        picks.append({
            "source": "drf_free",
            "horse_name": name,
            "program_number": prog,
            "rank": len(picks) + 1 if len(picks) < 3 else None,
            "comment": "",
            "raw_data": name,
        })
        if len(picks) >= 3:
            break
    return picks


def _looks_like_horse_name(name):
    """Reject obvious non-name captures (common section headings, nav words)."""
    if not name or len(name) < 3:
        return False
    lowered = name.lower()
    blacklist = {"race", "post", "time", "mtp", "odds", "horse", "name",
                 "jockey", "trainer", "first", "second", "third", "win",
                 "place", "show", "scratch", "final", "results", "entries"}
    # multi-word names are almost always real; single-word must avoid blacklist
    tokens = lowered.split()
    if len(tokens) == 1 and tokens[0] in blacklist:
        return False
    if lowered in blacklist:
        return False
    return True


def normalize(name):
    """Lightweight name normalization for in-source de-dup."""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()