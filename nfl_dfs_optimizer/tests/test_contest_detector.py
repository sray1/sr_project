"""Tests for NFL contest type detection (contest_detector.py)."""

from contest_detector import (
    ContestType, detect_contest_type, is_main_slate, get_contest_info,
)


class TestDetectContestType:
    def test_showdown_keywords(self):
        for name in [
            "NFL Showdown $100K [1 Game]",
            "NFL Single Game $50K",
            "NFL SGP Captain Mode",
            "NFL MVP Contest",
            "Thursday Night Showdown [$250K]",
        ]:
            assert detect_contest_type(name) == ContestType.SHOWDOWN, name

    def test_classic_names(self):
        for name in [
            "NFL $5M Fantasy Football Millionaire Maker ($20)",
            "NFL $3K Texas No Limit Hold'em",
            "NFL $500K Fantasy Football World",
            "NFL Play-Action $25K",
        ]:
            assert detect_contest_type(name) == ContestType.CLASSIC, name


class TestMainSlateDetection:
    def test_explicit_main_slate(self):
        assert is_main_slate("NFL $100K Main Slate Play-Action")
        assert is_main_slate("NFL Main Slate Crossover")

    def test_gpp_markers(self):
        assert is_main_slate("NFL $5M Fantasy Football Millionaire Maker ($20)")
        assert is_main_slate("NFL $1M Fantasy Football Grand Jam")
        assert is_main_slate("NFL Sunday GPP Special")

    def test_non_main_slate(self):
        assert not is_main_slate("NFL Showdown $100K [1 Game]")
        assert not is_main_slate("NFL Single Game $50K")
        assert not is_main_slate("NFL $3K 50/50")


class TestContestInfo:
    def test_showdown_info(self):
        info = get_contest_info(123, "NFL Showdown $100K")
        assert info.contest_type == ContestType.SHOWDOWN
        assert info.salary_cap == 50000
        assert info.roster_spots == 6
        assert info.captain_multiplier == 1.5

    def test_classic_info(self):
        info = get_contest_info(456, "NFL $5M Fantasy Football Millionaire Maker")
        assert info.contest_type == ContestType.CLASSIC
        assert info.salary_cap == 50000
        assert info.roster_spots == 9
        assert info.captain_multiplier == 1.0
        assert info.CLASSIC_POSITIONS == {
            'QB': 1, 'RB': 2, 'WR': 3, 'TE': 1, 'FLEX': 1, 'DST': 1
        }