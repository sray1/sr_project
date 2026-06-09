"""Tests for DraftKings scoring calculations."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from draftkings_scoring import DKScoringCalculator, DKScoringRules, PlayerStats


class TestDKScoringCalculator:
    """Test DK fantasy point calculations."""

    def setup_method(self):
        self.calc = DKScoringCalculator()

    def test_basic_scoring(self):
        """Test basic fantasy point calculation without bonuses."""
        stats = PlayerStats(points=20, rebounds=10, assists=5, steals=2, blocks=1, turnovers=3, three_pointers=3)
        # 20*1.0 + 10*1.25 + 5*1.5 + 2*2.0 + 1*2.0 + 3*(-0.5) + 3*0.5
        # = 20 + 12.5 + 7.5 + 4 + 2 - 1.5 + 1.5 = 46.0
        # Plus double-double bonus (pts>=10, reb>=10): +1.5
        result = self.calc.calculate_fantasy_points(stats)
        assert result == pytest.approx(47.5, abs=0.01)

    def test_triple_double_bonus(self):
        """Test triple-double bonus is applied correctly."""
        stats = PlayerStats(points=30, rebounds=12, assists=10, steals=1, blocks=0, turnovers=4, three_pointers=2)
        # 30*1.0 + 12*1.25 + 10*1.5 + 1*2.0 + 0*2.0 + 4*(-0.5) + 2*0.5
        # = 30 + 15 + 15 + 2 + 0 - 2 + 1 = 61.0
        # Triple-double (pts>=10, reb>=10, ast>=10): +3.0
        result = self.calc.calculate_fantasy_points(stats)
        assert result == pytest.approx(64.0, abs=0.01)

    def test_no_bonus(self):
        """Test scoring with no double-double or triple-double."""
        stats = PlayerStats(points=9, rebounds=5, assists=3, steals=1, blocks=0, turnovers=2, three_pointers=1)
        # 9*1.0 + 5*1.25 + 3*1.5 + 1*2.0 + 0*2.0 + 2*(-0.5) + 1*0.5
        # = 9 + 6.25 + 4.5 + 2 + 0 - 1 + 0.5 = 21.25
        result = self.calc.calculate_fantasy_points(stats)
        assert result == pytest.approx(21.25, abs=0.01)

    def test_zero_stats(self):
        """Test scoring with all zero stats."""
        stats = PlayerStats(points=0, rebounds=0, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=0)
        result = self.calc.calculate_fantasy_points(stats)
        assert result == 0.0

    def test_negative_turnovers(self):
        """Test that turnovers subtract points."""
        stats_no_to = PlayerStats(points=10, rebounds=5, assists=3, steals=1, blocks=0, turnovers=0, three_pointers=1)
        stats_with_to = PlayerStats(points=10, rebounds=5, assists=3, steals=1, blocks=0, turnovers=5, three_pointers=1)
        diff = self.calc.calculate_fantasy_points(stats_no_to) - self.calc.calculate_fantasy_points(stats_with_to)
        # 5 turnovers * 0.5 = 2.5
        assert diff == pytest.approx(2.5, abs=0.01)

    def test_captain_multiplier(self):
        """Test that captain 1.5x multiplier works on base fppg."""
        stats = PlayerStats(points=30, rebounds=12, assists=2, steals=1, blocks=3, turnovers=6, three_pointers=2)
        base = self.calc.calculate_fantasy_points(stats)
        captain = base * 1.5
        assert captain == pytest.approx(base * 1.5, abs=0.01)
        assert base > 0

    def test_double_double_same_category_threshold(self):
        """Test double-double with points and rebounds both at exactly 10."""
        stats = PlayerStats(points=10, rebounds=10, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=0)
        # 10*1.0 + 10*1.25 + 0 + 0 + 0 + 0 + 0 = 22.5
        # Double-double: +1.5
        result = self.calc.calculate_fantasy_points(stats)
        assert result == pytest.approx(24.0, abs=0.01)

    def test_calculate_from_stat_line(self):
        """Test the convenience method for calculating from raw stat line."""
        result = self.calc.calculate_from_stat_line(
            points=26, rebounds=12, assists=2, steals=1, blocks=3, turnovers=6, three_pointers=2
        )
        # Same as Wembanyama stat line
        assert result > 0
        # Should match creating PlayerStats manually
        manual = self.calc.calculate_fantasy_points(
            PlayerStats(points=26, rebounds=12, assists=2, steals=1, blocks=3, turnovers=6, three_pointers=2)
        )
        assert result == pytest.approx(manual, abs=0.01)


class TestDKScoringRules:
    """Test default scoring rule values."""

    def test_default_rules(self):
        rules = DKScoringRules()
        assert rules.points == 1.0
        assert rules.rebounds == 1.25
        assert rules.assists == 1.5
        assert rules.steals == 2.0
        assert rules.blocks == 2.0
        assert rules.turnovers == -0.5
        assert rules.three_pointers == 0.5
        assert rules.double_double_bonus == 1.5
        assert rules.triple_double_bonus == 3.0


class TestPlayerStats:
    """Test PlayerStats dataclass."""

    def test_default_values(self):
        stats = PlayerStats()
        assert stats.points == 0.0
        assert stats.rebounds == 0.0
        assert stats.assists == 0.0

    def test_custom_values(self):
        stats = PlayerStats(points=30, rebounds=10, assists=5)
        assert stats.points == 30
        assert stats.rebounds == 10
        assert stats.assists == 5
        assert stats.steals == 0.0  # default