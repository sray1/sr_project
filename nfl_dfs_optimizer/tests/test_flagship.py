"""Tests for the DB-corrected flagship single-entry pick (flagship.py)."""

import pytest

import accuracy_db as db
import flagship
from flagship import captain_rank_stats, flagship_pick


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init_db()
    return db


def seed_snapshot(temp_db, contest_id, actuals):
    """One scored showdown snapshot: a two-team pool with projections.

    Ten skill players across two teams (salaries tiny, so every captain
    choice is feasible). `actuals` maps player_id -> actual DK points;
    players without an entry count as 0 (did not play).
    """
    temp_db.save_contest(contest_id, 2, 'test', 'showdown', ['A @ B'])
    for pid in range(10):
        player = {
            'player_id': pid,
            'name': f'Player {pid}',
            'position': ['QB', 'RB', 'WR', 'TE', 'WR'][pid % 5],
            'positions': [['QB'], ['RB'], ['WR'], ['TE'], ['WR']][pid % 5],
            'team': 'A' if pid < 5 else 'B',
            'salary': 1000,
            'projection': 20.0 - pid,  # descending: the MILP's captain order
            'source': 'dailyfantasyfuel',
        }
        temp_db.save_source_projection(contest_id, 'dailyfantasyfuel',
                                       player)
        if pid in actuals:
            conn = db.get_connection()
            conn.execute(
                "UPDATE source_projections SET actual_fppg = ? "
                "WHERE contest_id = ? AND source = 'dailyfantasyfuel' "
                "AND player_id = ?",
                (actuals[pid], str(contest_id), str(pid)))
            conn.commit()
            conn.close()


def fake_builds(projections, captains=None):
    """Minimal lineup dicts: flagship_pick reads total_projection."""
    captains = captains or [f'Captain {i}' for i in range(len(projections))]
    return [{'total_projection': p, 'captain': {'name': c}, 'flex': []}
            for p, c in zip(projections, captains)]


class TestCaptainRankStats:
    def test_retro_grades_scored_snapshots(self, temp_db):
        # Flat 10-point actuals: every build scores 1.5*10 + 5*10 = 65
        seed_snapshot(temp_db, 1, {pid: 10.0 for pid in range(10)})
        stats = captain_rank_stats(n_builds=5, min_snapshots=1,
                                   min_players=1)
        assert set(stats) == set(range(1, 6))
        for rank, bucket in stats.items():
            assert bucket['n'] == 1
            assert bucket['mean_actual'] == pytest.approx(65.0)
        # Chalk build (rank 1) carries the highest projected total
        assert stats[1]['mean_proj'] > stats[5]['mean_proj']

    def test_snapshot_gate_too_few_contests(self, temp_db):
        seed_snapshot(temp_db, 1, {pid: 10.0 for pid in range(10)})
        # Two graded snapshots required; only one exists
        assert captain_rank_stats(n_builds=5, min_snapshots=2,
                                  min_players=1) == {}

    def test_thin_pools_skipped(self, temp_db):
        temp_db.save_contest(1, 2, 'test', 'showdown', ['A @ B'])
        for pid in range(3):  # too few scored rows to rebuild builds
            temp_db.save_source_projection(1, 'dailyfantasyfuel', {
                'player_id': pid, 'name': f'Player {pid}',
                'position': 'WR', 'positions': ['WR'], 'team': 'A',
                'salary': 1000, 'projection': 10.0,
                'source': 'dailyfantasyfuel'})
            conn = db.get_connection()
            conn.execute(
                "UPDATE source_projections SET actual_fppg = 5.0 "
                "WHERE contest_id = 1 AND player_id = ?", (str(pid),))
            conn.commit()
            conn.close()
        assert captain_rank_stats(n_builds=5, min_snapshots=1,
                                  min_players=10) == {}

    def test_no_db_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'missing.db'))
        assert captain_rank_stats() == {}


class TestFlagshipPick:
    def stats(self):
        # Retro-grade says: rank 1 underperforms its projection by 6,
        # rank 3 by 1 — the season pattern
        return {
            1: {'n': 27, 'mean_proj': 95.0, 'mean_actual': 89.0},
            2: {'n': 27, 'mean_proj': 94.0, 'mean_actual': 91.0},
            3: {'n': 27, 'mean_proj': 93.0, 'mean_actual': 92.0},
        }

    def test_picks_best_corrected_expectation(self):
        lineups = fake_builds([100.0, 99.0, 98.0])
        index, gain = flagship_pick(lineups, self.stats())
        # Corrected: r1 100-6=94, r2 99-3=96, r3 98-1=97 -> rank 3 wins
        assert index == 2
        assert gain == pytest.approx(3.0)

    def test_chalk_still_picked_when_corrections_favor_it(self):
        stats = {
            1: {'n': 27, 'mean_proj': 95.0, 'mean_actual': 95.0},
            2: {'n': 27, 'mean_proj': 94.0, 'mean_actual': 88.0},
            3: {'n': 27, 'mean_proj': 93.0, 'mean_actual': 90.0},
        }
        lineups = fake_builds([100.0, 99.0, 98.0])
        index, gain = flagship_pick(lineups, stats)
        assert index == 0
        assert gain == pytest.approx(0.0)

    def test_missing_stats_returns_none(self):
        assert flagship_pick(fake_builds([100.0, 99.0]), {}) is None

    def test_no_lineups_returns_none(self):
        assert flagship_pick([], self.stats()) is None

    def test_rank_table_shorter_than_builds_returns_none(self):
        stats = {1: {'n': 27, 'mean_proj': 95.0, 'mean_actual': 95.0}}
        assert flagship_pick(fake_builds([100.0, 99.0]), stats) is None
