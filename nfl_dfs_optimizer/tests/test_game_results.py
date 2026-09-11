"""Tests for actual-result fetching + DK scoring (game_results.py)."""

import json
import os

from game_results import (_stat_number, parse_game_string, parse_dst_points,
                          parse_kicker_points, parse_player_stats)

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                       'espn_summary.json')


def load_summary():
    with open(FIXTURE, encoding='utf-8') as f:
        return json.load(f)


class TestHelpers:
    def test_stat_number_plain(self):
        assert _stat_number(['128'], 0) == 128.0

    def test_stat_number_compound_slash(self):
        assert _stat_number(['15/19'], 0) == 15.0

    def test_stat_number_compound_dash(self):
        # sack-yards cell '2-13' -> first number
        assert _stat_number(['2-13'], 0) == 2.0

    def test_stat_number_missing(self):
        assert _stat_number(['-', '--', ''], 0) == 0.0
        assert _stat_number([], 3) == 0.0

    def test_parse_game_string(self):
        assert parse_game_string('NE @ SEA') == ('NE', 'SEA')
        assert parse_game_string('KC vs. BAL') == ('KC', 'BAL')
        assert parse_game_string(None) == (None, None)


class TestParsePlayerStats:
    def test_qb_passing_line(self):
        # Josh Dobbs: 15/19, 128 yds, 2 TD, 0 INT (frozen preseason fixture)
        # 128/25 + 2*4 = 13.12 DK points
        players = parse_player_stats(load_summary())
        assert players['joshua dobbs']['fppg'] == 13.12
        assert players['joshua dobbs']['team'] == 'DET'

    def test_receiving_line(self):
        # Lucky Jackson: 5 rec, 94 yds -> 5 + 9.4 = 14.4 DK points
        players = parse_player_stats(load_summary())
        assert players['lucky jackson']['fppg'] == 14.4

    def test_rush_reception_merge(self):
        # Trayveon Williams: 57 rush yds, 2 rush TD, 1 rec 2 yds, 1 fum lost
        # 5.7 + 12 + 1 + 0.2 - 1 = 17.9
        players = parse_player_stats(load_summary())
        assert players['trayveon williams']['fppg'] == 17.9

    def test_every_player_nonnegative(self):
        players = parse_player_stats(load_summary())
        assert len(players) > 20
        assert all(v['fppg'] >= 0 for v in players.values())

    def test_appended_column_does_not_skip_group(self):
        # Live 2026-09-09: ESPN inserted 'QBR' into the passing row after
        # the game went final; a positional prefix check silently zeroed
        # every QB's passing stats. Label-name indexing must survive it.
        summary = load_summary()
        for side in summary['boxscore']['players']:
            for group in side.get('statistics', []):
                if group.get('name') == 'passing':
                    group['labels'] = ['C/ATT', 'YDS', 'AVG', 'TD', 'INT',
                                       'SACKS', 'QBR', 'RTG']
                    for entry in group.get('athletes', []):
                        stats = entry['stats']
                        entry['stats'] = stats[:6] + ['55.0'] + stats[6:]
        players = parse_player_stats(summary)
        assert players['joshua dobbs']['fppg'] == 13.12  # unchanged

    def test_reordered_columns_still_read(self):
        # Column ORDER can change too — reads are by label name
        summary = load_summary()
        for side in summary['boxscore']['players']:
            for group in side.get('statistics', []):
                if group.get('name') == 'receiving':
                    group['labels'] = ['TGTS', 'REC', 'YDS', 'AVG', 'TD',
                                       'LONG']
                    for entry in group.get('athletes', []):
                        stats = entry['stats']
                        entry['stats'] = [stats[5], stats[0], stats[1],
                                          stats[2], stats[3], stats[4]]
        players = parse_player_stats(summary)
        assert players['lucky jackson']['fppg'] == 14.4  # unchanged


class TestKickerScoring:
    def test_field_goal_distance_tiers(self):
        from nfl_scoring import calculate_kicker_points
        assert calculate_kicker_points([30]) == 3.0   # 30-39 yd FG
        assert calculate_kicker_points([45]) == 4.0   # 40-49 yd FG
        assert calculate_kicker_points([52]) == 5.0   # 50-59 yd FG
        assert calculate_kicker_points([62]) == 6.0   # 60+ yd FG

    def test_extra_points_and_bonus(self):
        from nfl_scoring import calculate_kicker_points
        # 2 FGs (3+4) + 3 XPs = 10.0; the 3rd FG adds the 3+ FG bonus
        assert calculate_kicker_points([30, 45], 3) == 10.0
        assert calculate_kicker_points([30, 45, 52], 3) == 18.0

    def test_no_makes_is_zero(self):
        from nfl_scoring import calculate_kicker_points
        assert calculate_kicker_points([], 0) == 0.0


class TestParseKickerPoints:
    """Kicker actuals from ESPN scoring plays (live-verified 2026-09-09).

    NE@SEA week 1: Borregales 50-yd FG + 1 XP; Myers FGs from 30 and 26
    + 1 XP; JSN's TD was a Lock pass with the Myers XP attached.
    """
    SUMMARY = {
        'boxscore': {'players': [
            {'team': {'abbreviation': 'NE'}, 'statistics': [
                {'name': 'kicking', 'labels': [], 'athletes': [
                    {'athlete': {'displayName': 'Andy Borregales'},
                     'stats': ['1/1', '100.0', '50', '1/1', '4']}]},
            ]},
            {'team': {'abbreviation': 'SEA'}, 'statistics': [
                {'name': 'kicking', 'labels': [], 'athletes': [
                    {'athlete': {'displayName': 'Jason Myers'},
                     'stats': ['2/2', '100.0', '30', '1/1', '7']}]},
            ]},
        ]},
        'scoringPlays': [
            {'text': 'Eli Raridon 2 Yd pass from Drake Maye '
                     '(Andy Borregales Kick)'},
            {'text': 'Andy Borregales 50 Yd Field Goal'},
            {'text': 'Jason Myers 30 Yd Field Goal'},
            {'text': 'Jaxon Smith-Njigba 45 Yd pass from Drew Lock '
                     '(Jason Myers Kick)'},
            {'text': 'Jason Myers 26 Yd Field Goal'},
        ],
    }

    def test_distance_scored_kickers(self):
        kickers = parse_kicker_points(self.SUMMARY)
        # Borregales: 50-yd FG (5) + 1 XP = 6.0
        assert kickers['andy borregales']['fppg'] == 6.0
        assert kickers['andy borregales']['team'] == 'NE'
        assert kickers['andy borregales']['stats']['field_goals'] == [50]
        # Myers: 3 + 3 + 1 XP = 7.0
        assert kickers['jason myers']['fppg'] == 7.0
        assert kickers['jason myers']['stats']['field_goals'] == [26, 30]
        assert kickers['jason myers']['stats']['extra_points'] == 1

    def test_kicker_with_no_makes_gets_zero_row(self):
        # Played (box category) but made nothing: an exact 0, not a missing
        # row — otherwise the tracker counts them as a mismatch penalty
        summary = {'boxscore': {'players': [
            {'team': {'abbreviation': 'NE'}, 'statistics': [
                {'name': 'kicking', 'labels': [], 'athletes': [
                    {'athlete': {'displayName': 'Blank Kicker'},
                     'stats': ['0/1', '0.0', '-', '0/0', '0']}]}]}]},
            'scoringPlays': []}
        kickers = parse_kicker_points(summary)
        assert kickers['blank kicker']['fppg'] == 0.0

    def test_scoring_plays_for_other_players_ignored(self):
        # TD passes: scorer gets nothing, only the kicker's XP counts
        summary = {'boxscore': {'players': [
            {'team': {'abbreviation': 'NE'}, 'statistics': [
                {'name': 'kicking', 'labels': [], 'athletes': [
                    {'athlete': {'displayName': 'Andy Borregales'},
                     'stats': ['0/0', '0.0', '-', '1/1', '1']}]}]}]},
            'scoringPlays': [
                {'text': 'Eli Raridon 2 Yd pass from Drake Maye '
                         '(Andy Borregales Kick)'}]}
        kickers = parse_kicker_points(summary)
        assert 'eli raridon' not in kickers
        assert kickers['andy borregales']['fppg'] == 1.0

    def test_empty_summary_returns_empty(self):
        assert parse_kicker_points({}) == {}
        assert parse_kicker_points({'boxscore': {}, 'scoringPlays': []}) == {}


class TestReturnStats:
    """Kick/punt return groups: TDs score 6; return-only players get 0 rows."""

    SUMMARY = {'boxscore': {'players': [
        {'team': {'abbreviation': 'SEA'}, 'statistics': [
            {'name': 'kickReturns',
             'labels': ['NO', 'YDS', 'AVG', 'LONG', 'TD'],
             'athletes': [
                 {'athlete': {'displayName': 'DeeJay Dallas', 'id': 1},
                  'stats': ['3', '98', '32.7', '45', '1']},
                 {'athlete': {'displayName': 'Plain Returner', 'id': 2},
                  'stats': ['2', '17', '8.5', '13', '0']},
             ]},
        ]},
    ]}}

    def test_return_td_scores_six(self):
        players = parse_player_stats(self.SUMMARY)
        assert players['deejay dallas']['fppg'] == 6.0
        assert players['deejay dallas']['stats']['kickReturns']['touchdowns'] == 1.0

    def test_return_only_player_gets_explicit_zero_row(self):
        # Played, no scoring stats: an exact 0 row, not a missing row —
        # otherwise "no recorded actual" can't distinguish a scratch from
        # a name-matching miss
        players = parse_player_stats(self.SUMMARY)
        assert players['plain returner']['fppg'] == 0.0

    def test_return_yardage_scores_nothing(self):
        # 98 return yards, no TD -> 0 DK points (no yardage scoring)
        summary = {'boxscore': {'players': [
            {'team': {'abbreviation': 'SEA'}, 'statistics': [
                {'name': 'puntReturns',
                 'labels': ['NO', 'YDS', 'AVG', 'LONG', 'TD'],
                 'athletes': [
                     {'athlete': {'displayName': 'Yardage Only', 'id': 3},
                      'stats': ['5', '98', '19.6', '40', '0']}]},
            ]},
        ]}}
        assert parse_player_stats(summary)['yardage only']['fppg'] == 0.0


class TestParseDSTPoints:
    def test_both_teams(self):
        # Frozen fixture: DET 25 - IND 16
        # Lions DST: 2 sacks (IND allowed 2), 1 INT (IND threw 1), 16 PA
        #   -> 2 + 2 + 1.0 (14-20 tier) = 5.0
        # Colts DST: 2 sacks (DET allowed 2), 1 INT, 3 fum rec, 25 PA
        #   -> 2 + 2 + 6 + 0.0 (21-27 tier) = 10.0
        dst = parse_dst_points(load_summary())
        assert dst['lions']['fppg'] == 5.0
        assert dst['colts']['fppg'] == 10.0
        assert dst['colts']['stats']['points_allowed'] == 25.0

    def test_dst_key_matches_dk_normalization(self):
        # Keys must match normalize_dst_name of DK DST names
        dst = parse_dst_points(load_summary())
        from projections import normalize_dst_name
        assert normalize_dst_name('Lions DST') in dst
        assert normalize_dst_name('Indianapolis Colts DST') in dst

    def test_label_shift_skips_group(self, capsys):
        # If ESPN shifts column labels, the group must be skipped loudly,
        # never misread
        summary = load_summary()
        for side in summary['boxscore']['players']:
            for group in side.get('statistics', []):
                if group.get('name') == 'passing':
                    group['labels'] = ['ATT', 'C', 'YDS', 'TD', 'INT', 'S', 'RTG']
        players = parse_player_stats(summary)
        assert all('passing' not in v['stats'] for v in players.values())
        assert 'labels changed' in capsys.readouterr().out

    def test_empty_summary_returns_empty(self):
        assert parse_dst_points({}) == {}

class TestSlateResults:
    def test_unfinished_game_skipped(self, monkeypatch, capsys):
        from game_results import fetch_slate_results
        monkeypatch.setattr('game_results.fetch_scoreboard', lambda d: [{
            'event_id': 1, 'away': 'NE', 'home': 'SEA',
            'away_score': None, 'home_score': None, 'completed': False}])
        results = fetch_slate_results('20260909', ['NE @ SEA'])
        assert results == {}
        assert 'has not finished' in capsys.readouterr().out

    def test_unknown_game_skipped(self, monkeypatch, capsys):
        from game_results import fetch_slate_results
        monkeypatch.setattr('game_results.fetch_scoreboard', lambda d: [{
            'event_id': 9, 'away': 'KC', 'home': 'BAL',
            'away_score': 20, 'home_score': 17, 'completed': True}])
        results = fetch_slate_results('20260909', ['NE @ SEA'])
        assert results == {}
        assert 'No ESPN event found' in capsys.readouterr().out

    def test_empty_scoreboard_skipped(self, monkeypatch, capsys):
        from game_results import fetch_slate_results
        monkeypatch.setattr('game_results.fetch_scoreboard', lambda d: [])
        results = fetch_slate_results('20260909', ['NE @ SEA'])
        assert results == {}
        assert 'No ESPN scoreboard events' in capsys.readouterr().out

    def test_completed_game_parsed(self, monkeypatch):
        import game_results
        monkeypatch.setattr(game_results, 'fetch_scoreboard', lambda d: [{
            'event_id': 401873308, 'away': 'DET', 'home': 'IND',
            'away_score': 25, 'home_score': 16, 'completed': True}])
        monkeypatch.setattr(game_results, 'fetch_summary',
                            lambda eid: load_summary())
        results = game_results.fetch_slate_results('20260829', ['DET @ IND'])
        assert results['joshua dobbs']['fppg'] == 13.12
        assert results['lions']['fppg'] == 5.0
        assert results['colts']['fppg'] == 10.0

    def test_pregame_summary_yields_no_dst(self):
        # A pre-game summary has no team scores: DST entries must not be
        # fabricated from empty stats
        summary = load_summary()
        for side in summary['boxscore']['teams']:
            for stat in side.get('statistics', []):
                stat['displayValue'] = None
        for competition in summary['header'].get('competitions', []):
            for competitor in competition.get('competitors', []):
                competitor['score'] = ''
        assert parse_dst_points(summary) == {}
