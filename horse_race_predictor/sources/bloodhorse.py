"""
BloodHorse results source via the parse.bot API.

parse.bot wraps BloodHorse.com's public race-results pages in a JSON REST API.
`get_race_results_list` returns the top-3 finishers for every race across a date
range in a given region - exactly what we need for win/place/show accuracy
scoring, and a single date-range query (paginated) covers the whole backtest
week across all US tracks, well inside the free tier (100 calls/month).

API spec (free tier):
  GET https://api.parse.bot/scraper/a86c6f40-0178-4067-bea8-c80c3648ea82/get_race_results_list
      ?page=1&region=region-america&start_date=MM/DD/YYYY&end_date=MM/DD/YYYY
  Header: X-API-Key: <PARSE_API_KEY>
  Response: {data:{page, races:[{title, subtitle, link, top_3:[name,name,name],
                               properties:[...]}]}, status}

The `link` field cleanly encodes track code + date + race number, e.g.:
  https://www.bloodhorse.com/horse-racing/race/usa/cd/2026/6/10/8
  -> country=usa, track=cd (Churchill Downs), 2026-06-10, race 8
so we parse identity from the link (robust) rather than the free-text title.

The API key is read from the PARSE_API_KEY env var (utils.get_env loads .env).
Rate limit: 5 req/min -> we throttle to one call every ~13s.
"""

import re
import urllib.parse

from utils import get_env, rate_limit, retry_with_backoff

API_BASE = "https://api.parse.bot/scraper/a86c6f40-0178-4067-bea8-c80c3648ea82"
ENDPOINT = f"{API_BASE}/get_race_results_list"

# BloodHorse lowercase track code (from the `link` URL) -> our Equibase-style code.
# Extend as needed. Unknown codes fall through to a name-based guess from the title.
BH_CODE_MAP = {
    "cd": "CD", "churchill": "CD", "churchill-downs": "CD",
    "gp": "GP", "gulfstream": "GP", "gulfstream-park": "GP",
    "sa": "SA", "santa-anita": "SA",
    "bel": "BEL", "belmont": "BEL", "belmont-park": "BEL", "belmont-at-big-a": "BEL",
    "sar": "SAR", "saratoga": "SAR", "saratoga-og": "SAR",
    "aqu": "AQU", "aqueduct": "AQU",
    "mth": "MTH", "monmouth": "MTH", "monmouth-park": "MTH",
    "kee": "KEE", "keeneland": "KEE",
    "dmr": "DMR", "del-mar": "DMR",
    "gg": "GG", "golden-gate-fields": "GG",
    "tam": "TAM", "tampa-bay-downs": "TAM",
    "pim": "PIM", "pimlico": "PIM",
    "lrl": "LRL", "laurel-park": "LRL",
    "prx": "PRX", "parx-racing": "PRX", "parx": "PRX",
    "fg": "FG", "fair-grounds": "FG",
    "op": "OP", "oaklawn-park": "OP", "oaklawn": "OP",
    "wo": "WO", "woodbine": "WO",
}

# Title/name fragment -> our code, used when the link code is unrecognized.
_NAME_FRAGMENTS = [
    ("churchill", "CD"), ("gulfstream", "GP"), ("santa anita", "SA"),
    ("belmont", "BEL"), ("saratoga", "SAR"),
]


def fetch_results_range(start_date, end_date, tracks=None, region="region-america",
                        max_pages=50):
    """Fetch all race results in [start_date, end_date] for a region.

    Args:
        start_date, end_date: 'YYYY-MM-DD' (ISO) - converted to MM/DD/YYYY for the API.
        tracks: optional set/collection of our uppercase track codes to keep
                (e.g. {'CD','GP','SA','BEL','SAR'}). If None, keep all.
        region: 'region-america' or 'region-intl'.
        max_pages: safety cap on pagination.

    Returns:
        list of result dicts: {track_code, race_number, race_date (YYYY-MM-DD),
        finishers: [{horse_name, finish_position}]} for the top-3 finishers of
        each race matching `tracks`. Empty list on failure.
    """
    api_key = get_env("PARSE_API_KEY")
    if not api_key:
        print("    [bloodhorse] No PARSE_API_KEY env var set - cannot fetch results.")
        return []

    tracks = {t.upper() for t in tracks} if tracks else None
    out = []
    for page in range(1, max_pages + 1):
        races = _fetch_page(api_key, start_date, end_date, region, page)
        if not races:
            break
        for race in races:
            parsed = _parse_race(race)
            if not parsed:
                continue
            if tracks and parsed["track_code"] not in tracks:
                continue
            out.append(parsed)
        # If fewer than a typical page returned, assume last page.
        if len(races) < 20:
            break
    return out


def _fetch_page(api_key, start_date, end_date, region, page):
    """Fetch one results page (list of race dicts) with retry + rate limiting."""
    params = {
        "page": str(page),
        "region": region,
        "start_date": _to_us_date(start_date),
        "end_date": _to_us_date(end_date),
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"

    def _get():
        import requests
        # Free tier: 5 req/min. Throttle to stay safely under.
        rate_limit("bloodhorse_api", min_interval=13.0)
        resp = requests.get(url, headers={"X-API-Key": api_key,
                                          "Accept": "application/json"},
                            timeout=30)
        if resp.status_code != 200:
            print(f"    [bloodhorse] page {page} HTTP {resp.status_code}: "
                  f"{resp.text[:200]}")
            return []
        data = resp.json()
        if data.get("status") != "success":
            print(f"    [bloodhorse] page {page} non-success: {data}")
            return []
        return (data.get("data") or {}).get("races") or []

    try:
        return retry_with_backoff(_get, max_retries=2, base_delay=2.0)
    except Exception as e:
        print(f"    [bloodhorse] page {page} failed: {e}")
        return []


def _parse_race(race):
    """Parse one BloodHorse race dict into a normalized result dict.

    Identity (track/date/race) comes from the `link` URL; finishers from `top_3`.
    """
    link = race.get("link") or ""
    identity = _parse_link(link)
    if not identity:
        # Fallback: try the subtitle/title for track + race number.
        identity = _parse_title(race.get("title") or "", race.get("subtitle") or "")
    if not identity:
        return None

    track_code, race_date, race_number = identity
    top3 = race.get("top_3") or []
    finishers = []
    for i, name in enumerate(top3[:3], 1):
        name = (name or "").strip()
        if not name:
            continue
        finishers.append({"horse_name": name, "finish_position": i})

    if not finishers:
        return None

    return {
        "track_code": track_code,
        "race_date": race_date,
        "race_number": race_number,
        "finishers": finishers,
        "raw": {"title": race.get("title"), "link": link},
    }


def _parse_link(link):
    """Extract (track_code, race_date YYYY-MM-DD, race_number) from a BloodHorse link.

    URL shape: https://www.bloodhorse.com/horse-racing/race/usa/cd/2026/6/10/8
    Returns None if the shape is unrecognized.
    """
    parts = [p for p in link.strip("/").split("/") if p]
    # Expect: [..., 'race', country, track, year, month, day, race_number]
    if "race" not in parts:
        return None
    try:
        idx = parts.index("race")
        # Find the 'race' that precedes country/track/date (the last occurrence
        # matching the pattern with >=5 trailing segments).
        for i in range(idx, len(parts)):
            seg = parts[i:i + 6]
            if len(seg) == 6 and seg[1].lower() in ("usa", "can", "usa-can"):
                continue
        # Take trailing 5 segments relative to the 'race' token at idx:
        track = parts[idx + 2] if idx + 2 < len(parts) else None
        year = parts[idx + 3] if idx + 3 < len(parts) else None
        month = parts[idx + 4] if idx + 4 < len(parts) else None
        day = parts[idx + 5] if idx + 5 < len(parts) else None
        race_no = parts[idx + 6] if idx + 6 < len(parts) else None
        if not (track and year and month and day and race_no):
            return None
        track_code = _resolve_track_code(track)
        if not track_code:
            return None
        race_date = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        return (track_code, race_date, int(race_no))
    except (ValueError, IndexError):
        return None


def _parse_title(title, subtitle):
    """Fallback: guess track + race number from 'Churchill Downs, Race 8, AOC'."""
    text = f"{title} {subtitle}".lower()
    track_code = None
    for frag, code in _NAME_FRAGMENTS:
        if frag in text:
            track_code = code
            break
    if not track_code:
        return None
    m = re.search(r"race\s*#?\s*(\d{1,2})", text)
    if not m:
        return None
    # Date is unknown via this path; caller matches on track + race only.
    return (track_code, None, int(m.group(1)))


def _resolve_track_code(raw):
    """Map a BloodHorse track token (lowercase code or slug) to our code."""
    if not raw:
        return None
    key = raw.lower().strip()
    if key in BH_CODE_MAP:
        return BH_CODE_MAP[key]
    return key.upper()  # unknown -> uppercase as-is; caller's track set filters


def _to_us_date(iso_date):
    """Convert YYYY-MM-DD -> MM/DD/YYYY for the API. Pass through if already US."""
    if not iso_date:
        return ""
    if "/" in iso_date:
        return iso_date
    y, m, d = iso_date.split("-")
    return f"{m}/{d}/{y}"


# ── Single-race interface (registry compat; less efficient - fetches the day) ──

def fetch_results(race):
    """Fetch results for a single race (registry interface).

    Fetches the race's full date range (just that one day) and filters to the
    matching race. Used by the `predictor results` command. The backtest runner
    uses fetch_results_range() directly to batch the whole week.
    """
    out = fetch_results_range(race.race_date, race.race_date, tracks={race.track_code})
    for r in out:
        if (r["track_code"] == race.track_code and r["race_number"] == race.race_number
                and r["race_date"] == race.race_date):
            return r["finishers"]
    return []