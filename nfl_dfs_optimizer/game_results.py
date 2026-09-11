"""
Fetch actual NFL game results and compute actual DK fantasy points.

Primary source: ESPN's hidden site API (JSON, no auth, stable for years):
  scoreboard: .../nfl/scoreboard?dates=YYYYMMDD   -> events for that date
  summary:    .../nfl/summary?event={event_id}     -> full box score

Offensive DK points are computed with nfl_scoring.calculate_offensive_points
from the label-indexed stat arrays (passing/rushing/receiving/fumbles groups);
DST points with nfl_scoring.DSTScoring from team totals + opponent score;
kicker points with nfl_scoring.calculate_kicker_points from the scoring-plays
list (the box-score 'kicking' group only carries aggregates, but DK points
depend on FIELD GOAL DISTANCE, which every scoring play spells out:
"Jason Myers 30 Yd Field Goal", "(Andy Borregales Kick)" for extra points).

Known approximations (ESPN team totals lack them): blocked kicks, safeties,
and DST extra-point/two-point returns are scored as 0, as are player-level
two-point conversions (rare; counted against nobody, flagged as approximate).
"""

import re

import requests

from nfl_scoring import (calculate_kicker_points, calculate_offensive_points,
                         DSTScoring)
from projections import normalize_name, normalize_dst_name

ESPN_SCOREBOARD_URL = ("https://site.api.espn.com/apis/site/v2/"
                       "sports/football/nfl/scoreboard")
ESPN_SUMMARY_URL = ("https://site.api.espn.com/apis/site/v2/"
                    "sports/football/nfl/summary")

# Box-score groups and the labels the parser reads from each. Stats are
# indexed BY LABEL NAME, not position: ESPN appends columns after games go
# final ('QBR' appeared in the passing row the day after week 1, 2026-09),
# and a positional prefix check silently zeroed whole groups. A group is
# only skipped when a REQUIRED label disappears entirely.
GROUP_REQUIRED_LABELS = {
    'passing': ['C/ATT', 'YDS', 'TD', 'INT'],
    'rushing': ['YDS', 'TD'],
    'receiving': ['REC', 'YDS', 'TD'],
    'fumbles': ['LOST'],
    # Return groups carry no DK yardage points, but the TD column does
    # (6 pts) — and parsing them gives return-only players an explicit
    # 0-point row so "no recorded actual" means truly not in the box
    'kickReturns': ['NO', 'TD'],
    'puntReturns': ['NO', 'TD'],
}


def _fetch_json(url, params, timeout=20):
    """Fetch a URL, returning parsed JSON or None on any failure."""
    try:
        response = requests.get(url, params=params, timeout=timeout,
                               headers={'User-Agent': 'Mozilla/5.0'})
        if response.status_code == 200:
            return response.json()
        print(f"    {url} returned HTTP {response.status_code}")
    except (requests.RequestException, ValueError) as e:
        print(f"    {url} failed: {e}")
    return None


def parse_game_string(game):
    """Split a DK game string into (away, home) abbreviations.

    'NE @ SEA' -> ('NE', 'SEA'); 'NE vs. SEA' -> ('NE', 'SEA')
    """
    if not game:
        return None, None
    if '@' in game:
        away, home = game.split('@', 1)
    elif 'vs' in game.lower():
        away, home = re.split(r'\s+vs\.?\s+', game, flags=re.IGNORECASE)
    else:
        return None, None
    return away.strip().upper(), home.strip().upper()


def fetch_scoreboard(date_compact):
    """Fetch NFL events for one date from the ESPN scoreboard.

    Args:
        date_compact: 'YYYYMMDD'

    Returns:
        List of dicts {event_id, away, home, away_score, home_score}
    """
    data = _fetch_json(ESPN_SCOREBOARD_URL, {'dates': date_compact})
    if not data:
        return []

    events = []
    for event in data.get('events', []):
        competition = (event.get('competitions') or [{}])[0]
        away = home = None
        away_score = home_score = None
        for competitor in competition.get('competitors', []):
            team = competitor.get('team', {}).get('abbreviation')
            if competitor.get('homeAway') == 'away':
                away, away_score = team, competitor.get('score')
            else:
                home, home_score = team, competitor.get('score')
        # ESPN status: pre / in / post — only 'post' games have final stats
        status = ((event.get('status') or {}).get('type') or {}).get('state')
        if away and home:
            events.append({'event_id': event['id'], 'away': away,
                           'home': home,
                           'away_score': _to_float(away_score),
                           'home_score': _to_float(home_score),
                           'completed': status == 'post'})
    return events


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stat_number(stats, index):
    """Parse one label-indexed stat cell ('128', '15/19', '-') to float."""
    if index >= len(stats) or stats[index] in ('-', '--', '', None):
        return 0.0
    # Compound cells like '15/19' or '0-0': take the first number for
    # C/ATT; sack-yards '2-13' is handled separately (never read here)
    text = str(stats[index])
    if '/' in text or '-' in text:
        text = re.split(r'[/-]', text)[0]
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_player_stats(summary):
    """Extract offensive player stat lines -> DK points.

    Merges the passing/rushing/receiving/fumbles groups per athlete and
    computes DK points with nfl_scoring.calculate_offensive_points. Stat
    cells are looked up by label name, so columns ESPN adds or reorders
    (the passing row gained 'QBR' post-game) cannot shift the reads.

    Returns:
        Dict {norm_name: {'fppg', 'team', 'stats'}} (normalized names)
    """
    players = {}
    for side in (summary.get('boxscore', {}).get('players') or []):
        team_abbr = side.get('team', {}).get('abbreviation')
        for group in side.get('statistics', []):
            name = group.get('name')
            if name not in GROUP_REQUIRED_LABELS:
                continue
            labels = group.get('labels', [])
            missing = [lbl for lbl in GROUP_REQUIRED_LABELS[name]
                       if lbl not in labels]
            if missing:
                # Required label gone — skip rather than guess positions
                print(f"    ESPN '{name}' labels changed: {labels} "
                      f"(missing {missing}) — skipped")
                continue
            col = {lbl: labels.index(lbl) for lbl in
                   GROUP_REQUIRED_LABELS[name]}
            for entry in group.get('athletes', []):
                athlete = entry.get('athlete') or {}
                display_name = athlete.get('displayName')
                stats = entry.get('stats') or []
                if not display_name or not stats:
                    continue

                record = players.setdefault(
                    athlete.get('id'),
                    {'name': display_name, 'team': team_abbr,
                     'stats': {}})
                record['stats'].setdefault(name, {})

                if name == 'passing':
                    record['stats']['passing'] = {
                        'yards': _stat_number(stats, col['YDS']),
                        'tds': _stat_number(stats, col['TD']),
                        'interceptions': _stat_number(stats, col['INT']),
                    }
                elif name == 'rushing':
                    record['stats']['rushing'] = {
                        'yards': _stat_number(stats, col['YDS']),
                        'tds': _stat_number(stats, col['TD']),
                    }
                elif name == 'receiving':
                    record['stats']['receiving'] = {
                        'receptions': _stat_number(stats, col['REC']),
                        'yards': _stat_number(stats, col['YDS']),
                        'tds': _stat_number(stats, col['TD']),
                    }
                elif name == 'fumbles':
                    record['stats']['fumbles'] = {
                        'lost': _stat_number(stats, col['LOST']),
                    }
                elif name in ('kickReturns', 'puntReturns'):
                    record['stats'][name] = {
                        'touchdowns': _stat_number(stats, col['TD']),
                    }

    results = {}
    for record in players.values():
        passing = record['stats'].get('passing', {})
        rushing = record['stats'].get('rushing', {})
        receiving = record['stats'].get('receiving', {})
        fumbles = record['stats'].get('fumbles', {})
        kick_returns = record['stats'].get('kickReturns', {})
        punt_returns = record['stats'].get('puntReturns', {})
        fppg = calculate_offensive_points(
            pass_yards=passing.get('yards', 0),
            pass_tds=passing.get('tds', 0),
            interceptions=passing.get('interceptions', 0),
            rush_yards=rushing.get('yards', 0),
            rush_tds=rushing.get('tds', 0),
            receptions=receiving.get('receptions', 0),
            rec_yards=receiving.get('yards', 0),
            rec_tds=receiving.get('tds', 0),
            fumbles_lost=fumbles.get('lost', 0),
            return_tds=(kick_returns.get('touchdowns', 0)
                        + punt_returns.get('touchdowns', 0)),
        )
        key = normalize_name(record['name'])
        if key:
            results[key] = {'fppg': fppg, 'team': record['team'],
                            'stats': record['stats']}
    return results


# Scoring-play text patterns (verified 2026-09 on a live summary):
# "Andy Borregales 50 Yd Field Goal", "Eli Raridon 2 Yd pass from Drake
# Maye (Andy Borregales Kick)". Made FGs and XPs are always scoring plays,
# so kicker points are exact — no distance aggregates needed.
FG_PLAY = re.compile(r'^(.+?) (\d{1,3}) Yd Field Goal$')
XP_PLAY = re.compile(r'\(([^()]+) Kick\)$')


def parse_kicker_points(summary):
    """Compute each kicker's DK points from the scoring-plays list.

    Kicker names/teams come from the box-score 'kicking' group (so a kicker
    who made nothing still gets a 0-point row — they played); points come
    from summary['scoringPlays'], which carries every made field goal with
    its distance and every extra point by name.

    Returns:
        Dict {norm_name: {'fppg', 'team', 'stats'}} (kickers only)
    """
    kickers = {}
    for side in (summary.get('boxscore', {}).get('players') or []):
        team_abbr = side.get('team', {}).get('abbreviation')
        for group in side.get('statistics', []):
            if group.get('name') != 'kicking':
                continue
            for entry in group.get('athletes', []):
                name = (entry.get('athlete') or {}).get('displayName')
                key = normalize_name(name) if name else ''
                if key:
                    kickers.setdefault(
                        key, {'name': name, 'team': team_abbr,
                              'field_goals': [], 'extra_points': 0})

    for play in summary.get('scoringPlays') or []:
        text = (play.get('text') or '').strip()
        m = FG_PLAY.match(text)
        if m:
            key = normalize_name(m.group(1))
            if key in kickers:
                kickers[key]['field_goals'].append(int(m.group(2)))
            continue
        m = XP_PLAY.search(text)
        if m:
            key = normalize_name(m.group(1))
            if key in kickers:
                kickers[key]['extra_points'] += 1

    results = {}
    for key, rec in kickers.items():
        results[key] = {
            'fppg': calculate_kicker_points(rec['field_goals'],
                                             rec['extra_points']),
            'team': rec['team'],
            'stats': {'field_goals': sorted(rec['field_goals']),
                      'extra_points': rec['extra_points']},
        }
    return results


def parse_dst_points(summary):
    """Compute each team's DST DK points from team totals + opponent score.

    Sacks/INTs/fumble recoveries come from the OPPONENT's offensive totals
    (sacks they allowed, INTs they threw, fumbles they lost); points allowed
    from the opponent's final score.

    Returns:
        Dict {norm_dst_token: {'fppg', 'team', 'stats'}} where the key is the
        mascot token ('patriots') matching normalize_dst_name output.
    """
    teams = summary.get('boxscore', {}).get('teams') or []
    totals = {}
    scores = {}
    display_names = {}
    for side in teams:
        abbr = side.get('team', {}).get('abbreviation')
        display_names[abbr] = side.get('team', {}).get('displayName', '')
        for stat in side.get('statistics', []):
            totals[(abbr, stat.get('name'))] = stat.get('displayValue')

    header = summary.get('header', {})
    for competition in header.get('competitions', []):
        for competitor in competition.get('competitors', []):
            abbr = competitor.get('team', {}).get('abbreviation')
            raw_score = competitor.get('score')
            # '' / missing = pre-game: no points-allowed data at all
            # (never coerce to 0.0 — a real 0-0 game reports score '0')
            scores[abbr] = None if raw_score in (None, '') else \
                _to_float(raw_score)

    results = {}
    for abbr, display_name in display_names.items():
        opponent = next((o for o in display_names if o != abbr), None)
        if opponent is None:
            continue

        # Sacks the OPPONENT's offense allowed ('2-13' -> 2 sacks)
        opp_sacks = _stat_number(
            [totals.get((opponent, 'sacksYardsLost'), '0')], 0)
        opp_ints = _stat_number(
            [totals.get((opponent, 'interceptions'), '0')], 0)
        opp_fumbles_lost = _stat_number(
            [totals.get((opponent, 'fumblesLost'), '0')], 0)
        def_tds = _stat_number(
            [totals.get((abbr, 'defensiveTouchdowns'), '0')], 0)
        points_allowed = scores.get(opponent)

        # No opponent score = the game hasn't been played (or the box score
        # isn't populated) — never emit a fake 0-point DST line
        if points_allowed is None:
            continue

        dst = DSTScoring()
        fppg = (opp_sacks * dst.sack
                + opp_ints * dst.interception
                + opp_fumbles_lost * dst.fumble_recovery
                + def_tds * dst.touchdown)
        if points_allowed is not None:
            fppg += DSTScoring.points_allowed_bonus(int(points_allowed))

        key = normalize_dst_name(display_name)
        if key:
            results[key] = {
                'fppg': fppg, 'team': abbr,
                'stats': {'sacks': opp_sacks, 'interceptions': opp_ints,
                          'fumble_recoveries': opp_fumbles_lost,
                          'touchdowns': def_tds,
                          'points_allowed': points_allowed},
            }
    return results


def fetch_summary(event_id):
    """Fetch the ESPN summary JSON for one event, or None."""
    return _fetch_json(ESPN_SUMMARY_URL, {'event': event_id})


def fetch_slate_results(date_compact, games):
    """Fetch actual results for every game on a slate.

    Args:
        date_compact: 'YYYYMMDD' — ESPN scoreboard date for the slate
        games: Game strings from the contest's draftables ('NE @ SEA')

    Returns:
        Dict {norm_name or dst_token: {'fppg', 'team', 'stats'}}
    """
    scoreboard = fetch_scoreboard(date_compact)
    if not scoreboard:
        print("  No ESPN scoreboard events found for that date")
        return {}

    by_team = {}
    for event in scoreboard:
        by_team[event['away']] = event
        by_team[event['home']] = event

    wanted = {}
    for game in games:
        away, home = parse_game_string(game)
        event = by_team.get(away) or by_team.get(home)
        if event is None:
            print(f"  No ESPN event found for {game!r} on {date_compact}")
        elif not event['completed']:
            print(f"  {game!r} has not finished yet (ESPN state not final) "
                  f"- skipped")
        else:
            wanted[event['event_id']] = event

    results = {}
    for event_id in wanted:
        summary = fetch_summary(event_id)
        if not summary:
            continue
        results.update(parse_player_stats(summary))
        results.update(parse_kicker_points(summary))
        results.update(parse_dst_points(summary))
        print(f"  {wanted[event_id]['away']} @ {wanted[event_id]['home']}: "
              f"{len(results)} players/DSTs accumulated")

    return results