"""Tests for contest type detection."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from contest_detector import ContestType, ContestInfo, detect_contest_type, get_contest_info


class TestDetectContestType:
    """Test contest type detection from contest names."""

    def test_showdown_keyword(self):
        assert detect_contest_type("NBA Showdown $1M Finals Tip-Off Special") == ContestType.SHOWDOWN

    def test_showdown_case_insensitive(self):
        assert detect_contest_type("NBA SHOWDOWN Championship") == ContestType.SHOWDOWN

    def test_single_game_keyword(self):
        assert detect_contest_type("NBA Single Game $50K") == ContestType.SHOWDOWN

    def test_sgp_keyword(self):
        assert detect_contest_type("NBA SGP Pick'em") == ContestType.SHOWDOWN

    def test_captain_keyword(self):
        assert detect_contest_type("NBA Captain Mode $100K") == ContestType.SHOWDOWN

    def test_mvp_keyword(self):
        assert detect_contest_type("NBA MVP Contest") == ContestType.SHOWDOWN

    def test_classic_default(self):
        assert detect_contest_type("NBA Classic $200K Tournament") == ContestType.CLASSIC

    def test_classic_gpp(self):
        assert detect_contest_type("NBA GPP $100K") == ContestType.CLASSIC

    def test_classic_no_keyword(self):
        assert detect_contest_type("NBA Regular Season Contest") == ContestType.CLASSIC

    def test_empty_string(self):
        assert detect_contest_type("") == ContestType.CLASSIC

    def test_partial_showdown_in_word(self):
        # "showdown" should match even as part of other text
        assert detect_contest_type("Showdown Special") == ContestType.SHOWDOWN


class TestGetContestInfo:
    """Test ContestInfo creation from contest ID and name."""

    def test_showdown_contest_info(self):
        info = get_contest_info(12345, "NBA Showdown $1M")
        assert info.contest_type == ContestType.SHOWDOWN
        assert info.contest_id == 12345
        assert info.salary_cap == 50000
        assert info.roster_spots == 6
        assert info.captain_multiplier == 1.5

    def test_classic_contest_info(self):
        info = get_contest_info(67890, "NBA Classic $200K")
        assert info.contest_type == ContestType.CLASSIC
        assert info.contest_id == 67890
        assert info.salary_cap == 50000
        assert info.roster_spots == 8
        assert info.captain_multiplier == 1.0

    def test_contest_info_name_preserved(self):
        info = get_contest_info(999, "My Contest Name")
        assert info.contest_name == "My Contest Name"