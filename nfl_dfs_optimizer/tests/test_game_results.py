"""Tests for actual-result fetching + DK scoring (game_results.py)."""

import json
import os

from game_results import (_stat_number, parse_game_string, parse_dst_points,
                          parse_player_stats)

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
