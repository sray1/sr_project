"""Tests for prediction tracker module — game result fetching and display logic."""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from draftkings_scoring import PlayerStats
from prediction_tracker import (
    fetch_game_results, display_actual_scores, display_lineup_comparison,
    display_best_possible_lineup, save_results_to_db,
)


# Sample data for testing
SAMPLE_PLAYER_SCORES = {
    "Victor Wembanyama": {"fppg": 51.5, "salary": 10300, "team": "SAS"},
    "Karl-Anthony Towns": {"fppg": 41.5, "salary": 8300, "team": "NYK"},
    "Josh Hart": {"fppg": 40.8, "salary": 8150, "team": "NYK"},
    "Jalen Brunson": {"fppg": 35.8, "salary": 7150, "team": "NYK"},
}

SAMPLE_ACTUAL_DATA = {
    "Victor Wembanyama": {
        "stats": PlayerStats(points=26, rebounds=12, assists=2, steals=1, blocks=3, turnovers=6, three_pointers=2),
        "team": "SAS", "minutes": 32.0,
    },
    "Karl-Anthony Towns": {
        "stats": PlayerStats(points=18, rebounds=12, assists=4, steals=0, blocks=1, turnovers=2, three_pointers=0),
        "team": "NYK", "minutes": 36.0,
    },
    "Josh Hart": {
        "stats": PlayerStats(points=3, rebounds=15, assists=6, steals=4, blocks=1, turnovers=0, three_pointers=0),
        "team": "NYK", "minutes": 38.0,
    },
    "Jalen Brunson": {
        "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
        "team": "NYK", "minutes": 35.0,
    },
}

SAMPLE_PREDICTED_LINEUPS = [
    {
        "captain": "Josh Hart",
        "captain_salary": 8150,
        "utility": [
            ("Victor Wembanyama", 10300),
            ("Karl-Anthony Towns", 8300),
            ("Jalen Brunson", 7150),
        ],
    },
]

SAMPLE_PROJECTED_FPPG = {
    "Victor Wembanyama": 56.0,
    "Karl-Anthony Towns": 47.2,
    "Josh Hart": 35.8,
    "Jalen Brunson": 47.2,
}


class TestFetchGameResults:
    """Test the fetch_game_results function with mocked API calls."""

    @patch('prediction_tracker.confirm_game_played')
    @patch('prediction_tracker.fetch_box_score')
    @patch('prediction_tracker.fetch_statmuse_box_score')
    def test_game_not_played(self, mock_statmuse, mock_box_score, mock_confirm):
        """Should return None when game hasn't been played."""
        mock_confirm.return_value = {"played": False, "final_score": None, "source": None}

        result = fetch_game_results(away_team="NYK", home_team="SAS", date="2026-07-01")
        assert result is None or result[0] is None

    @patch('prediction_tracker.confirm_game_played')
    @patch('prediction_tracker.fetch_box_score')
    @patch('prediction_tracker.fetch_statmuse_box_score')
    def test_nba_api_success(self, mock_statmuse, mock_box_score, mock_confirm):
        """Should use nba_api data when available."""
        mock_confirm.return_value = {"played": True, "final_score": "NYK 105 - SAS 95", "source": "box_score"}
        mock_box_score.return_value = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
        }

        result = fetch_game_results(away_team="NYK", home_team="SAS", date="2026-06-03")
        assert result is not None
        player_scores, actual_data, game_info = result
        assert "Jalen Brunson" in player_scores
        assert player_scores["Jalen Brunson"]["fppg"] > 0
        assert game_info["played"] is True

    @patch('prediction_tracker.confirm_game_played')
    @patch('prediction_tracker.fetch_box_score')
    @patch('prediction_tracker.fetch_statmuse_box_score')
    def test_statmuse_fallback(self, mock_statmuse, mock_box_score, mock_confirm):
        """Should fall back to StatMuse when nba_api fails."""
        mock_confirm.return_value = {"played": True, "final_score": "NYK 105 - SAS 95", "source": "box_score"}
        mock_box_score.return_value = None  # nba_api fails
        mock_statmuse.return_value = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
        }

        result = fetch_game_results(away_team="NYK", home_team="SAS", date="2026-06-03")
        assert result is not None
        player_scores, actual_data, game_info = result
        assert "Jalen Brunson" in player_scores


class TestDisplayLineupComparison:
    """Test lineup comparison display (captures stdout)."""

    def test_comparison_with_data(self, capsys):
        result = display_lineup_comparison(
            SAMPLE_PLAYER_SCORES, SAMPLE_ACTUAL_DATA,
            SAMPLE_PREDICTED_LINEUPS, SAMPLE_PROJECTED_FPPG
        )
        captured = capsys.readouterr()
        # Should contain player names and totals
        assert "Josh Hart" in captured.out
        assert "Victor Wembanyama" in captured.out
        assert "TOTAL" in captured.out
        # Result should be (best_lineup_num, best_actual_total)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_comparison_empty_lineups(self, capsys):
        result = display_lineup_comparison(
            SAMPLE_PLAYER_SCORES, SAMPLE_ACTUAL_DATA, [], {}
        )
        captured = capsys.readouterr()
        assert "No predicted lineups" in captured.out
        assert result == (0, 0)


class TestDisplayActualScores:
    """Test actual scores display."""

    def test_displays_player_scores(self, capsys):
        display_actual_scores(SAMPLE_PLAYER_SCORES, SAMPLE_ACTUAL_DATA)
        captured = capsys.readouterr()
        assert "Victor Wembanyama" in captured.out
        assert "Jalen Brunson" in captured.out
        assert "UTIL" in captured.out
        assert "CPT" in captured.out

    def test_double_double_annotation(self, capsys):
        # Karl-Anthony Towns: 18pts/12reb = double-double
        display_actual_scores(SAMPLE_PLAYER_SCORES, SAMPLE_ACTUAL_DATA)
        captured = capsys.readouterr()
        assert "DOUBLE-DOUBLE" in captured.out


class TestDisplayBestPossibleLineup:
    """Test best possible lineup display."""

    def test_finds_best_lineup(self, capsys):
        """Need enough players to form a valid 6-player lineup."""
        # Build a larger player_scores dict with salary >= 3000 for all
        extended_scores = {
            "Victor Wembanyama": {"fppg": 51.5, "salary": 10300, "team": "SAS"},
            "Karl-Anthony Towns": {"fppg": 41.5, "salary": 8300, "team": "NYK"},
            "Josh Hart": {"fppg": 40.8, "salary": 8150, "team": "NYK"},
            "Jalen Brunson": {"fppg": 35.8, "salary": 7150, "team": "NYK"},
            "Julian Champagnie": {"fppg": 36.0, "salary": 7200, "team": "SAS"},
            "Stephon Castle": {"fppg": 31.0, "salary": 8600, "team": "SAS"},
            "OG Anunoby": {"fppg": 26.2, "salary": 5250, "team": "NYK"},
            "Devin Vassell": {"fppg": 24.8, "salary": 4950, "team": "SAS"},
            "Mikal Bridges": {"fppg": 20.8, "salary": 6600, "team": "NYK"},
            "De'Aaron Fox": {"fppg": 20.0, "salary": 7600, "team": "SAS"},
        }
        result = display_best_possible_lineup(extended_scores)
        captured = capsys.readouterr()
        assert "BEST POSSIBLE LINEUP" in captured.out
        assert "Captain" in captured.out
        # Should return a positive fppg value
        assert result > 0

    def test_insufficient_players(self, capsys):
        """With only 4 players, no valid lineup can be formed."""
        result = display_best_possible_lineup(SAMPLE_PLAYER_SCORES)
        captured = capsys.readouterr()
        assert "BEST POSSIBLE LINEUP" in captured.out
        assert result == 0


class TestGameInfoIntegration:
    """Test game_info dict is properly created and passed through."""

    @patch('prediction_tracker.confirm_game_played')
    @patch('prediction_tracker.fetch_box_score')
    @patch('prediction_tracker.fetch_statmuse_box_score')
    def test_game_info_contains_score(self, mock_statmuse, mock_box_score, mock_confirm):
        mock_confirm.return_value = {
            "played": True,
            "final_score": "NYK 105 - SAS 95",
            "source": "box_score",
        }
        mock_box_score.return_value = {
            "Jalen Brunson": {
                "stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=4, three_pointers=2),
                "team": "NYK", "minutes": 35.0,
            },
        }

        result = fetch_game_results(away_team="NYK", home_team="SAS", date="2026-06-03")
        assert result is not None
        _, _, game_info = result
        assert game_info["final_score"] == "NYK 105 - SAS 95"
        assert game_info["played"] is True