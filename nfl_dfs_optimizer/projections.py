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
from datetime import date, timedelta, timezone

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


def scrape_numberfire(week=None, slate_games=None, slate_date=None):
    """Scrape numberFire NFL weekly projections (best-effort).

    slate_games/slate_date are accepted for registry symmetry and ignored
    (numberFire serves a league-wide board).

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


def scrape_fantasypros(week=None, slate_games=None, slate_date=None):
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
# DFF's site now serves per-slate projection pages (mid-Sept 2026): the bare
# /nfl/projections/ URL defaults to ONE slate (e.g. "Afternoon Only"), not
# the full slate, so the contest's own slate must be requested explicitly.
DFF_DK_PROJECTIONS_URL = (DFF_URL + "draftkings")
DFF_SHOWDOWN_URL = ("https://www.dailyfantasyfuel.com/nfl/"
                    "showdown-single-game-projections/draftkings")
# Slate list (JS-driven JSON): ?date=YYYY-MM-DD (defaults to today)
DFF_SLATES_URL = ("https://www.dailyfantasyfuel.com/data/slates/recent/"
                  "NFL/draftkings")

# Trailing injury-report tokens in player names ("Ja'Marr Chase Q")
INJURY_TAGS = {'Q', 'D', 'O', 'IR', 'OUT', 'NA', 'P', 'SUSP'}
# Injury statuses that mean the player will NOT play: excluded from the
# projections and from every lineup (questionable players stay in)
OUT_STATUSES = {'O', 'OUT', 'IR', 'SUSP'}
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
    followed by desktop cells, and carries the injury designation in a
    data-inj attribute ('' / 'Q' / 'D' / 'O'). Column names come from the
    detailed thead row (POS/NAME/SALARY/TEAM/OPP/.../'DK FP PROJECTED'), so
    column order changes are tolerated. The current-slate page is fetched;
    DFF has no week param.

    Players listed OUT/IR/SUSP get NO projection (they will not play) and
    their (name, team) pairs are returned separately so the lineup pool can
    exclude them — team-qualified, because the board is league-wide and
    same-named players exist across teams (salary fallback would otherwise
    hand them a projection anyway).

    Also returns each player's DFF-listed team (out_entries carry it too, but
    team-qualified; the team map catches traded players — DK draftables lag
    roster moves, so a slate entry can reference a player who has since left
    the team and cannot appear in the slate's games).

    Returns:
        (projections, out_entries, team_map) — projections as a dict
        {normalized_name: pts} plus {normalized_name|team: pts} keys
        (or ({}, [], {}) if the layout is unrecognized); out_entries as
        (display_name, team_abbr) tuples of players ruled out; team_map as
        {normalized_name: set(team_abbrs)} of every listed non-DST row.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'lxml')
    table = soup.find('table')
    if table is None:
        return {}, [], {}

    # Column map from the detailed header row (the one with POS and NAME)
    colmap = {}
    for tr in table.find_all('tr'):
        headers = [th.get_text(' ', strip=True).upper()
                   for th in tr.find_all('th', recursive=False)]
        if 'POS' in headers and 'NAME' in headers:
            colmap = {h: i for i, h in enumerate(headers)}
            break
    if not colmap:
        return {}, [], {}

    projections = {}
    out_entries = []
    team_map = {}
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

        # Record the DFF-listed team for every non-DST row — injured or
        # not, projected or not. get_dff_team_mismatches() uses it to catch
        # players DK's draftables still list under a team they've left.
        norm = (normalize_name(_strip_injury_tag(raw_name) or raw_name)
                if position != 'DST' else '')
        if norm and team:
            team_map.setdefault(norm, set()).add(team)

        injury = (row.get('data-inj') or '').strip().upper()
        if injury in OUT_STATUSES:
            entry = (_strip_injury_tag(raw_name) or raw_name.strip(), team)
            if entry not in out_entries:  # DFF lists some players twice
                out_entries.append(entry)
            continue

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

    return projections, out_entries, team_map


def fetch_dff_slates(slate_date=None):
    """Fetch DFF's slate list for a date. Best-effort: [] on any failure.

    Args:
        slate_date: 'YYYY-MM-DD' (defaults to today) — the date of the
                    contest's slate, from the draftables' game_start

    Returns:
        List of slate dicts {slate_type, url, showdown_flag, game_count,
        ...} ('slate_type' is '' for the main classic slate, 'IND @ KC'
        for a single-game showdown slate).
    """
    params = {'date': slate_date or date.today().isoformat(), 'url': ''}
    try:
        response = requests.get(DFF_SLATES_URL, params=params,
                               headers={'User-Agent': _HEADERS['User-Agent'],
                                        'Accept': 'application/json'},
                               timeout=15)
        response.raise_for_status()
        return response.json().get('slates') or []
    except (requests.RequestException, ValueError) as e:
        print(f"    DFF slate list fetch failed: {e}")
        return []


def resolve_dff_slate(slates, slate_games):
    """Pick the DFF slate covering exactly this contest's games.

    Args:
        slates: Slate dicts from fetch_dff_slates()
        slate_games: The contest's game strings ('IND @ KC') from the
                     draftables — one entry means showdown

    Returns:
        (slate_url, is_showdown) or (None, False) when nothing matches —
        the caller then falls back to the default page. A single-game
        slate must match its game name exactly; a multi-game slate picks
        the tightest classic slate that covers it (smallest game_count
        >= the contest's game count — the main slate over Sun-Mon).
    """
    if not slates or not slate_games:
        return None, False

    games = [g.upper() for g in slate_games]
    if len(games) == 1:
        for slate in slates:
            if (slate.get('showdown_flag') == 1
                    and slate.get('slate_type', '').upper() == games[0]):
                return slate['url'], True
        return None, False

    game_count = len(games)
    covering = [s for s in slates if not s.get('showdown_flag')
                and s.get('game_count', 0) >= game_count]
    if not covering:
        return None, False
    tightest = min(covering, key=lambda s: s['game_count'])
    return tightest['url'], False


def scrape_dailyfantasyfuel(week=None, slate_games=None, slate_date=None):
    """Scrape Daily Fantasy Fuel NFL DK-point projections.

    Verified 2026-09: the projections pages are server-rendered — the
    full slate's player table (name with injury tag, position, salary,
    team, opponent, projected DK points, data-inj injury designation) is
    present in the initial HTML with no login. `week` is accepted for
    registry symmetry but ignored (DFF serves the current slate only).

    Since mid-Sept 2026 DFF serves ONE slate per page: the bare
    /nfl/projections/ URL defaults to a partial slate (e.g. "Afternoon
    Only"), so when the contest's games are known (slate_games from the
    draftables, one entry = showdown) the matching per-slate page is
    fetched instead — the single-game showdown page for one game, the
    tightest covering classic slate otherwise. Unknown slates fall back
    to the default page with a printed note.

    Also caches the players DFF lists as OUT/IR/SUSP for
    get_dff_out_names(), so the pool excludes them from every lineup.

    Args:
        week: Ignored (registry symmetry)
        slate_games: The contest's game strings ('IND @ KC') from the
                     draftables; None = the site's default slate
        slate_date: The slate's 'YYYY-MM-DD' (from the draftables'
                    game_start); defaults to today

    Returns:
        Dict {normalized_name: projected DK points}, or {} on failure.
    """
    global _dff_out_cache, _dff_team_cache
    print("  Trying DailyFantasyFuel...")
    url = DFF_URL
    if slate_games:
        slates = fetch_dff_slates(slate_date)
        slate_url, is_showdown = resolve_dff_slate(slates, slate_games)
        if slate_url:
            base = DFF_SHOWDOWN_URL if is_showdown else DFF_DK_PROJECTIONS_URL
            date_str = slate_date or date.today().isoformat()
            url = f"{base}/{date_str}?slate={slate_url}"
            print(f"    DFF slate page: {slate_url}")
        else:
            print("    DFF has no slate matching this contest's games — "
                  "using the site's default slate (may be partial)")
    html = _fetch_html(url)
    if not html:
        _dff_out_cache = []
        _dff_team_cache = {}
        return {}

    projections, out_entries, team_map = _parse_dff_projections(html)
    _dff_out_cache = out_entries
    _dff_team_cache = team_map
    if not projections:
        print("    DailyFantasyFuel: no projections parsed (page layout changed)")
    else:
        print(f"    DailyFantasyFuel: {len(projections)} projections")
        if out_entries:
            shown = ', '.join(n for n, _ in out_entries[:6])
            more = (f' (+{len(out_entries) - 6} more)'
                    if len(out_entries) > 6 else '')
            print(f"    DailyFantasyFuel injury report: {len(out_entries)} "
                  f"listed OUT/IR (excluded): {shown}{more}")
    return projections


# Players DFF lists as OUT/IR/SUSP from the last scrape (None = never
# scraped; [] = scraped but none/failed). Module cache so the pool can
# exclude them without a second fetch.
_dff_out_cache = None


def get_dff_out_names(allow_scrape=True):
    """Players DFF lists as OUT/IR/SUSP on the current slate.

    Uses the cache from the last DailyFantasyFuel scrape; scrapes once if
    none has run yet (so a CSV-first pipeline still gets the injury list).
    Returns [] when DFF is unavailable — never blocks lineup building.

    Returns:
        List of (name, team_abbr) tuples, team-qualified for exclusion
        matching (same-named players exist across teams).
    """
    if _dff_out_cache is None and allow_scrape:
        scrape_dailyfantasyfuel()
    return list(_dff_out_cache or [])


# Each player's DFF-listed teams from the last scrape ({norm_name: set(abbrs)};
# None = never scraped; {} = scraped but none/failed). Same fetch as
# _dff_out_cache — the caches fill together.
_dff_team_cache = None


# Team-abbreviation aliases across sites (DFF currently matches DK's set —
# verified 2026-09 — but a mismatch EXCLUDES a player, so equivalent
# spellings must never compare unequal; same lesson as the A.J. Brown
# name-token bug: convention drift must not silently drop players).
TEAM_ABBR_ALIASES = {
    'WSH': 'WAS', 'JAC': 'JAX', 'LA': 'LAR', 'SD': 'LAC', 'OAK': 'LV',
}


def _norm_team_abbr(team):
    return TEAM_ABBR_ALIASES.get((team or '').strip().upper(),
                                 (team or '').strip().upper())


def get_dff_team_mismatches(players, allow_scrape=True):
    """Slate players DFF now lists under a team DK does not show them on.

    DK draftables lag real-world roster moves (verified 2026-09: after
    Kayshon Boutte was traded NE->HOU, DK's NE@SEA slate still listed him
    as a Patriot while DFF's board already carried him under HOU with a
    fresh projection). Such a player cannot appear in the slate's games —
    callers exclude them from the pool like OUT players (bare-name
    matching would otherwise still hand them DFF's projection).

    Only names DFF explicitly lists under another team are flagged: if DFF
    shows the player on ANY of the DK team(s) seen for that name, no flag
    (covers two same-named players on different teams).

    Args:
        players: Normalized DK draftable dicts (name, team, positions)
        allow_scrape: Scrape DFF once if no cache exists yet

    Returns:
        List of (display_name, dk_team) tuples — the stale DK slate entries.
    """
    if _dff_team_cache is None and allow_scrape:
        scrape_dailyfantasyfuel()
    dff_teams = _dff_team_cache or {}

    mismatches = []
    for player in players:
        if 'DST' in (player.get('positions') or []):
            continue
        dk_team = _norm_team_abbr(player.get('team'))
        if not dk_team:
            continue  # no DK team to compare against
        dff_team_set = dff_teams.get(normalize_name(player.get('name') or ''))
        if not dff_team_set:
            continue  # not on DFF's board — nothing to compare
        if dk_team not in {_norm_team_abbr(t) for t in dff_team_set}:
            mismatches.append((player['name'], player.get('team')))
    return mismatches


# ---------------------------------------------------------------------------
# Source 2c: BlueCollarDFS (best-effort stub, login-walled)
# ---------------------------------------------------------------------------

BLUECOLLAR_URL = "https://bluecollardfs.com/nfl-optimizer"
# Documented developer API (bluecollardfs.com/developers): premium-gated
# (key by email, 200 requests/day) but the exact shape we need — slates of
# {name, team, position, opponent, projection, salary, value} string fields.
BLUECOLLAR_API_URL = "https://bluecollardfs.com/api/nfl_draftkings"


def _parse_bluecollar_api(data):
    """Parse BlueCollarDFS developer-API JSON into projection keys.

    Verified shape (2026-09, bluecollardfs.com/developers): every value is a
    STRING — {'slates': [{'slate': 'Main', 'slate_type': 'classic',
    'info': [{'name', 'team', 'position', 'opponent', 'projection',
    'salary', 'value'}]}]}.

    Returns:
        Dict {normalized_name: pts} plus {normalized_name|team: pts} keys
    """
    projections = {}
    for slate in data.get('slates') or []:
        for entry in slate.get('info') or []:
            name = entry.get('name')
            try:
                pts = float(entry.get('projection'))
            except (TypeError, ValueError):
                continue
            if not name or math.isnan(pts):
                continue
            team = entry.get('team') or ''
            if (entry.get('position') or '').upper() == 'DST':
                key = normalize_dst_name(name)
                if key:
                    projections[key] = pts
            else:
                key = normalize_name(name)
                if key:
                    projections[key] = pts
                    if team:
                        projections[f"{key}|{team.lower()}"] = pts
    return projections


def fetch_bluecollar_api(timeout=15):
    """Call the BlueCollarDFS NFL DraftKings API if a key is configured.

    The key lives in the BLUECOLLAR_API_KEY (or BCDFS_API_KEY) environment
    variable — premium-gated, obtained by emailing bluecollardfs@gmail.com
    (bluecollardfs.com/developers). No key configured -> (None, None), so
    the HTML-shell fallback runs instead.

    Returns:
        Parsed projections dict, or ({}, None) on no-key, or ({}, status)
        on an HTTP error (401 bad key / 403 no sport access / 429 limit).
    """
    import os

    key = (os.environ.get('BLUECOLLAR_API_KEY')
           or os.environ.get('BCDFS_API_KEY'))
    if not key:
        return None, None
    try:
        resp = requests.get(
            BLUECOLLAR_API_URL,
            headers={'User-Agent': 'Mozilla/5.0',
                     'Authorization': f'ApiKey {key}'},
            timeout=timeout)
    except requests.RequestException as e:
        print(f"    BlueCollarDFS API request failed: {e}")
        return {}, None
    if resp.status_code != 200:
        detail = ''
        try:
            detail = resp.json().get('error') or ''
        except ValueError:
            pass
        print(f"    BlueCollarDFS API: HTTP {resp.status_code}"
              f"{' - ' + detail if detail else ''} — no projections")
        return {}, resp.status_code
    try:
        return _parse_bluecollar_api(resp.json()), 200
    except ValueError as e:
        print(f"    BlueCollarDFS API: bad JSON ({e})")
        return {}, None


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


def scrape_bluecollar(week=None, slate_games=None, slate_date=None):
    """Scrape BlueCollarDFS NFL projections (API first, HTML shell second).

    Verified 2026-09: the optimizer page is JS-rendered behind a login and
    projections are premium-gated — an anonymous fetch returns only the site
    shell. The site DOES document a developer API (bluecollardfs.com/
    developers, premium key by email, 200 requests/day): when a key is in
    BLUECOLLAR_API_KEY / BCDFS_API_KEY, this fetcher calls it and becomes a
    real projection source; without one, the HTML fallback stays a stub.

    Returns:
        Dict {normalized_name: projected DK points}, or {} on failure.
    """
    print("  Trying BlueCollarDFS...")
    projections, status = fetch_bluecollar_api()
    if status == 200 and projections:
        print(f"    BlueCollarDFS API: {len(projections)} projections")
        return projections
    if status == 200 and not projections:
        print("    BlueCollarDFS API: no slates parsed")
        return {}

    # No key configured (or the call failed) — anonymous HTML shell
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


# DFF's slate dates are site-local US Eastern — DK's game_start datetimes
# are UTC, so a Sunday 8:20 PM ET game is Monday 00:20 UTC; deriving the
# slate date without the ET shift asks DFF for the wrong day's slates.
_DFF_TZ = timezone(timedelta(hours=-5))


def _slate_context(players):
    """The contest's games + slate date, from the draftables pool.

    Returns:
        (games, slate_date) — games as the sorted set of game strings
        ('IND @ KC'), slate_date as the ET 'YYYY-MM-DD' of the earliest
        game_start (DFF's slate dates are Eastern); ([], None) when the
        pool carries no game info (synthetic pools), in which case
        slate-aware scrapers keep their default-slate behavior.
    """
    games = sorted({p['game'] for p in players if p.get('game')})
    starts = [p['game_start'] for p in players if p.get('game_start')]
    et_starts = [s.astimezone(_DFF_TZ) if s.tzinfo else s for s in starts]
    slate_date = (min(et_starts).strftime('%Y-%m-%d') if et_starts
                  else None)
    return games, slate_date


def run_scrape_fetchers(week=None, slate_games=None, slate_date=None):
    """Run every registered scrape fetcher until one yields projections.

    Args:
        week: Optional NFL week for scrapers that accept one
        slate_games/slate_date: The contest's games and slate date (from
                                the draftables) — passed to slate-aware
                                fetchers (DFF); others ignore them

    Returns:
        (source_name, projections_dict) or (None, {}) if all fail
    """
    for name, fetcher in FETCHER_REGISTRY:
        try:
            projections = fetcher(week=week, slate_games=slate_games,
                                  slate_date=slate_date)
        except Exception as e:
            print(f"    {name} fetcher crashed: {e}")
            continue
        if projections:
            return name, projections
    return None, {}


def run_all_scrape_fetchers(week=None, slate_games=None, slate_date=None):
    """Run every registered scrape fetcher, keeping all sources that yield data.

    Used by the comparison layer (comparison.py / prediction_tracker.py),
    which needs each source independently rather than first-wins.

    Args:
        week: Optional NFL week for scrapers that accept one
        slate_games/slate_date: The contest's games and slate date (from
                                the draftables) — passed to slate-aware
                                fetchers (DFF); others ignore them

    Returns:
        Dict {source_name: projections_dict} with only non-empty results.
    """
    results = {}
    for name, fetcher in FETCHER_REGISTRY:
        try:
            projections = fetcher(week=week, slate_games=slate_games,
                                  slate_date=slate_date)
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
        slate_games, slate_date = _slate_context(players)
        for name, projections in run_all_scrape_fetchers(
                week=week, slate_games=slate_games,
                slate_date=slate_date).items():
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
        slate_games, slate_date = _slate_context(players)
        scrape_name, scrape_projections = run_scrape_fetchers(
            week=week, slate_games=slate_games, slate_date=slate_date)

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