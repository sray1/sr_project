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
'Doubtful', 'Questionable', 'Active'), the athlete's name, and the
timestamp of the designation. Players listed Out/IR are excluded from
the pool like DFF OUT players — team-qualified (name, team) tuples
through the same exclude_named_players path. Doubtful/Questionable
players stay in the pool (they might play) but are reported as notes.

Stale-tag guard (WAS @ NYG TNF week 10 2026): on short weeks the
report still carries the PREVIOUS game week's designations. Cam
Skattebo (Out all week for the Sunday PHI game with a hamstring) was
still listed Out at the Thursday fetch, played that night, and rushed
for 2 TDs — the exclusion cost the flagship build ~52 points. Each
designation carries a timestamp; when the team has played any game
AFTER the designation was recorded (checked against the team's season
schedule, one small fetch per team with a pending exclusion), the tag
belongs to a previous game week and is stale: the player stays in the
pool and is reported as a note to verify before lock.

Best-effort like every other scrape: on any fetch/parse failure the
guard returns empty and lineup building proceeds untouched. A missing
designation timestamp or a failed schedule fetch makes every tag count
as fresh (the pre-staleness behavior — only positive evidence of a
game between the tag and now ever keeps a player).
"""

from datetime import datetime, timezone

import requests

from projections import TEAM_ABBR_ALIASES, normalize_name

INJURIES_URL = ('https://site.api.espn.com/apis/site/v2/sports/'
                'football/nfl/injuries')
SCHEDULE_URL = ('https://site.api.espn.com/apis/site/v2/sports/'
                'football/nfl/teams/{team_id}/schedule')
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
# Parallel cache: {team_abbr: {norm_name: datetime|None}} — when ESPN
# recorded each designation (its 'date' field), for staleness checks.
_injury_dates = None
# {team_abbr: [game datetimes]} from the teams schedule endpoint; {}
# entries mean the fetch failed (all designations then count as fresh).
_schedule_cache = {}


def _norm_team(team):
    """DK team abbreviation, canonicalized (WSH -> WAS etc.)."""
    team = (team or '').strip().upper()
    return TEAM_ABBR_ALIASES.get(team, team)


# ESPN team id for each DK-style abbreviation (reverse of the map above,
# canonicalized: WSH -> WAS -> id 28).
_ESPN_ABBR_IDS = {_norm_team(abbr): tid for tid, abbr in ESPN_TEAM_ABBRS.items()}


def _utcnow():
    """Timezone-aware now — a seam tests can freeze."""
    return datetime.now(timezone.utc)


def _parse_espn_date(value):
    """ESPN ISO timestamp ('2026-11-13T04:38Z') -> datetime, or None."""
    try:
        return datetime.fromisoformat((value or '').strip().replace('Z', '+00:00'))
    except ValueError:
        return None


def fetch_injury_report(allow_scrape=True):
    """ESPN injury report keyed for slate lookups.

    One fetch per process. Returns {team_abbr: {norm_name: status}}
    (team abbreviations DK-style, player names normalized), {} when
    the fetch is disallowed or fails — never blocks lineup building.
    """
    global _injury_cache, _injury_dates
    if _injury_cache is not None:
        return _injury_cache
    _injury_cache = {}
    _injury_dates = {}
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
            norm = normalize_name(name)
            _injury_cache.setdefault(_norm_team(abbr), {})[norm] = status
            _injury_dates.setdefault(_norm_team(abbr), {})[norm] = \
                _parse_espn_date(item.get('date'))

    n_out = sum(1 for team in _injury_cache.values()
                for status in team.values()
                if status in DROP_STATUSES)
    print(f"    ESPN injury report: {len(_injury_cache)} teams, "
          f"{n_out} players Out/IR")
    return _injury_cache


def team_game_dates(team, allow_scrape=True):
    """The team's game datetimes this season (ESPN schedule hidden-API).

    One fetch per team per process, cached; [] when the team is unknown
    or the fetch fails — callers then treat every designation as fresh
    (the pre-staleness behavior). Only fetched for teams with a pending
    exclusion, so the normal run adds at most a couple of requests.
    """
    team = _norm_team(team)
    if team in _schedule_cache:
        return _schedule_cache[team]
    dates = []
    team_id = _ESPN_ABBR_IDS.get(team)
    if allow_scrape and team_id:
        try:
            response = requests.get(SCHEDULE_URL.format(team_id=team_id),
                                    headers=_HEADERS, timeout=INJURY_TIMEOUT)
            response.raise_for_status()
            for event in response.json().get('events') or []:
                when = _parse_espn_date(event.get('date'))
                if when:
                    dates.append(when)
            dates.sort()
        except (requests.RequestException, ValueError):
            pass
    _schedule_cache[team] = dates
    return dates


def _designation_is_stale(team, norm_name, now, allow_scrape=True):
    """True when a game happened AFTER the designation was recorded.

    The tag then belongs to a previous game week (the TNF short-week
    trap: Sunday's 'Out' still sitting in Thursday's report). A missing
    timestamp or an empty schedule never counts as stale — only
    positive evidence of an intervening game ever keeps a player.
    """
    dated = (_injury_dates or {}).get(_norm_team(team), {}).get(norm_name)
    if dated is None:
        return False
    games = team_game_dates(team, allow_scrape=allow_scrape)
    return any(dated < game < now for game in games)


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

    now = _utcnow()
    exclusions = []
    stale_kept = 0
    for player in players:
        team = _norm_team(player.get('team'))
        norm = normalize_name(player['name'])
        status = report.get(team, {}).get(norm)
        if status not in DROP_STATUSES:
            continue
        if _designation_is_stale(team, norm, now, allow_scrape=allow_scrape):
            stale_kept += 1
            continue
        exclusions.append((player['name'], player.get('team')))
    if stale_kept:
        print(f"    ESPN injury guard: {stale_kept} Out/IR tag(s) predate "
              f"the team's last game (previous game week) — kept in pool, "
              f"verify before lock")
    return exclusions


def espn_injury_notes(players, allow_scrape=True):
    """Slate players with an injury tag, for display only.

    Doubtful/Questionable players stay in the pool and are noted; so
    are Out/IR players whose tag is STALE (predates the team's last
    game — kept in the pool, so the user sees them before lock).

    Returns:
        List of strings 'Name (TEAM) — Status' sorted by name
    """
    report = fetch_injury_report(allow_scrape=allow_scrape)
    if not report:
        return []

    now = _utcnow()
    notes = []
    for player in players:
        team = _norm_team(player.get('team'))
        norm = normalize_name(player['name'])
        status = report.get(team, {}).get(norm)
        if status in NOTE_STATUSES:
            notes.append(f"{player['name']} ({player.get('team')}) — "
                         f"{status.capitalize()}")
        elif status in DROP_STATUSES and _designation_is_stale(
                team, norm, now, allow_scrape=allow_scrape):
            notes.append(f"{player['name']} ({player.get('team')}) — "
                         f"{status.capitalize()} (stale tag — verify)")
    return sorted(notes)
