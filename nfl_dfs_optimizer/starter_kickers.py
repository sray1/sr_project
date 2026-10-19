"""
Starting-placekicker resolution via ESPN's roster API.

DK NFL draftables list a team's placekicker, punter and sometimes a
practice-squad kicker ALL under position 'K' at the same salary — the
kicker model then hands them near-identical projections, so the MILP
treats the punter as an interchangeable starter (DAL @ GB week 6 listed
Trey Smack AND practice-squad kicker Lenny Krieg, both $4,800 / 11.78
proj, and both appeared as captain candidates). ESPN's roster API
separates them cleanly:

    group 'specialTeam', position 'PK'  -> the active placekicker
    group 'specialTeam', position 'P'   -> the punter (never a fantasy K)
    groups 'practiceSquad' / 'injuredReserveOrOut' / 'suspended'
                                       -> not eligible

Same hidden-API pattern as game_results (no auth, best-effort): any
fetch failure keeps the pool unchanged. Rosters are cached per process
so per-source comparison builds don't re-fetch.
"""

from game_results import _fetch_json
from projections import normalize_name

ESPN_TEAMS_URL = ("https://site.api.espn.com/apis/site/v2/"
                  "sports/football/nfl/teams")
ESPN_ROSTER_URL = (ESPN_TEAMS_URL + "/{team_id}/roster")

# Roster buckets an eligible starting kicker can live in (ESPN groups
# kickers under 'specialTeam', but accept any active group)
ACTIVE_GROUPS = {'offense', 'defense', 'specialTeam'}
KICKER_POSITIONS = {'PK', 'K'}

_team_id_map = None
_roster_cache = {}  # {team_abbr: set(norm_names)}; empty set = unresolved


def get_team_ids():
    """{abbreviation: ESPN team id}, fetched once per process."""
    global _team_id_map
    if _team_id_map is not None:
        return _team_id_map
    data = _fetch_json(ESPN_TEAMS_URL, {'limit': 40})
    mapping = {}
    for league in ((data or {}).get('sports') or [{}])[0].get('leagues', []):
        for entry in league.get('teams', []):
            team = entry.get('team') or {}
            if team.get('abbreviation') and team.get('id'):
                mapping[team['abbreviation']] = str(team['id'])
    _team_id_map = mapping
    return mapping


def get_starting_kickers(team_abbrevs):
    """Active placekickers per team, from ESPN rosters (cached).

    Args:
        team_abbrevs: Iterable of team abbreviations to resolve (only the
                      slate's own teams — showdown asks for 2)

    Returns:
        {abbr: set(norm_name)} of each team's active placekickers. A team
        whose roster can't be fetched (or isn't in ESPN's team list) maps
        to an empty set — callers keep such a team's kickers untouched.
    """
    ids = get_team_ids()
    result = {}
    for abbr in team_abbrevs:
        if abbr in _roster_cache:
            result[abbr] = _roster_cache[abbr]
            continue
        names = set()
        team_id = ids.get(abbr)
        if team_id:
            roster = _fetch_json(ESPN_ROSTER_URL.format(team_id=team_id), {})
            for group in (roster or {}).get('athletes') or []:
                if group.get('position') not in ACTIVE_GROUPS:
                    continue
                for item in group.get('items') or []:
                    pos = item.get('position')
                    pos_abbr = (pos.get('abbreviation')
                                if isinstance(pos, dict) else pos)
                    if pos_abbr not in KICKER_POSITIONS:
                        continue
                    full = ' '.join(filter(None, (item.get('firstName'),
                                                   item.get('lastName'))))
                    key = normalize_name(full) or normalize_name(
                        item.get('displayName'))
                    if key:
                        names.add(key)
        _roster_cache[abbr] = names
        result[abbr] = names
    return result


def filter_nonstarter_kickers(pool):
    """Drop DK-listed kickers who are not their team's active placekicker.

    Safety valve: a team is only filtered when at least one of its
    DK-listed kickers matches an ESPN active placekicker by name — if no
    pool kicker matches (roster fetch failed, or DK and ESPN spell the
    name differently), every kicker on that team stays in the pool
    rather than risking a wrong drop.

    Returns:
        (kept, dropped) pool entries
    """
    kickers_by_team = {}
    for entry in pool:
        if 'K' in (entry.get('positions') or []):
            kickers_by_team.setdefault(entry.get('team'), []).append(entry)
    if not kickers_by_team:
        return pool, []

    starters = get_starting_kickers(kickers_by_team)
    # Only filter a team whose starter set actually intersects the slate's
    # kicker names (see docstring)
    filter_team = {}
    for team, kickers in kickers_by_team.items():
        names = starters.get(team) or set()
        filter_team[team] = bool(names) and any(
            normalize_name(k['name']) in names for k in kickers)

    kept, dropped = [], []
    for entry in pool:
        team = entry.get('team')
        if ('K' not in (entry.get('positions') or [])
                or not filter_team.get(team)):
            kept.append(entry)
        elif normalize_name(entry['name']) in (starters.get(team) or set()):
            kept.append(entry)
        else:
            dropped.append(entry)
    return kept, dropped