"""Tests for the game-environment kicker model (kicker_model.py)."""

import pytest

from kicker_model import (apply_kicker_model, estimate_kicker_projection,
                          KICKER_BASE, KICKER_ENV_CENTER, KICKER_ENV_SLOPE,
                          KICKER_MAX, KICKER_MIN)


def make_player(pid, name, position, team, salary):
    return {'player_id': pid, 'name': name, 'position': position,
            'positions': [position], 'team': team, 'salary': salary}


class TestEstimate:
    def test_center_is_base(self):
        assert estimate_kicker_projection(KICKER_ENV_CENTER) == KICKER_BASE

    def test_high_scoring_environment_tilts_up(self):
        # KC's 139.4 offensive points (IND@KC week 2) -> base + 0.016*(139.4-88.7)
        expected = KICKER_BASE + KICKER_ENV_SLOPE * (139.4 - KICKER_ENV_CENTER)
        assert estimate_kicker_projection(139.4) == pytest.approx(expected, abs=0.01)
        assert estimate_kicker_projection(139.4) > KICKER_BASE

    def test_poor_environment_tilts_down(self):
        # NYG's 33.0 offensive points (NYG@LAR week 2)
        assert estimate_kicker_projection(33.0) < KICKER_BASE
        assert estimate_kicker_projection(33.0) >= KICKER_MIN

    def test_clamped(self):
        assert estimate_kicker_projection(0) >= KICKER_MIN
        assert estimate_kicker_projection(100000) == KICKER_MAX
        assert estimate_kicker_projection(-1000) == KICKER_MIN

    def test_fit_sample_sanity(self):
        # The model's core promise: a fair ~7-point kicker, not the ~11.2
        # the salary curve hands a $4,600 kicker
        mid = estimate_kicker_projection(90)
        assert 6.0 < mid < 9.0


class TestApplyKickerModel:
    def pool(self):
        return [
            make_player(1, 'Patrick Mahomes', 'QB', 'KC', 9600),
            make_player(2, 'Isiah Pacheco', 'RB', 'KC', 5200),
            make_player(3, 'Travis Kelce', 'TE', 'KC', 7000),
            make_player(4, 'Chiefs DST', 'DST', 'KC', 3500),
            make_player(5, 'Harrison Butker', 'K', 'KC', 4600),
            make_player(6, 'Xavier Worthy', 'WR', 'KC', 5400),
            make_player(7, 'Jonathan Taylor', 'RB', 'IND', 11000),
            make_player(8, 'Spencer Shrader', 'K', 'IND', 4300),
        ]

    def attached(self, pool, kicker_source='fallback'):
        """Projections dict with kickers on the given source label."""
        attached = {}
        for p in pool:
            if p['position'] == 'K':
                attached[p['player_id']] = {'projection': 11.0,
                                             'source': kicker_source}
            else:
                attached[p['player_id']] = {'projection': 10.0,
                                             'source': 'dailyfantasyfuel'}
        return attached

    def test_kickers_replaced_and_labeled(self):
        pool = self.pool()
        attached = self.attached(pool)
        replaced = apply_kicker_model(pool, attached)

        assert replaced == 2
        for pid in (5, 8):
            assert attached[pid]['source'] == 'kicker_model'
            assert attached[pid]['projection'] != 11.0

    def test_team_sum_is_own_team_offense_only(self):
        pool = self.pool()
        attached = self.attached(pool)
        apply_kicker_model(pool, attached)

        # KC kicker: 4 offensive teammates (DST excluded) x 10.0 = 40
        # IND kicker: Taylor only = 10
        kc = KICKER_BASE + KICKER_ENV_SLOPE * (40.0 - KICKER_ENV_CENTER)
        ind = KICKER_BASE + KICKER_ENV_SLOPE * (10.0 - KICKER_ENV_CENTER)
        assert attached[5]['projection'] == pytest.approx(kc, abs=0.01)
        assert attached[8]['projection'] == pytest.approx(ind, abs=0.01)
        assert attached[5]['projection'] > attached[8]['projection']

    def test_dst_excluded_from_environment(self):
        pool = self.pool()
        attached = self.attached(pool)
        apply_kicker_model(pool, attached)
        # If the DST counted, the KC sum would be 50, not 40
        kc = KICKER_BASE + KICKER_ENV_SLOPE * (40.0 - KICKER_ENV_CENTER)
        assert attached[5]['projection'] == pytest.approx(kc, abs=0.01)

    def test_board_matched_kicker_overridden(self):
        # DFF's own kicker projections were WORSE than this model on the
        # fit sample (+2.4 over actual, 6.6 MAE) — they get replaced too
        pool = self.pool()
        attached = self.attached(pool, kicker_source='dailyfantasyfuel')
        replaced = apply_kicker_model(pool, attached)
        assert replaced == 2
        assert attached[5]['source'] == 'kicker_model'

    def test_csv_kicker_untouched(self):
        pool = self.pool()
        attached = self.attached(pool)
        attached[5] = {'projection': 9.5, 'source': 'csv'}
        replaced = apply_kicker_model(pool, attached)
        assert replaced == 1
        assert attached[5] == {'projection': 9.5, 'source': 'csv'}

    def test_kicker_with_no_offense_on_slate(self):
        # Slate lists only the kicker for some reason -> environment 0
        pool = [make_player(9, 'Eddy Pineiro', 'K', 'SF', 4300)]
        attached = {9: {'projection': 11.0, 'source': 'fallback'}}
        apply_kicker_model(pool, attached)
        expected = KICKER_BASE + KICKER_ENV_SLOPE * (0.0 - KICKER_ENV_CENTER)
        assert attached[9]['projection'] == pytest.approx(expected, abs=0.01)

    def test_no_kickers_noop(self):
        pool = [p for p in self.pool() if p['position'] != 'K']
        attached = self.attached(pool)
        assert apply_kicker_model(pool, attached) == 0

    def test_offense_projections_unchanged(self):
        pool = self.pool()
        attached = self.attached(pool)
        apply_kicker_model(pool, attached)
        for pid in (1, 2, 3, 4, 6, 7):
            assert attached[pid] == {'projection': 10.0,
                                    'source': 'dailyfantasyfuel'}