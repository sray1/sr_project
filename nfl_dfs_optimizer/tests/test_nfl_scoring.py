"""Tests for DK NFL scoring rules (nfl_scoring.py)."""

import pytest

from nfl_scoring import (
    NFLScoringRules, DSTScoring,
    calculate_offensive_points, calculate_dst_points,
)


class TestOffensiveScoring:
    def test_empty_stat_line(self):
        assert calculate_offensive_points() == 0.0

    def test_passing_only(self):
        # 250 pass yds, 2 TD: 10 + 8 = 18
        assert calculate_offensive_points(pass_yards=250, pass_tds=2) == 18.0

    def test_passing_bonus_300_yards(self):
        # 300 pass yds, 0 TD: 12 + 3 bonus = 15
        assert calculate_offensive_points(pass_yards=300) == 15.0

    def test_299_yards_no_bonus(self):
        # 299/25 = 11.96, no bonus
        assert calculate_offensive_points(pass_yards=299) == pytest.approx(11.96, abs=0.01)

    def test_interception_penalty(self):
        # 200 pass yds, 1 TD, 1 INT: 8 + 4 - 1 = 11
        assert calculate_offensive_points(pass_yards=200, pass_tds=1,
                                          interceptions=1) == 11.0

    def test_rushing_only(self):
        # 85 rush yds, 1 TD: 8.5 + 6 = 14.5
        assert calculate_offensive_points(rush_yards=85, rush_tds=1) == 14.5

    def test_rushing_bonus_100_yards(self):
        # 100 rush yds: 10 + 3 bonus = 13
        assert calculate_offensive_points(rush_yards=100) == 13.0

    def test_receiving_ppr(self):
        # 8 rec, 90 yds: 8 + 9 = 17
        assert calculate_offensive_points(receptions=8, rec_yards=90) == 17.0

    def test_receiving_bonus_100_yards(self):
        # 10 rec, 120 yds, 1 TD: 10 + 12 + 6 + 3 bonus = 31
        assert calculate_offensive_points(receptions=10, rec_yards=120,
                                          rec_tds=1) == 31.0

    def test_dual_threat_qb(self):
        # Mahomes-like: 320/25=12.8, 3 pass TD=12, 1 INT=-1,
        # 45 rush/10=4.5, 1 rush TD=6, 300+ bonus=3 -> 37.3
        points = calculate_offensive_points(pass_yards=320, pass_tds=3,
                                            interceptions=1,
                                            rush_yards=45, rush_tds=1)
        assert points == pytest.approx(37.3, abs=0.01)

    def test_fumble_and_two_point(self):
        # 60 rush yds=6, 1 TD=6, 1 fumble=-1, 1 two-pt=2 -> 13
        assert calculate_offensive_points(rush_yards=60, rush_tds=1,
                                          fumbles_lost=1,
                                          two_pt_conversions=1) == 13.0

    def test_scoring_constants(self):
        rules = NFLScoringRules()
        assert rules.passing_touchdown == 4.0
        assert rules.reception == 1.0  # full PPR
        assert rules.rushing_touchdown == 6.0
        assert rules.interception_thrown == -1.0
        assert rules.fumble_lost == -1.0


class TestDSTScoring:
    def test_shutout(self):
        assert DSTScoring.points_allowed_bonus(0) == 10.0

    def test_points_allowed_tiers(self):
        assert DSTScoring.points_allowed_bonus(3) == 7.0
        assert DSTScoring.points_allowed_bonus(6) == 7.0
        assert DSTScoring.points_allowed_bonus(7) == 4.0
        assert DSTScoring.points_allowed_bonus(13) == 4.0
        assert DSTScoring.points_allowed_bonus(14) == 1.0
        assert DSTScoring.points_allowed_bonus(20) == 1.0
        assert DSTScoring.points_allowed_bonus(21) == 0.0
        assert DSTScoring.points_allowed_bonus(27) == 0.0
        assert DSTScoring.points_allowed_bonus(28) == -1.0
        assert DSTScoring.points_allowed_bonus(34) == -1.0
        assert DSTScoring.points_allowed_bonus(35) == -4.0
        assert DSTScoring.points_allowed_bonus(56) == -4.0

    def test_invalid_points_allowed(self):
        with pytest.raises(ValueError):
            DSTScoring.points_allowed_bonus(-1)

    def test_typical_dst_line(self):
        # 3 sacks, 1 INT, 17 points allowed: 3 + 2 + 1 = 6
        points = calculate_dst_points(sacks=3, interceptions=1, points_allowed=17)
        assert points == 6.0

    def test_dominant_dst_line(self):
        # 5 sacks, 2 INT, 1 fumble rec, 1 TD, 3 points allowed:
        # 5 + 4 + 2 + 6 + 7 = 24
        points = calculate_dst_points(sacks=5, interceptions=2,
                                      fumble_recoveries=1, touchdowns=1,
                                      points_allowed=3)
        assert points == 24.0

    def test_dst_constants(self):
        scoring = DSTScoring()
        assert scoring.sack == 1.0
        assert scoring.interception == 2.0
        assert scoring.touchdown == 6.0
        assert scoring.safety == 2.0