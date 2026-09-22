"""Tests for DB-driven per-position calibration (calibration.py)."""

import pytest

import accuracy_db as db
import calibration
from calibration import apply_corrections, load_corrections, position_biases


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    return db


def seed_row(temp_db, contest_id, source, pid, name, position, team,
             projected, actual, matched):
    temp_db.save_source_projection(contest_id, source, {
        'player_id': pid, 'name': name, 'position': position,
        'positions': [position], 'team': team, 'salary': 5000,
        'projection': projected,
        'source': source if matched else 'fallback',
    })
    conn = temp_db.get_connection()
    conn.execute("UPDATE source_projections SET actual_fppg = ? "
                 "WHERE contest_id = ? AND source = ? AND player_id = ?",
                 (actual, contest_id, source, str(pid)))
    conn.commit()
    conn.close()


class TestPositionBiases:
    def test_bias_on_matched_rows_only(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        # Matched TE row: actual 12 - projected 10 = +2 (under-projected)
        seed_row(temp_db, 1, 'dff', 1, 'Travis Kelce', 'TE', 'KC', 10.0, 12.0,
                 matched=True)
        # Fallback-filled row inside the dff source (unmatched): its error
        # must NOT pollute dff's bias
        seed_row(temp_db, 1, 'dff', 2, 'No Board TE', 'TE', 'KC', 9.0, 20.0,
                 matched=False)

        biases = position_biases()
        assert biases['dff']['TE']['bias'] == pytest.approx(2.0)
        assert biases['dff']['TE']['n'] == 1

    def test_no_db_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'missing.db'))
        assert position_biases() == {}

    def test_ungraded_rows_ignored(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        seed_row(temp_db, 1, 'dff', 1, 'Travis Kelce', 'TE', 'KC', 10.0, 12.0,
                 matched=True)
        # A second contest with no actuals recorded yet
        temp_db.save_contest(2, 3, 'test2', 'showdown', ['IND @ KC'])
        seed_row(temp_db, 2, 'dff', 1, 'Travis Kelce', 'TE', 'KC', 11.0, None,
                 matched=True)
        biases = position_biases()
        assert biases['dff']['TE']['n'] == 1


class TestLoadCorrections:
    def test_min_n_gate(self, temp_db, capsys):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        # 2 graded rows: below the default gate -> no correction, but listed
        for i in range(2):
            seed_row(temp_db, 1, 'dff', i, f'WR {i}', 'WR', 'KC', 10.0, 12.0,
                     matched=True)
        corrections = load_corrections()
        assert corrections == {}
        assert 'skipped' in capsys.readouterr().out

    def test_qualified_positions_returned(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        for i in range(30):
            seed_row(temp_db, 1, 'dff', i, f'WR {i}', 'WR', 'KC', 10.0, 11.0,
                     matched=True)
        corrections = load_corrections()
        assert corrections == {'dff': {'WR': 1.0}}

    def test_lowered_gate(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        for i in range(2):
            seed_row(temp_db, 1, 'dff', i, f'WR {i}', 'WR', 'KC', 10.0, 12.0,
                     matched=True)
        assert load_corrections(min_n=2) == {'dff': {'WR': 2.0}}


class TestApplyCorrections:
    def players(self):
        return [
            {'player_id': 1, 'name': 'Patrick Mahomes', 'position': 'QB',
             'positions': ['QB'], 'team': 'KC'},
            {'player_id': 2, 'name': 'Travis Kelce', 'position': 'TE',
             'positions': ['TE'], 'team': 'KC'},
            {'player_id': 3, 'name': 'Ghost WR', 'position': 'WR',
             'positions': ['WR'], 'team': 'KC'},
        ]

    def test_correction_by_rows_own_source(self):
        players = self.players()
        attached = {
            1: {'projection': 20.0, 'source': 'dailyfantasyfuel'},  # matched
            2: {'projection': 10.0, 'source': 'dailyfantasyfuel'},  # matched
            3: {'projection': 8.0, 'source': 'fallback'},  # salary-curve miss
        }
        corrections = {'dailyfantasyfuel': {'QB': -1.0, 'TE': 0.9},
                       'fallback': {'WR': -2.0}}
        corrected = apply_corrections(attached, players, corrections)

        assert corrected == 3
        assert attached[1]['projection'] == 19.0
        assert attached[2]['projection'] == 10.9
        assert attached[3]['projection'] == 6.0

    def test_rows_without_history_untouched(self):
        players = self.players()
        attached = {
            1: {'projection': 20.0, 'source': 'kicker_model'},
            2: {'projection': 10.0, 'source': 'csv'},
            3: {'projection': 8.0, 'source': 'dailyfantasyfuel'},
        }
        corrections = {'dailyfantasyfuel': {'QB': 5.0}}  # no TE, no others
        assert apply_corrections(attached, players, corrections) == 0
        assert all(v['projection'] for v in attached.values())

    def test_empty_corrections_noop(self):
        players = self.players()
        attached = {1: {'projection': 20.0, 'source': 'x'},
                    2: {'projection': 10.0, 'source': 'x'},
                    3: {'projection': 8.0, 'source': 'x'}}
        assert apply_corrections(attached, players, {}) == 0
        assert attached[1]['projection'] == 20.0