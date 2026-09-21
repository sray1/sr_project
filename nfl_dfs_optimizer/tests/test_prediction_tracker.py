"""Tests for the accuracy tracker (prediction_tracker.py), network mocked."""

import json

import pytest

import accuracy_db as db
import prediction_tracker as tracker


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    return db


def make_draftable(pid, name, position, team, salary):
    return {'player_id': pid, 'name': name, 'position': position,
            'positions': position.split('/'), 'salary': salary, 'team': team,
            'game': 'NE @ SEA', 'game_start': None, 'is_disabled': False}


class FakeContest:
    contest_id = 98765
    draft_group_id = 151820
    name = 'NE @ SEA Showdown'
    starts_at = None  # set per-test


class TestLineupNormName:
    def test_dst(self):
        assert tracker._lineup_norm_name('Patriots DST') == 'patriots'

    def test_offense(self):
        assert tracker._lineup_norm_name('Ja\'Mar Chasey') == 'ja mar chasey'


class TestETDate:
    def test_utc_to_et(self):
        # 2026-09-10 00:20 UTC is 2026-09-09 in ET
        assert tracker._et_date_compact('2026-09-10T00:20:00+00:00') == '20260909'

    def test_none(self):
        assert tracker._et_date_compact(None) is None


class TestScoreOneContest:
    def _save_fixture(self, temp_db):
        """Contest with two sources' projections + one lineup each."""
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'],
                             starts_at='2026-09-10T00:20:00+00:00')
        pool = [
            {'player_id': 1, 'name': 'Drake Maye', 'position': 'QB',
             'positions': ['QB'], 'team': 'NE', 'salary': 10000,
             'projection': 20.0, 'source': 'dff'},
            {'player_id': 2, 'name': 'Patriots DST', 'position': 'DST',
             'positions': ['DST'], 'team': 'NE', 'salary': 3000,
             'projection': 8.0, 'source': 'fallback'},
        ]
        for source in ('dff', 'fallback'):
            for player in pool:
                temp_db.save_source_projection(1, source, player)

        # dff lineup: CPT Maye + flex incl. a DST
        lineup = {'captain': {**pool[0]}, 'flex': [pool[1]],
                  'total_projection': 38.0, 'total_salary': 18000}
        temp_db.save_lineup_prediction(1, 'dff', 'showdown', lineup)

    def test_scores_captain_at_1_5x(self, temp_db, monkeypatch):
        self._save_fixture(temp_db)
        monkeypatch.setattr(
            tracker, 'fetch_slate_results',
            lambda date, games: {
                'drake maye': {'fppg': 20.0, 'team': 'NE', 'stats': {}},
                'patriots': {'fppg': 11.0, 'team': 'NE', 'stats': {}}})

        row = temp_db.get_contest(1)
        tracker._score_one_contest(row, '20260909')

        accuracy = temp_db.contest_accuracy(1)
        assert len(accuracy) == 1
        # CPT 1.5 * 20 + DST 11 = 41.0
        assert accuracy[0]['lineup_actual'] == 41.0

    def test_missing_player_counts_zero(self, temp_db, monkeypatch, capsys):
        self._save_fixture(temp_db)
        # Only Maye found; the DST (and flex bench guy) get 0
        monkeypatch.setattr(
            tracker, 'fetch_slate_results',
            lambda date, games: {'drake maye': {'fppg': 20.0, 'stats': {}}})

        row = temp_db.get_contest(1)
        tracker._score_one_contest(row, '20260909')

        accuracy = temp_db.contest_accuracy(1)
        # CPT 1.5 * 20 + DST 0 = 30.0
        assert accuracy[0]['lineup_actual'] == 30.0
        out = capsys.readouterr().out
        assert 'no recorded actual' in out

    def test_no_results_graceful(self, temp_db, monkeypatch, capsys):
        self._save_fixture(temp_db)
        monkeypatch.setattr(tracker, 'fetch_slate_results',
                            lambda date, games: {})
        row = temp_db.get_contest(1)
        tracker._score_one_contest(row, '20260909')
        out = capsys.readouterr().out
        assert 'No actual results found' in out


class TestGradeSavedLineups:
    """grade_saved_lineups: score lineups from recorded actuals, no network."""

    def _graded_fixture(self, temp_db):
        """Contest with recorded actuals and a sentinel expert lineup."""
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        pool = [
            {'player_id': 1, 'name': 'Drake Maye', 'position': 'QB',
             'positions': ['QB'], 'team': 'NE', 'salary': 10000,
             'projection': 20.0, 'source': 'dff'},
            {'player_id': 2, 'name': 'Patriots DST', 'position': 'DST',
             'positions': ['DST'], 'team': 'NE', 'salary': 3000,
             'projection': 8.0, 'source': 'fallback'},
        ]
        for source in ('dff', 'fallback'):
            for player in pool:
                temp_db.save_source_projection(1, source, player)
        temp_db.record_player_actuals(1, {
            'drake maye': {'fppg': 20.0},
            'patriots': {'fppg': 11.0}})

        # expert lineup: no stated projection (0.0 sentinel)
        lineup = {'captain': {**pool[0]}, 'flex': [pool[1]],
                  'total_projection': 0.0, 'total_salary': 18000}
        temp_db.save_lineup_prediction(1, 'si', 'showdown', lineup)

    def test_scores_captain_at_1_5x(self, temp_db):
        self._graded_fixture(temp_db)
        tracker.grade_saved_lineups(1)
        accuracy = temp_db.contest_accuracy(1)
        assert accuracy[0]['lineup_actual'] == 41.0  # 1.5 * 20 + 11
        assert accuracy[0]['projected_known'] is False

    def test_print_renders_sentinel_as_unknown(self, temp_db, capsys):
        self._graded_fixture(temp_db)
        tracker.grade_saved_lineups(1)
        tracker._print_contest_accuracy(1)
        out = capsys.readouterr().out
        assert 'si' in out
        assert '--' in out  # proj/diff columns show '--', not -41.00

    def test_rescore_scores_without_network(self, temp_db, monkeypatch):
        self._graded_fixture(temp_db)

        class Args:
            contest_id = 1

        def no_network(*a, **kw):
            raise AssertionError('--rescore must not hit the network')
        monkeypatch.setattr(tracker, 'fetch_slate_results', no_network)
        tracker.rescore_contests(Args())
        accuracy = temp_db.contest_accuracy(1)
        assert accuracy[0]['lineup_actual'] == 41.0

    def test_no_actuals_yet_leaves_lineups_unscored(self, temp_db, capsys):
        # Pre-game import: zero actuals recorded — grading must NOT stamp
        # total_actual=0 on every lineup (that would look like a real score)
        temp_db.save_contest(1, 2, 'test', 'showdown', ['NE @ SEA'])
        player = {'player_id': 1, 'name': 'Drake Maye', 'position': 'QB',
                   'positions': ['QB'], 'team': 'NE', 'salary': 10000,
                   'projection': 20.0, 'source': 'dff'}
        temp_db.save_source_projection(1, 'dff', player)
        lineup = {'captain': {**player}, 'flex': [],
                  'total_projection': 30.0, 'total_salary': 10000}
        temp_db.save_lineup_prediction(1, 'dff', 'showdown', lineup)

        tracker.grade_saved_lineups(1)
        out = capsys.readouterr().out
        assert 'nothing to grade' in out

        conn = temp_db.get_connection()
        row = conn.execute("SELECT total_actual FROM lineup_predictions"
                           ).fetchone()
        conn.close()
        assert row['total_actual'] is None


class TestSaveSnapshot:
    def test_full_save_flow(self, temp_db, monkeypatch):
        draftables = [make_draftable(i, n, p, t, s) for i, n, p, t, s in [
            (1, 'Drake Maye', 'QB', 'NE', 10000),
            (2, 'Rhamondre Stevenson', 'RB', 'NE', 8400),
            (3, 'Hunter Henry', 'TE', 'NE', 2200),
            (4, 'Sam Darnold', 'QB', 'SEA', 9400),
            (5, 'Kenneth Walker', 'RB', 'SEA', 8600),
            (6, 'Jaxon Smith', 'WR', 'SEA', 3400),
            (7, 'Patriots DST', 'DST', 'NE', 3000),
            (8, 'Seahawks DST', 'DST', 'SEA', 2800),
        ]]

        monkeypatch.setattr(tracker, 'prepare_contest',
                            lambda args: (FakeContest(), 'showdown',
                                          draftables))
        monkeypatch.setattr(
            tracker, 'get_source_projections',
            lambda players, csv_path=None, week=None, allow_scrape=True: {
                'dailyfantasyfuel': {
                    p['player_id']: {'projection': 12.0,
                                     'source': 'dailyfantasyfuel'}
                    for p in players},
                'fallback': {
                    p['player_id']: {'projection': 10.0, 'source': 'fallback'}
                    for p in players},
            })

        class Args:
            csv = None
            week = None
            no_scrape = True
            stack = 'qbwr'
            no_dst_captain = True

        tracker.save_snapshot(Args())

        contests = temp_db.list_contests()
        assert len(contests) == 1
        assert json.loads(contests[0]['slate_json']) == ['NE @ SEA']
        # 8 players x 2 sources
        assert contests[0]['n_projections'] == 16
        # one lineup per source
        conn = temp_db.get_connection()
        lineups = conn.execute(
            "SELECT source, total_projected, captain_name, players_json "
            "FROM lineup_predictions").fetchall()
        conn.close()
        assert {row['source'] for row in lineups} == {'dailyfantasyfuel',
                                                      'fallback'}
        captains = {row['source']: row['captain_name'] for row in lineups}
        assert captains['dailyfantasyfuel'] is not None