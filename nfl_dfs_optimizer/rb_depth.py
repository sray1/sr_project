"""
Backup-RB filter via ESPN's depth-chart API.

Why this exists (week 11 2026, the classic main slate): the projection
board was thin (DFF matched 25 of 514 players), so every cheap RB
carried a salary-curve fallback projection that prices role-less
players like starters. The optimizer built around DJ Giddens (zero
snaps the week before, behind a healthy Jonathan Taylor) and Jerome
Ford (practice-squad elevation, ARI's No. 4 back) — the salary curve
gave both ~10-12 'points' and the MILP happily spent real cap on them.
Same failure mode as the backup-QB filter, but DK salaries do NOT flag
the starter at RB (committees are normal), so the top-salary heuristic
alone can't fix it.

ESPN's depth-chart hidden-API ranks each team's backs
(sports.core.api.espn.com/v2/.../teams/{id}/depthcharts). A pool RB is
KEPT when either:

  1. he is among the top CHART_KEEP ranks of his team's chart who are
     actually in the pool (unavailable players — Out/IR, already
     dropped by the DK status filter — are skipped when counting
     ranks: KC's chart still listed IR'd Kenneth Walker III at #1, so
     Emmett Johnson is the effective #1), or
  2. he is among the top SALARY_KEEP DK salaries of his team's pool
     RBs (salary corroborates role when the chart lags — GB's chart
     still listed the injured Josh Jacobs #1 with beat-report
     favorite Kaleb Johnson 4th, but Johnson is DK's No. 2 salary).

Everything else on that team's RB list is dropped. The two arms union,
so a committee's top 2-3 backs always survive and only the 3rd-string
/ practice-squad / zero-snap ghosts go.

Safety valve (same pattern as the kicker filter): a team is only
filtered when its chart RESOLVED — a fetch failure or an unresolvable
team keeps that team's RBs untouched. Best-effort like every scrape.
"""

from game_results import _fetch_json
from projections import normalize_name
from starter_kickers import ESPN_ROSTER_URL, get_team_ids

# Depth charts are season-scoped on the core API.
ESPN_SEASON = 2026
ESPN_DEPTHCHARTS_URL = ('https://sports.core.api.espn.com/v2/sports/'
                        'football/leagues/nfl/seasons/'
                        '{season}/teams/{team_id}/depthcharts')

CHART_KEEP = 2    # top chart ranks kept per team (pool-present only)
SALARY_KEEP = 2   # top DK salaries kept per team (chart-lag corroboration)

# {team_abbr: [norm_name in chart rank order]}; [] = fetched but
# unresolvable/failed. Module cache so per-source comparison builds
# don't re-fetch (same pattern as starter_kickers._roster_cache).
_depth_cache = {}


def rb_depth_chart(team, allow_scrape=True):
    """The team's RBs in depth-chart rank order, normalized names.

    Fetches the depth chart (athlete ids by rank) and the roster
    (id -> name) — two small requests per team, cached per process.
    Returns [] when the team is unknown or either fetch fails: callers
    then leave the team's RBs untouched.
    """
    if team in _depth_cache:
        return _depth_cache[team]

    ranked = []
    if allow_scrape:
        team_id = get_team_ids().get((team or '').strip().upper())
        if team_id:
            chart = _fetch_json(
                ESPN_DEPTHCHARTS_URL.format(season=ESPN_SEASON,
                                            team_id=team_id), {})
            # Athlete ids in rank order: scan every chart (offense,
            # defense, special teams) for the RB position entries.
            entries = []
            for item in (chart or {}).get('items') or []:
                for pos in (item.get('positions') or {}).values():
                    if (pos.get('position') or {}).get('abbreviation') != 'RB':
                        continue
                    for athlete in pos.get('athletes') or []:
                        ref = str((athlete.get('athlete') or {}).get('$ref')
                                  or '')
                        if '/athletes/' in ref:
                            athlete_id = ref.split('/athletes/')[1]\
                                           .split('?')[0]
                            entries.append((athlete.get('rank'), athlete_id))
            if entries:
                roster = _fetch_json(
                    ESPN_ROSTER_URL.format(team_id=team_id),
                    {'limit': 100})
                names = {}
                for group in (roster or {}).get('athletes') or []:
                    for item in group.get('items') or []:
                        full = ' '.join(filter(None, (item.get('firstName'),
                                                      item.get('lastName'))))
                        key = normalize_name(full)
                        if key:
                            names[str(item.get('id'))] = key
                ranked = [names[athlete_id]
                          for _, athlete_id in sorted(entries)
                          if names.get(athlete_id)]

    _depth_cache[team] = ranked
    return ranked


def _team_keep_set(team_rbs, chart, chart_keep=CHART_KEEP,
                   salary_keep=SALARY_KEEP):
    """Normalized names of the team's pool RBs that survive the filter.

    chart_keep: first N chart ranks whose names are actually in the
    pool (IR/Out chart entries don't consume a slot). salary_keep:
    top-N pool RBs by DK salary, chart rank breaking salary ties (a
    chart-listed backup outranks an unlisted one at the same price).
    """
    pool_names = {normalize_name(rb['name']) for rb in team_rbs}
    rank_of = {name: i for i, name in enumerate(chart)}

    kept = []
    for name in chart:                      # arm 1: top chart ranks in pool
        if name in pool_names:
            kept.append(name)
            if len(kept) >= chart_keep:
                break

    by_salary = sorted(                    # arm 2: top DK salaries
        team_rbs,
        key=lambda rb: (-rb.get('salary') or 0,
                        rank_of.get(normalize_name(rb['name']), 10**6),
                        normalize_name(rb['name'])))
    for rb in by_salary[:salary_keep]:
        kept.append(normalize_name(rb['name']))

    return set(kept)


def filter_backup_rbs(pool, allow_scrape=True, chart_keep=CHART_KEEP,
                      salary_keep=SALARY_KEEP):
    """Drop RBs who are neither top chart ranks nor top salaries.

    Only touches entries with 'RB' in positions. A team whose depth
    chart can't be resolved keeps every RB (safety valve — a failed
    fetch must never bench a starter).

    Returns:
        (kept, dropped) pool entries
    """
    rbs_by_team = {}
    for entry in pool:
        if 'RB' in (entry.get('positions') or []):
            rbs_by_team.setdefault(entry.get('team'), []).append(entry)
    if not rbs_by_team:
        return pool, []

    keep_by_team = {}
    for team, team_rbs in rbs_by_team.items():
        chart = rb_depth_chart(team, allow_scrape=allow_scrape)
        if chart:  # unresolved chart -> team unfiltered
            keep_by_team[team] = _team_keep_set(
                team_rbs, chart, chart_keep=chart_keep,
                salary_keep=salary_keep)

    kept, dropped = [], []
    for entry in pool:
        team = entry.get('team')
        if ('RB' not in (entry.get('positions') or [])
                or team not in keep_by_team
                or normalize_name(entry['name']) in keep_by_team[team]):
            kept.append(entry)
        else:
            dropped.append(entry)
    return kept, dropped
