"""Tests for the accuracy-tracking SQLite layer (accuracy_db.py)."""

import json

import pytest

import accuracy_db as db
from projections import normalize_name


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test_accuracy.db'))
    db.init_db()
    return db


def make_pool_player(pid, name, position, team, salary, projection,
                     source='test'):
    return {
        'player_id': pid, 'name': name, 'position': position,
        'positions': position.split('/'), 'team': team, 'salary': salary,
        'projection': projection, 'source': source,
    }


def make_hindsight_lineup():
    return {
        'players': [
            {'name': 'Bryce Young', 'lineup_position': 'QB',
             'positions': ['QB'], 'team': 'CAR', 'salary': 5200,
             'projection': 35.44, 'opponent': None},
            {'name': 'Derrick Henry', 'lineup_position': 'RB',
             'positions': ['RB'], 'team': 'BAL', 'salary': 6700,
             'projection': 38.3, 'opponent': None},
        ],
        'total_projection': 73.74,
        'total_salary': 11900,
    }


class TestHindsightLineups:
    def test_save_and_fetch(self, temp_db):
        temp_db.save_contest(1, 2, 'Week 1 Classic', 'classic', ['KC @ BAL'])
        lineup = make_hindsight_lineup()
        temp_db.save_hindsight_lineup(1, 'classic', lineup, 73.74)
        row = temp_db.get_hindsight_lineup(1)
        assert row['total_actual'] == 73.74
        assert row['total_salary'] == 11900
        assert row['mode'] == 'classic'
        assert row['computed_at']
        assert [p['name'] for p in row['players']] == \
            ['Bryce Young', 'Derrick Henry']
        assert row['players'][0]['actual'] == 35.44

    def test_save_is_upsert(self, temp_db):
        temp_db.save_contest(1, 2, 'Week 1 Classic', 'classic', ['KC @ BAL'])
        temp_db.save_hindsight_lineup(1, 'classic',
                                      make_hindsight_lineup(), 73.74)
        lineup = make_hindsight_lineup()
        lineup['total_projection'] = 99.0
        temp_db.save_hindsight_lineup(1, 'classic', lineup, 99.0)
        rows = temp_db.get_connection().execute(
            'SELECT COUNT(*) AS n FROM hindsight_optimals').fetchone()
        assert rows['n'] == 1  # recomputation replaces, never duplicates
        assert temp_db.get_hindsight_lineup(1)['total_actual'] == 99.0

    def test_missing_contest_returns_none(self, temp_db):
        assert temp_db.get_hindsight_lineup(999) is None

    def test_showdown_shape_saved_with_captain_flag(self, temp_db):
        temp_db.save_contest(7, 8, 'NE @ SEA Showdown', 'showdown',
                             ['NE @ SEA'])
        lineup = {
            'captain': {'name': 'Jaxon Smith-Njigba', 'team': 'SEA',
                        'salary': 10600, 'projection': 29.2},
            'flex': [{'name': 'Drake Maye', 'team': 'NE', 'salary': 10000,
                      'projection': 12.82}],
            'total_projection': 56.62,  # 29.2 x 1.5 + 12.82
            'total_salary': 25900,
        }
        temp_db.save_hindsight_lineup(7, 'showdown', lineup, 56.62)
        row = temp_db.get_hindsight_lineup(7)
        assert row['total_actual'] == 56.62
        captain = next(p for p in row['players'] if p['is_captain'])
        assert captain['name'] == 'Jaxon Smith-Njigba'
        assert captain['actual'] == 29.2  # base points, 1.5x at read time
        flex = next(p for p in row['players'] if not p['is_captain'])
        assert flex['name'] == 'Drake Maye'


class TestSchema:
    def test_init_creates_tables(self, temp_db):
        conn = temp_db.get_connection()
        tables = {r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert {'contests', 'source_projections',
                'lineup_predictions'} <= tables

    def test_init_idempotent(self, temp_db):
        temp_db.init_db()  # second call must not raise


class TestContests:
    def test_save_and_fetch(self, temp_db):
        temp_db.save_contest(123, 456, 'NE @ SEA Showdown', 'showdown',
                             ['NE @ SEA'], starts_at='2026-09-10T00:20:00+00:00')
        row = temp_db.get_contest(123)
        assert row['name'] == 'NE @ SEA Showdown'
        assert row['mode'] == 'showdown'
        assert json.loads(row['slate_json']) == ['NE @ SEA']
        assert row['starts_at'] == '2026-09-10T00:20:00+00:00'

    def test_upsert_updates(self, temp_db):
        temp_db.save_contest(123, 456, 'Old Name', 'showdown', ['NE @ SEA'])
        temp_db.save_contest(123, 456, 'New Name', 'showdown', ['NE @ SEA'])
        row = temp_db.get_contest(123)
        assert row['name'] == 'New Name'
        assert len(temp_db.list_contests()) == 1


class TestSourceProjections:
    def test_save_and_upsert(self, temp_db):
        player = make_pool_player(1, 'Patrick Mahomes', 'QB', 'KC', 8000, 21.5,
                                  source='dff')
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_source_projection(1, 'dff', player)
        temp_db.save_source_projection(1, 'dff',
                                       {**player, 'projection': 22.0})
        conn = temp_db.get_connection()
        rows = conn.execute(
            "SELECT * FROM source_projections WHERE contest_id = 1").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]['projected_fppg'] == 22.0
        assert rows[0]['matched'] == 1
        assert rows[0]['norm_name'] == 'patrick mahomes'

    def test_unmatched_player_flagged(self, temp_db):
        # source label says fallback -> matched = 0
        player = make_pool_player(1, 'Bench Guy', 'WR', 'KC', 1200, 4.5,
                                  source='fallback')
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_source_projection(1, 'fallback', player)
        conn = temp_db.get_connection()
        row = conn.execute("SELECT matched FROM source_projections").fetchone()
        conn.close()
        assert row['matched'] == 0

    def test_dst_normalized_to_team_token(self, temp_db):
        player = make_pool_player(9, 'Patriots DST', 'DST', 'NE', 3000, 8.0)
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        temp_db.save_source_projection(1, 'dff', player)
        conn = temp_db.get_connection()
        row = conn.execute("SELECT norm_name FROM source_projections").fetchone()
        conn.close()
        assert row['norm_name'] == 'patriots'  # matches game_results DST keys

    def test_record_player_actuals_across_sources(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        for source in ('dff', 'fallback'):
            temp_db.save_source_projection(
                1, source, make_pool_player(1, 'Patrick Mahomes', 'QB',
                                            'KC', 8000, 21.5))
        updated = temp_db.record_player_actuals(1, {
            'patrick mahomes': {'fppg': 30.2, 'stats': {'passing': {}}}})
        assert updated == 2

    def test_record_actuals_dst_key(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        temp_db.save_source_projection(
            1, 'dff', make_pool_player(9, 'Patriots DST', 'DST', 'NE',
                                       3000, 8.0))
        updated = temp_db.record_player_actuals(1, {'patriots': {'fppg': 11.0}})
        assert updated == 1


def make_showdown_lineup():
    captain = {'player_id': 1, 'name': 'Patrick Mahomes', 'team': 'KC',
               'salary': 8000, 'projection': 21.5}
    flex = [{'player_id': 2, 'name': 'Rashee Rice', 'team': 'KC',
             'salary': 4800, 'projection': 12.0}]
    return {'captain': captain, 'flex': flex,
            'total_projection': 44.25, 'total_salary': 16800}


class TestLineupPredictions:

    def test_showdown_players_json(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'dff', 'showdown',
                                        make_showdown_lineup())
        players, mode = temp_db.get_lineup_players(1, 'dff')
        assert mode == 'showdown'
        assert players[0]['is_captain'] is True
        assert players[0]['name'] == 'Patrick Mahomes'
        assert players[1]['is_captain'] is False

    def test_classic_players_json(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'classic', ['KC @ BAL'])
        lineup = {'players': [{'name': 'Patrick Mahomes', 'team': 'KC',
                               'salary': 8000}],
                  'total_projection': 21.5, 'total_salary': 8000}
        temp_db.save_lineup_prediction(1, 'csv', 'classic', lineup)
        players, mode = temp_db.get_lineup_players(1, 'csv')
        assert mode == 'classic'
        assert players[0]['is_captain'] is False

    def test_update_lineup_actual_and_accuracy(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'dff', 'showdown',
                                        make_showdown_lineup())
        temp_db.update_lineup_actual(1, 'dff', 41.0)

        accuracy = temp_db.contest_accuracy(1)
        assert len(accuracy) == 1
        assert accuracy[0]['lineup_projected'] == 44.25
        assert accuracy[0]['lineup_actual'] == 41.0
        # no matched players with actuals yet -> player_mae None
        assert accuracy[0]['player_mae'] is None

    def test_player_mae_in_accuracy(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'dff', 'showdown',
                                        make_showdown_lineup())
        temp_db.save_source_projection(
            1, 'dff', make_pool_player(1, 'Patrick Mahomes', 'QB', 'KC',
                                       8000, 21.5, source='dff'))
        temp_db.save_source_projection(
            1, 'dff', make_pool_player(2, 'Rashee Rice', 'WR', 'KC',
                                       4800, 12.0, source='dff'))
        temp_db.update_lineup_actual(1, 'dff', 41.0)
        temp_db.record_player_actuals(1, {
            'patrick mahomes': {'fppg': 23.5},
            'rashee rice': {'fppg': 10.0}})
        # MAE over the two players: |21.5-23.5| = 2.0, |12-10| = 2.0
        accuracy = temp_db.contest_accuracy(1)
        assert accuracy[0]['player_mae'] == pytest.approx(2.0)
        assert accuracy[0]['n_players'] == 2


class TestProjectionSentinel:
    """Expert lineups stored with total_projected=0.0 (no stated
    projection) must not pollute lineup-level error stats."""

    def make_sentinel_lineup(self):
        lineup = make_showdown_lineup()
        lineup['total_projection'] = 0.0
        return lineup

    def test_contest_accuracy_flags_unknown_projection(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'si', 'showdown',
                                        self.make_sentinel_lineup())
        temp_db.update_lineup_actual(1, 'si', 41.0)
        accuracy = temp_db.contest_accuracy(1)
        assert accuracy[0]['projected_known'] is False
        assert accuracy[0]['lineup_actual'] == 41.0

    def test_contest_accuracy_known_projection_flagged(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'dff', 'showdown',
                                        make_showdown_lineup())
        temp_db.update_lineup_actual(1, 'dff', 41.0)
        assert temp_db.contest_accuracy(1)[0]['projected_known'] is True

    def test_summary_splits_sentinel_from_error_stats(self, temp_db,
                                                      capsys):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'dff', 'showdown',
                                        make_showdown_lineup())
        temp_db.save_lineup_prediction(1, 'si', 'showdown',
                                        self.make_sentinel_lineup())
        temp_db.update_lineup_actual(1, 'dff', 41.0)
        temp_db.update_lineup_actual(1, 'si', 41.0)

        temp_db.display_accuracy_summary()
        out = capsys.readouterr().out
        assert 'LINEUP LEVEL' in out
        assert 'EXPERT LINEUPS' in out
        # the projected source sits in the error table, the sentinel only
        # in the actual-only table
        head, _, tail = out.partition('EXPERT LINEUPS')
        assert 'dff' in head
        assert 'si' not in head
        assert 'si' in tail

    def test_empty_summary_with_only_sentinel_rows(self, temp_db, capsys):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'si', 'showdown',
                                        self.make_sentinel_lineup())
        temp_db.update_lineup_actual(1, 'si', 41.0)
        temp_db.display_accuracy_summary()
        out = capsys.readouterr().out
        assert 'EXPERT LINEUPS' in out
        assert 'No scored contests' not in out


class TestDisplays:
    def test_history_and_summary_empty(self, temp_db, capsys):
        temp_db.display_history()
        temp_db.display_accuracy_summary()
        out = capsys.readouterr().out
        assert 'No contests saved yet' in out
        assert 'No scored contests yet' in out

    def test_summary_after_scoring(self, temp_db, capsys):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['KC @ BAL'])
        temp_db.save_lineup_prediction(1, 'dff', 'showdown',
                                       make_showdown_lineup())
        temp_db.save_source_projection(
            1, 'dff', make_pool_player(1, 'Patrick Mahomes', 'QB', 'KC',
                                       8000, 21.5, source='dff'))
        temp_db.update_lineup_actual(1, 'dff', 41.0)
        temp_db.record_player_actuals(1, {'patrick mahomes': {'fppg': 23.5}})

        temp_db.display_accuracy_summary()
        out = capsys.readouterr().out
        assert 'LINEUP LEVEL' in out
        assert 'PLAYER LEVEL' in out
        assert 'dff' in out