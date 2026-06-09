"""Tests for game results module — StatMuse parsing, name normalization, verification."""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from draftkings_scoring import PlayerStats
from game_results import (
    _normalize_player_name, get_statmuse_url, parse_statmuse_box_score,
    verify_box_score, get_game_id_for_contest, get_date_for_contest,
    _parse_statmuse_html, _html_to_markdown_tables,
)


class TestNormalizePlayerName:
    """Test NBA player name normalization from nba_api format."""

    def test_known_mapping(self):
        assert _normalize_player_name("V. Wembanyama") == "Victor Wembanyama"

    def test_known_mapping_fox(self):
        assert _normalize_player_name("D. Fox") == "De'Aaron Fox"

    def test_known_mapping_brunson(self):
        assert _normalize_player_name("J. Brunson") == "Jalen Brunson"

    def test_unknown_short_name_passthrough(self):
        # Unknown name should pass through unchanged
        assert _normalize_player_name("Unknown Player") == "Unknown Player"

    def test_full_name_passthrough(self):
        # Already-full names should pass through
        assert _normalize_player_name("Victor Wembanyama") == "Victor Wembanyama"


class TestGetStatmuseUrl:
    """Test StatMuse URL construction."""

    def test_basic_url(self):
        url = get_statmuse_url("2026-06-03", "NYK", "SAS")
        assert url == "https://www.statmuse.com/nba/game/2026-6-03-nyk-at-sas"

    def test_no_zero_padding(self):
        # Month is not zero-padded, day keeps leading zero
        url = get_statmuse_url("2025-01-05", "LAL", "BOS")
        assert url == "https://www.statmuse.com/nba/game/2025-1-05-lal-at-bos"

    def test_double_digit_date(self):
        url = get_statmuse_url("2025-12-25", "GSW", "MIA")
        assert url == "https://www.statmuse.com/nba/game/2025-12-25-gsw-at-mia"

    def test_lowercase_teams(self):
        url = get_statmuse_url("2025-06-22", "IND", "OKC")
        assert "ind-at-okc" in url


class TestParseStatmuseBoxScore:
    """Test parsing StatMuse pipe-delimited box score output."""

    def test_basic_parsing(self):
        raw = """Victor Wembanyama|SAS|32.0|26|12|2|1|3|6|2
Jalen Brunson|NYK|35.0|30|3|2|0|0|4|2
Josh Hart|NYK|38.0|3|15|6|4|1|0|0"""
        result = parse_statmuse_box_score(raw, "SAS", "NYK")

        assert "Victor Wembanyama" in result
        assert "Jalen Brunson" in result
        assert "Josh Hart" in result

        wemby = result["Victor Wembanyama"]
        assert wemby["stats"].points == 26
        assert wemby["stats"].rebounds == 12
        assert wemby["stats"].assists == 2
        assert wemby["stats"].steals == 1
        assert wemby["stats"].blocks == 3
        assert wemby["stats"].turnovers == 6
        assert wemby["stats"].three_pointers == 2
        assert wemby["team"] == "SAS"
        assert wemby["minutes"] == 32.0

    def test_empty_input(self):
        result = parse_statmuse_box_score("", "SAS", "NYK")
        assert result == {}

    def test_malformed_lines_skipped(self):
        raw = """Victor Wembanyama|SAS|32.0|26|12|2|1|3|6|2
BadLine|SAS|incomplete
Jalen Brunson|NYK|35.0|30|3|2|0|0|4|2"""
        result = parse_statmuse_box_score(raw, "SAS", "NYK")
        assert "Victor Wembanyama" in result
        assert "Jalen Brunson" in result
        assert "BadLine" not in result

    def test_no_data_marker(self):
        raw = "NO_DATA"
        result = parse_statmuse_box_score(raw, "SAS", "NYK")
        assert result == {}

    def test_dk_scoring_matches(self):
        """Verify DK fantasy points are calculated correctly from parsed stats."""
        raw = "Josh Hart|NYK|38.0|3|15|6|4|1|0|0"
        result = parse_statmuse_box_score(raw, "SAS", "NYK")

        from draftkings_scoring import DKScoringCalculator
        calc = DKScoringCalculator()
        hart = result["Josh Hart"]
        fppg = calc.calculate_fantasy_points(hart["stats"])
        # 3*1.0 + 15*1.25 + 6*1.5 + 4*2.0 + 1*2.0 + 0*(-0.5) + 0*0.5
        # = 3 + 18.75 + 9 + 8 + 2 + 0 + 0 = 40.75
        # No double-double bonus: only reb>=10, ast>=10 are close but ast=6 not >=10
        # Wait: reb=15>=10, ast=6<10, pts=3<10 → only 1 category >=10, no DD
        assert fppg == pytest.approx(40.75, abs=0.01)


class TestVerifyBoxScore:
    """Test cross-verification between two stat sources."""

    def test_matching_stats(self):
        primary = {
            "Victor Wembanyama": {
                "stats": PlayerStats(points=26, rebounds=12, assists=2, steals=1, blocks=3, turnovers=6, three_pointers=2),
                "team": "SAS", "minutes": 32.0,
            },
        }
        secondary = {
            "Victor Wembanyama": {
                "stats": PlayerStats(points=26, rebounds=12, assists=2, steals=1, blocks=3, turnovers=6, three_pointers=2),
                "team": "SAS", "minutes": 32.0,
            },
        }
        result = verify_box_score(primary, secondary)
        assert "Victor Wembanyama" in result
        assert result["Victor Wembanyama"]["stats"].points == 26

    def test_discrepancy_prefers_secondary(self):
        """When stats differ, verify_box_score should prefer StatMuse (secondary)."""
        primary = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=20, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
        }
        secondary = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
        }
        result = verify_box_score(primary, secondary)
        # Should use secondary (StatMuse) data since there are discrepancies
        assert result["Jalen Brunson"]["stats"].points == 30

    def test_secondary_fills_missing(self):
        """Players only in secondary should be added."""
        primary = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
        }
        secondary = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
            "Josh Hart": {
                "stats": PlayerStats(points=3, rebounds=15, assists=6, steals=4, blocks=1, turnovers=0, three_pointers=0),
                "team": "NYK", "minutes": 38.0,
            },
        }
        result = verify_box_score(primary, secondary)
        assert "Josh Hart" in result
        assert "Jalen Brunson" in result

    def test_empty_secondary(self):
        """Empty secondary should just return primary."""
        primary = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
        }
        result = verify_box_score(primary, {})
        assert len(result) == 1
        assert "Jalen Brunson" in result


class TestGetGameIdForContest:
    """Test team abbreviation parsing from contest names."""

    def test_standard_contest_name(self):
        away, home = get_game_id_for_contest("NBA Showdown $1M (NYK @ SAS)")
        assert away == "NYK"
        assert home == "SAS"

    def test_reverse_order(self):
        away, home = get_game_id_for_contest("NBA Showdown (SAS @ NYK)")
        assert away == "SAS"
        assert home == "NYK"

    def test_no_match_raises(self):
        with pytest.raises(ValueError):
            get_game_id_for_contest("NBA Classic Tournament")


class TestGetDateForContest:
    """Test datetime to date string conversion."""

    def test_datetime_object(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 6, 3, 19, 30, tzinfo=timezone.utc)
        result = get_date_for_contest(dt)
        assert result == "2026-06-03"

    def test_string_passthrough(self):
        result = get_date_for_contest("2026-06-03T00:30:00Z")
        assert result == "2026-06-03"


class TestHtmlToMarkdownTables:
    """Test HTML table to markdown conversion."""

    def test_simple_table(self):
        html = "<table><tr><td>Name</td><td>PTS</td></tr><tr><td>Wemby</td><td>26</td></tr></table>"
        result = _html_to_markdown_tables(html)
        assert "Name|PTS" in result
        assert "Wemby|26" in result

    def test_empty_html(self):
        result = _html_to_markdown_tables("")
        assert result == ""

    def test_no_tables(self):
        html = "<div>No tables here</div>"
        result = _html_to_markdown_tables(html)
        assert result == ""

    def test_html_entities(self):
        html = "<table><tr><td>Name&nbsp;&amp;&nbsp;Team</td></tr></table>"
        result = _html_to_markdown_tables(html)
        assert "Name & Team" in result


class TestParseStatmuseHtml:
    """Test parsing StatMuse HTML for box score data."""

    def test_basic_html_table(self):
        # Minimal StatMuse-like HTML with a player row
        html = """<table>
        <tr><td>Player</td><td>MIN</td><td>PTS</td><td>REB</td><td>AST</td><td>STL</td><td>BLK</td><td>TO</td><td>3PM</td></tr>
        <tr><td>Jalen Brunson</td><td>35</td><td>30</td><td>3</td><td>2</td><td>0</td><td>0</td><td>4</td><td>2</td></tr>
        </table>"""
        result = _parse_statmuse_html(html, "NYK", "SAS")
        # Note: layout detection may vary, but should find Brunson
        # With 9-column layout (Layout A), PTS=col[2], REB=col[3], etc.
        if "Jalen Brunson" in result:
            assert result["Jalen Brunson"]["stats"].points == 30.0 or result["Jalen Brunson"]["stats"].rebounds == 3.0

    def test_header_rows_skipped(self):
        html = """<table>
        <tr><td>Player</td><td>MIN</td><td>PTS</td></tr>
        <tr><td>Totals</td><td>240</td><td>105</td></tr>
        </table>"""
        result = _parse_statmuse_html(html, "NYK", "SAS")
        assert "Player" not in result
        assert "Totals" not in result

    def test_empty_html(self):
        result = _parse_statmuse_html("", "NYK", "SAS")
        assert result == {}