"""
NFL projection sources: fetcher registry + name matching + fallbacks.

Mirrors the horse_race_predictor registry pattern: each projection source is a
best-effort fetcher behind a registry; failures degrade gracefully to the next
source, and every player always ends up with a projection (salary-implied
fallback as last resort, clearly labeled).

Priority order per player:
1. Manual CSV override (highest priority) - name + projected DK points
2. Free-site scrape (DailyFantasyFuel, BlueCollarDFS, numberFire, FantasyPros)
   - best-effort, often bot-walled; DFF is the one verified-working source
3. Salary-implied baseline per position (crude, labeled as 'fallback')
"""

import csv
import math
import re

import requests

# ---------------------------------------------------------------------------
# Name normalization & matching
# ---------------------------------------------------------------------------

SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}
PUNCT_RE = re.compile(r"[.''-]")


def normalize_name(name):
    """Normalize a player name for matching across sources.

    Lowercase, strip punctuation and generational suffixes, collapse spaces.
    'Patrick Mahomes II' -> 'patrick mahomes'
    """
    if not name:
        return ''
    name = name.lower().strip()
    name = PUNCT_RE.sub(' ', name)
    parts = [p for p in name.split() if p and p not in SUFFIXES]
    return ' '.join(parts)


# DST name variants: DK lists defenses as e.g. "Patriots DST"
def normalize_dst_name(name):
    """Normalize a DST entry to a team token: 'New England Patriots DST' -> 'patriots'."""
    if not name:
        return ''
    name = name.lower().replace(' dst', '').replace('defense', '').strip()
    return name.split()[-1] if name else ''


def _match_key(name, team=None):
    """Build a lookup key: (normalized full name, or team + last name)."""
    norm = normalize_name(name)
    if team:
        return f"{norm}|{team.lower()}"
    return norm


# ---------------------------------------------------------------------------
# Source 1: Manual CSV
# ---------------------------------------------------------------------------

def load_csv_projections(csv_path):
    """Load projections from a manual CSV file.

    Expected columns (case-insensitive):
        name | player          - player name
        points | proj | projection | dk_points | fp - projected DK points

    Optional:
        position (QB/RB/WR/TE/DST), team

    Returns:
        Dict {normalized_name: float} (plus team+lastname keys when team given)
    """
    projections = {}
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = {fn.lower().strip(): fn for fn in (reader.fieldnames or [])}

        name_col = next((fieldnames[c] for c in ('name', 'player', 'player_name')
                         if c in fieldnames), None)
        pts_col = next((fieldnames[c] for c in
                        ('dk_points', 'draftkings_projection', 'dk_proj',
                         'points', 'proj', 'projection', 'fp')
                        if c in fieldnames), None)
        team_col = fieldnames.get('team')

        if not name_col or not pts_col:
            raise ValueError(
                f"CSV {csv_path} must have a name/player column and a "
                f"points/proj column (found: {list(fieldnames.values())})")

        for row in reader:
            name = (row[name_col] or '').strip()
            pts = (row[pts_col] or '').strip()
            if not name or not pts:
                continue
            try:
                pts = float(pts)
            except ValueError:
                continue
            if math.isnan(pts):
                continue

            key = normalize_name(name)
            if key:
                projections[key] = pts
                team = (row[team_col] or '').strip() if team_col else None
                if team:
                    projections[f"{key}|{team.lower()}"] = pts

    return projections


# ---------------------------------------------------------------------------
# Source 2: Free-site scrapes (best-effort)
# ---------------------------------------------------------------------------

_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/128.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml',
}


def _fetch_html(url, timeout=15):
    """Fetch a URL, returning HTML text or None on any failure."""
    try:
        response = requests.get(url, headers=_HEADERS, timeout=timeout)
        if response.status_code == 200:
            return response.text
        print(f"    {url} returned HTTP {response.status_code}")
    except requests.RequestException as e:
        print(f"    {url} failed: {e}")
    return None


def scrape_numberfire(week=None):
    """Scrape numberFire NFL weekly projections (best-effort).

    numberFire publishes DK-point projections for the current week at
    https://www.numberfire.com/nfl/fantasy/fantasy-football-projections

    Returns:
        Dict {normalized_name: projected DK points}, or {} on failure.
    """
    from bs4 import BeautifulSoup

    url = "https://www.numberfire.com/nfl/fantasy/fantasy-football-projections"
    if week:
        url += f"?week={week}"

    print("  Trying numberFire...")
    html = _fetch_html(url)
    if not html:
        return {}

    soup = BeautifulSoup(html, 'lxml')
    projections = {}

    # Tables carry player links and numeric projection cells. Strategy:
    # find table rows whose first cell links to a player page, then take the
    # last numeric cell in the row (numberFire projects table: FP column).
    for row in soup.select('tr'):
        link = row.select_one('td a[href*="/nfl/players/"]')
        if not link:
            continue
        name = link.get_text(strip=True)
        if not name:
            continue

        numbers = []
        for cell in row.select('td')[1:]:
            text = cell.get_text(strip=True).replace(',', '')
            try:
                numbers.append(float(text))
            except ValueError:
                continue

        if numbers:
            key = normalize_name(name)
            if key:
                projections[key] = numbers[-1]

    if not projections:
        print("    numberFire: no projections parsed (page layout changed or JS-rendered)")
    else:
        print(f"    numberFire: {len(projections)} projections")
    return projections


def scrape_fantasypros(week=None):
    """Scrape FantasyPros NFL weekly projections (best-effort).

    FantasyPros posts per-position projection tables at
    https://www.fantasypros.com/nfl/projections/{pos}.php

    Verified limits (2026-09): the static page serves only the first ~10 rows
    per position, and JS rendering (Playwright) is Cloudflare-blocked, so this
    yields ~50 top-name projections. Names carry trailing team abbreviations
    ("Jalen Hurts PHI") that are stripped here.

    Returns:
        Dict {normalized_name: projected points}, or {} on failure.
    """
    from bs4 import BeautifulSoup

    print("  Trying FantasyPros...")
    projections = {}

    for pos in ('qb', 'rb', 'wr', 'te', 'dst'):
        url = f"https://www.fantasypros.com/nfl/projections/{pos}.php"
        if week:
            url += f"?week={week}"
        html = _fetch_html(url)
        if not html:
            continue

        soup = BeautifulSoup(html, 'lxml')
        table = soup.select_one('table')
        if not table:
            continue

        for row in table.select('tr')[1:]:
            cells = row.select('td')
            if not cells:
                continue

            # First cell holds the player name (inside a link), often with a
            # trailing team abbreviation: "Jalen Hurts PHI"
            name_cell = cells[0]
            name = name_cell.get_text(' ', strip=True)
            name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
            name = re.sub(r'\s+[A-Z]{2,4}$', '', name.strip()).strip()
            if not name:
                continue

            # FantasyPros projection is typically the last numeric column
            numbers = []
            for cell in cells[1:]:
                text = cell.get_text(strip=True).replace(',', '')
                try:
                    numbers.append(float(text))
                except ValueError:
                    continue

            if numbers:
                if pos == 'dst':
                    key = normalize_dst_name(name)
                else:
                    key = normalize_name(name)
                if key:
                    projections[key] = numbers[-1]

    if not projections:
        print("    FantasyPros: no projections parsed (likely bot-walled)")
    else:
        print(f"    FantasyPros: {len(projections)} projections")
    return projections


# ---------------------------------------------------------------------------
# Source 2b: Daily Fantasy Fuel (verified working, 2026-09)
# ---------------------------------------------------------------------------

DFF_URL = "https://www.dailyfantasyfuel.com/nfl/projections/"

# Trailing injury-report tokens in player names ("Ja'Marr Chase Q")
INJURY_TAGS = {'Q', 'D', 'O', 'IR', 'OUT', 'NA', 'P', 'SUSP'}
DFF_SALARY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*k", re.IGNORECASE)


def _strip_injury_tag(name):
    """Strip trailing injury-report tokens: 'Ja'Marr Chase Q' -> 'Ja'Marr Chase'.

    Only known tags are stripped (never name parts like 'III', which
    normalize_name handles separately).
    """
    parts = name.split()
    while parts and parts[-1].upper() in INJURY_TAGS:
        parts.pop()
    return ' '.join(parts)


def _parse_dff_projections(html):
    """Parse the Daily Fantasy Fuel projections table into projection keys.

    Verified layout (2026-09): the page is server-rendered with one <table>;
    each player <tr> holds a mobile card (first <td>, hidden on desktop)
    followed by desktop cells. Column names come from the detailed thead row
    (POS/NAME/SALARY/TEAM/OPP/.../'DK FP PROJECTED'), so column order changes
    are tolerated. The current-slate page is fetched; DFF has no week param.

    Returns:
        Dict {normalized_name: pts} plus {normalized_name|team: pts} keys,
        or {} if the layout is unrecognized.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'lxml')
    table = soup.find('table')
    if table is None:
        return {}

    # Column map from the detailed header row (the one with POS and NAME)
    colmap = {}
    for tr in table.find_all('tr'):
        headers = [th.get_text(' ', strip=True).upper()
                   for th in tr.find_all('th', recursive=False)]
        if 'POS' in headers and 'NAME' in headers:
            colmap = {h: i for i, h in enumerate(headers)}
            break
    if not colmap:
        return {}

    projections = {}
    tbody = table.find('tbody') or table
    for row in tbody.find_all('tr', recursive=False):
        tds = row.find_all('td', recursive=False)
        # Drop the mobile card: the first td when it is hidden on desktop
        if tds and 'hidden-lg' in (tds[0].get('class') or []):
            tds = tds[1:]
        if len(tds) <= max(colmap.values()):
            continue

        position = tds[colmap['POS']].get_text(strip=True).upper()
        raw_name = tds[colmap['NAME']].get_text(' ', strip=True)
        team = tds[colmap['TEAM']].get_text(strip=True).upper()
        proj_text = tds[colmap['DK FP PROJECTED']].get_text(strip=True)

        try:
            proj = float(proj_text)
        except ValueError:
            continue  # '--' / not yet posted
        if math.isnan(proj):
            continue

        if position == 'DST':
            key = normalize_dst_name(raw_name)
            if key:
                projections[key] = proj
            continue

        name = _strip_injury_tag(raw_name)
        key = normalize_name(name)
        if not key:
            continue
        projections[key] = proj
        if team:
            projections[f"{key}|{team.lower()}"] = proj

    return projections


def scrape_dailyfantasyfuel(week=None):
    """Scrape Daily Fantasy Fuel NFL DK-point projections.

    Verified 2026-09: https://www.dailyfantasyfuel.com/nfl/projections/ is
    server-rendered — the full current-slate player table (~450 rows: name
    with injury tag, position, salary, team, opponent, projected DK points)
    is present in the initial HTML with no login. `week` is accepted for
    registry symmetry but ignored (DFF serves the current slate only).

    Returns:
        Dict {normalized_name: projected DK points}, or {} on failure.
    """
    print("  Trying DailyFantasyFuel...")
    html = _fetch_html(DFF_URL)
    if not html:
        return {}

    projections = _parse_dff_projections(html)
    if not projections:
        print("    DailyFantasyFuel: no projections parsed (page layout changed)")
    else:
        print(f"    DailyFantasyFuel: {len(projections)} projections")
    return projections


# ---------------------------------------------------------------------------
# Source 2c: BlueCollarDFS (best-effort stub, login-walled)
# ---------------------------------------------------------------------------

BLUECOLLAR_URL = "https://bluecollardfs.com/nfl-optimizer"


def _parse_bluecollar_projections(html):
    """Attempt to pull player projections from the BlueCollarDFS page shell.

    Best-effort: scans embedded JSON in <script> tags for name/projection
    pairs. The optimizer renders client-side after login and projections are
    premium-gated, so this currently finds nothing — it exists so the source
    slots in automatically if a public payload ever appears.
    """
    import json

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'lxml')
    projections = {}
    for script in soup.find_all('script'):
        text = script.string if script.string else ''
        if not text or 'projection' not in text.lower():
            continue
        for match in re.finditer(r"\{[^{}]*['\"]name['\"][^{}]*\}", text):
            try:
                blob = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                continue
            name = blob.get('name') or blob.get('player')
            pts = blob.get('projection') or blob.get('points') or blob.get('proj')
            if not name or pts is None:
                continue
            try:
                pts = float(pts)
            except (TypeError, ValueError):
                continue
            key = normalize_name(name)
            if key:
                projections[key] = pts
    return projections


def scrape_bluecollar(week=None):
    """Scrape BlueCollarDFS NFL projections (best-effort stub).

    Verified 2026-09: the optimizer page is JS-rendered behind a login and
    projections/CSV/API access are premium features, so an anonymous fetch
    returns only the site shell. Kept as a registry slot per the
    horse_race_predictor pattern: if a public endpoint appears or credentials
    are wired in, it slots in without touching the pipeline.

    Returns:
        Dict {normalized_name: projected DK points}, or {} on failure.
    """
    print("  Trying BlueCollarDFS...")
    html = _fetch_html(BLUECOLLAR_URL)
    if not html:
        return {}

    projections = _parse_bluecollar_projections(html)
    if not projections:
        print("    BlueCollarDFS: no projections (login-walled optimizer, "
              "projections premium-gated)")
    else:
        print(f"    BlueCollarDFS: {len(projections)} projections")
    return projections


# Registry of best-effort scrape fetchers, tried in order
FETCHER_REGISTRY = [
    ('dailyfantasyfuel', scrape_dailyfantasyfuel),
    ('bluecollar', scrape_bluecollar),
    ('numberfire', scrape_numberfire),
    ('fantasypros', scrape_fantasypros),
]


def run_scrape_fetchers(week=None):
    """Run every registered scrape fetcher until one yields projections.

    Returns:
        (source_name, projections_dict) or (None, {}) if all fail
    """
    for name, fetcher in FETCHER_REGISTRY:
        try:
            projections = fetcher(week=week)
        except Exception as e:
            print(f"    {name} fetcher crashed: {e}")
            continue
        if projections:
            return name, projections
    return None, {}


def run_all_scrape_fetchers(week=None):
    """Run every registered scrape fetcher, keeping all sources that yield data.

    Used by the comparison layer (comparison.py / prediction_tracker.py),
    which needs each source independently rather than first-wins.

    Returns:
        Dict {source_name: projections_dict} with only non-empty results.
    """
    results = {}
    for name, fetcher in FETCHER_REGISTRY:
        try:
            projections = fetcher(week=week)
        except Exception as e:
            print(f"    {name} fetcher crashed: {e}")
            continue
        if projections:
            results[name] = projections
    return results


# ---------------------------------------------------------------------------
# Source 3: Salary-implied fallback
# ---------------------------------------------------------------------------

# Crude salary-implied projection curves by position: proj = salary*slope + floor.
# Rough 2020s-era baselines; anything using them is labeled 'fallback' in all
# output so low accuracy is never hidden.
FALLBACK_CURVES = {
    'QB': (0.0022, 4.0),   # $6,000 QB -> 17.2 pts
    'RB': (0.0021, 2.0),   # $6,000 RB -> 14.6 pts
    'WR': (0.0021, 2.0),
    'TE': (0.0019, 1.0),   # $5,000 TE -> 10.5 pts
    'DST': (0.0028, 2.0),  # $3,000 DST -> 10.4 pts
}


def salary_fallback_projection(salary, position):
    """Crude salary-implied projection. Only used when no source matched.

    Args:
        salary: DK salary
        position: primary position (QB/RB/WR/TE/DST)

    Returns:
        float projected DK points
    """
    if not salary:
        return 0.0
    slope, floor = FALLBACK_CURVES.get(position, (0.0020, 2.0))
    return round(salary * slope + floor, 2)


# ---------------------------------------------------------------------------
# Merge layer
# ---------------------------------------------------------------------------

def _resolve_player_projection(player, projections, source_name):
    """Match one player against one source's projection dict.

    DST entries match on team token; everyone else on (name, team) then
    plain normalized name.

    Returns:
        (projection, source_name) or (None, None) if the source has no
        projection for this player.
    """
    if not projections:
        return None, None

    name = player['name']
    team = player.get('team')
    if 'DST' in (player.get('positions') or []):
        dst_key = normalize_dst_name(name)
        if dst_key in projections:
            return projections[dst_key], source_name
        # CSV sources may key DST rows by their literal name ("Patriots DST")
        base_key = _match_key(name)
        if base_key in projections:
            return projections[base_key], source_name
        return None, None

    key = _match_key(name, team)
    if key in projections:
        return projections[key], source_name
    base_key = _match_key(name)
    if base_key in projections:
        return projections[base_key], source_name
    return None, None


def _attach_source(players, projections, source_name):
    """Resolve every player against one source, salary-fallback for misses.

    Returns:
        Dict {player_id: {'projection': float, 'source': str}} — 'source'
        is the source name when matched, 'fallback' when salary-implied.
    """
    attached = {}
    for player in players:
        projection, source = _resolve_player_projection(
            player, projections, source_name)
        if projection is None:
            projection = salary_fallback_projection(
                player.get('salary'), player['position'])
            source = 'fallback'
        attached[player['player_id']] = {'projection': projection, 'source': source}
    return attached


def get_source_projections(players, csv_path=None, week=None, allow_scrape=True):
    """Resolve projections from EVERY available source, independently.

    Unlike get_player_projections (first source wins for the main pipeline),
    this keeps each source separate so lineups can be built per source and
    compared (comparison.py, prediction_tracker.py). Every source dict covers
    every player — unmatched players get the salary-implied fallback within
    that source so its optimizer run is always feasible.

    Args:
        players: List of normalized player dicts (from dk_client.fetch_draftables,
                 post-dedup) with name, team, salary, position
        csv_path: Optional manual CSV projection file (one 'csv' source)
        week: Optional NFL week number for scrapers
        allow_scrape: Whether to attempt web scrapers

    Returns:
        Dict {source_name: {player_id: {'projection': float, 'source': str}}}.
        'fallback' (salary-implied only) is always included as the baseline.
    """
    sources = {}

    csv_projections = load_csv_projections(csv_path) if csv_path else {}
    if csv_projections:
        print(f"Loaded {len(csv_projections)} manual CSV projections")
        sources['csv'] = _attach_source(players, csv_projections, 'csv')

    if allow_scrape:
        print("Fetching web projections from all sources (best-effort)...")
        for name, projections in run_all_scrape_fetchers(week=week).items():
            sources[name] = _attach_source(players, projections, name)

    sources['fallback'] = {
        player['player_id']: {
            'projection': salary_fallback_projection(
                player.get('salary'), player['position']),
            'source': 'fallback',
        }
        for player in players
    }

    return sources


def get_player_projections(players, csv_path=None, week=None, allow_scrape=True):
    """Resolve a projection for every player in the slate.

    Tries sources in priority order per player; every player gets a value.

    Args:
        players: List of normalized player dicts (from dk_client.fetch_draftables,
                 post-dedup) with name, team, salary, position
        csv_path: Optional manual CSV projection file (highest priority)
        week: Optional NFL week number for scrapers
        allow_scrape: Whether to attempt web scrapers

    Returns:
        Dict {player_id: {'projection': float, 'source': str}}
    """
    result = {}

    # Build lookup structures
    csv_projections = load_csv_projections(csv_path) if csv_path else {}
    if csv_projections:
        print(f"Loaded {len(csv_projections)} manual CSV projections")

    scrape_name, scrape_projections = (None, {})
    if allow_scrape:
        print("Fetching web projections (best-effort)...")
        scrape_name, scrape_projections = run_scrape_fetchers(week=week)

    for player in players:
        # 1. Manual CSV, 2. Scrape results
        projection, source = _resolve_player_projection(
            player, csv_projections, 'csv')
        if projection is None:
            projection, source = _resolve_player_projection(
                player, scrape_projections, scrape_name)

        # 3. Salary-implied fallback
        if projection is None:
            projection = salary_fallback_projection(
                player.get('salary'), player['position'])
            source = 'fallback'

        result[player['player_id']] = {'projection': projection, 'source': source}

    return result


def display_projection_sources(player_projections, players):
    """Print a per-player source summary so low-confidence values are visible."""
    from collections import Counter
    sources = Counter(v['source'] for v in player_projections.values())
    print(f"\nProjection sources: {dict(sources)}")

    fallback_players = [
        (p['name'], p['salary'])
        for p in players
        if player_projections.get(p['player_id'], {}).get('source') == 'fallback'
    ]
    if fallback_players:
        print("Players on crude salary fallback (no source projection found):")
        for name, salary in fallback_players:
            print(f"  {name} (${salary:,.0f})")