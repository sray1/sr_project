"""
ESPN injury-report guard for the player pool.

Why this exists: DFF's board and DK's own status flag lag team
announcements. Week 8 2026 (TB @ CHI) the Bears had already announced
Rachad Monangai would not play, yet DFF still projected him 16.70 — he
was carried into all 5 captain builds and recorded nothing, the season's
worst capture (44.7%). DK's 'status' field had not flipped to OUT either.

ESPN's hidden injuries API (the same site.api.espn.com feed the
results/scoring pipeline already uses) publishes the official practice
report per team: each entry carries a status ('Out', 'Injured Reserve',
'Doubtful', 'Questionable', 'Active') and the athlete's name. Players
listed Out/IR are excluded from the pool like DFF OUT players —
team-qualified (name, team) tuples through the same
exclude_named_players path. Doubtful/Questionable players stay in the
pool (they might play) but are reported as notes.

Best-effort like every other scrape: on any fetch/parse failure the
guard returns empty and lineup building proceeds untouched.
"""

import requests

from projections import TEAM_ABBR_ALIASES, normalize_name

INJURIES_URL = ('https://site.api.espn.com/apis/site/v2/sports/'
                'football/nfl/injuries')
INJURY_TIMEOUT = 15  # seconds

# ESPN team ids are stable (site.api.espn.com .../teams, verified
# 2026-11); mapping them here avoids a second fetch per run.
ESPN_TEAM_ABBRS = {
    '1': 'ATL', '2': 'BUF', '3': 'CHI', '4': 'CIN', '5': 'CLE',
    '6': 'DAL', '7': 'DEN', '8': 'DET', '9': 'GB', '10': 'TEN',
    '11': 'IND', '12': 'KC', '13': 'LV', '14': 'LAR', '15': 'MIA',
    '16': 'MIN', '17': 'NE', '18': 'NO', '19': 'NYG', '20': 'NYJ',
    '21': 'PHI', '22': 'ARI', '23': 'PIT', '24': 'LAC', '25': 'SF',
    '26': 'SEA', '27': 'TB', '28': 'WSH', '29': 'CAR', '30': 'JAX',
    '33': 'BAL', '34': 'HOU',
}

# Statuses that bench a player entirely (compared lowercase).
DROP_STATUSES = {'out', 'injured reserve', 'suspended', 'suspension'}
# Statuses that keep the player in the pool but are worth a note.
NOTE_STATUSES = {'doubtful', 'questionable'}

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# {team_abbr: {norm_name: status}} from the last fetch; None = never
# fetched, {} = fetched but nothing/failed. Module cache so the pool
# can consult it without a second fetch (same pattern as
# projections._dff_out_cache).
_injury_cache = None


def _norm_team(team):
    """DK team abbreviation, canonicalized (WSH -> WAS etc.)."""
    team = (team or '').strip().upper()
    return TEAM_ABBR_ALIASES.get(team, team)


def fetch_injury_report(allow_scrape=True):
    """ESPN injury report keyed for slate lookups.

    One fetch per process. Returns {team_abbr: {norm_name: status}}
    (team abbreviations DK-style, player names normalized), {} when
    the fetch is disallowed or fails — never blocks lineup building.
    """
    global _injury_cache
    if _injury_cache is not None:
        return _injury_cache
    _injury_cache = {}
    if not allow_scrape:
        return _injury_cache

    print("  Fetching ESPN injury report...")
    try:
        response = requests.get(INJURIES_URL, headers=_HEADERS,
                                 timeout=INJURY_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as e:
        print(f"    ESPN injury fetch failed: {e} — pool unchanged")
        return _injury_cache

    for team_entry in payload.get('injuries') or []:
        abbr = ESPN_TEAM_ABBRS.get(str(team_entry.get('id')))
        if not abbr:
            continue
        for item in team_entry.get('injuries') or []:
            athlete = item.get('athlete') or {}
            name = athlete.get('displayName')
            status = (item.get('status') or '').strip().lower()
            if not name or not status:
                continue
            _injury_cache.setdefault(_norm_team(abbr), {})[
                normalize_name(name)] = status

    n_out = sum(1 for team in _injury_cache.values()
                for status in team.values()
                if status in DROP_STATUSES)
    print(f"    ESPN injury report: {len(_injury_cache)} teams, "
          f"{n_out} players Out/IR")
    return _injury_cache


def espn_injury_exclusions(players, allow_scrape=True):
    """Slate players ESPN lists as Out/IR — exclusion entries for the pool.

    Matches slate players by normalized name AND team, so league-wide
    report entries for players not on the slate (or same-named players
    on other teams) never exclude anyone.

    Args:
        players: Normalized DK draftable dicts (name, team)
        allow_scrape: Fetch ESPN once if no cache exists yet

    Returns:
        List of (player_name, team_abbr) tuples — the DK player's own
        name and team, ready for exclude_named_players (team-qualified,
        so the DFF/manual exclusion path handles them identically)
    """
    report = fetch_injury_report(allow_scrape=allow_scrape)
    if not report:
        return []

    exclusions = []
    for player in players:
        team = _norm_team(player.get('team'))
        status = report.get(team, {}).get(normalize_name(player['name']))
        if status in DROP_STATUSES:
            exclusions.append((player['name'], player.get('team')))
    return exclusions


def espn_injury_notes(players, allow_scrape=True):
    """Slate players listed Doubtful/Questionable, for display only.

    Returns:
        List of strings 'Name (TEAM) — Questionable' sorted by name
    """
    report = fetch_injury_report(allow_scrape=allow_scrape)
    notes = []
    for player in players:
        team = _norm_team(player.get('team'))
        status = report.get(team, {}).get(normalize_name(player['name']))
        if status in NOTE_STATUSES:
            notes.append(f"{player['name']} ({player.get('team')}) — "
                         f"{status.capitalize()}")
    return sorted(notes)
