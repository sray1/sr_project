"""Tests for horse_race_predictor/consensus.py - pure logic, no network."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import consensus  # noqa: E402
from race import Entry  # noqa: E402


def _entries():
    return [
        Entry("1", "Speed Star", "J. Ortiz", "T. Pletcher", 5.0, 1),
        Entry("2", "Lazy Day", "I. Ortiz", "S. Asmussen", 3.0, 2),
        Entry("3", "Midnight Run", "F. Geroux", "B. Cox", 2.5, 3),
        Entry("4", "Long Shot Lou", "R. Santana", "D. Lukas", 20.0, 4),
    ]


def test_basic_rank_points():
    entries = _entries()
    picks = [
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1, "comment": ""},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2, "comment": ""},
        {"source": "A", "horse_name": "Midnight Run", "program_number": "3", "rank": 3, "comment": ""},
    ]
    r = consensus.aggregate(entries, picks)
    # 5 / 3 / 1
    by_prog = {row["program_number"]: row for row in r["rows"]}
    assert by_prog["1"]["points"] == 5
    assert by_prog["2"]["points"] == 3
    assert by_prog["3"]["points"] == 1
    assert by_prog["4"]["points"] == 0
    assert r["best_pick"]["program_number"] == "1"
    assert r["num_sources"] == 1
    assert r["margin"] == 2  # 5 - 3


def test_aggregation_across_sources_and_votes():
    entries = _entries()
    picks = [
        # Source A: 1 > 2 > 3
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2},
        {"source": "A", "horse_name": "Midnight Run", "program_number": "3", "rank": 3},
        # Source B: 3 > 1 > 4
        {"source": "B", "horse_name": "Midnight Run", "program_number": "3", "rank": 1},
        {"source": "B", "horse_name": "Speed Star", "program_number": "1", "rank": 2},
        {"source": "B", "horse_name": "Long Shot Lou", "program_number": "4", "rank": 3},
        # Source C: 1 only (single top pick)
        {"source": "C", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
    ]
    r = consensus.aggregate(entries, picks)
    by_prog = {row["program_number"]: row for row in r["rows"]}
    # Speed Star: A=5, B=3, C=5 => 13 ; #1 votes: A,C => 2
    assert by_prog["1"]["points"] == 13
    assert by_prog["1"]["first_votes"] == 2
    # Midnight Run: A=1, B=5 => 6 ; #1 votes: B => 1
    assert by_prog["3"]["points"] == 6
    assert by_prog["3"]["first_votes"] == 1
    assert r["best_pick"]["program_number"] == "1"
    assert r["num_sources"] == 3
    # Confidence: 2 of 3 sources named the best pick #1
    assert abs(r["confidence"] - 2 / 3) < 1e-9


def test_tiebreak_by_first_votes_then_mlo():
    entries = _entries()
    # Speed Star (MLO 5.0) and Midnight Run (MLO 2.5) each get 5 pts from one
    # source as #1. Tie on points (5 each), tie on first_votes (1 each) ->
    # lower MLO wins -> Midnight Run (2.5).
    picks = [
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "B", "horse_name": "Midnight Run", "program_number": "3", "rank": 1},
    ]
    r = consensus.aggregate(entries, picks)
    assert r["best_pick"]["program_number"] == "3"
    assert r["confidence"] == 0.5


def test_fuzzy_name_match_when_prog_missing():
    entries = _entries()
    # Source omits program number and adds a country suffix + different case
    picks = [
        {"source": "A", "horse_name": "Speed Star (IRE)", "program_number": "", "rank": 1},
        {"source": "A", "horse_name": "midnight run", "program_number": "", "rank": 2},
    ]
    r = consensus.aggregate(entries, picks)
    by_prog = {row["program_number"]: row for row in r["rows"]}
    assert by_prog["1"]["points"] == 5  # matched to Speed Star via fuzzy name
    assert by_prog["3"]["points"] == 3  # matched to Midnight Run
    assert r["best_pick"]["program_number"] == "1"


def test_unmatched_picks_collected():
    entries = _entries()
    picks = [
        {"source": "A", "horse_name": "Ghost Horse", "program_number": "9", "rank": 1},
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 2},
    ]
    r = consensus.aggregate(entries, picks)
    assert len(r["unmatched_picks"]) == 1
    assert r["unmatched_picks"][0]["horse_name"] == "Ghost Horse"
    # Best pick is still Speed Star (3 pts, the only matched pick)
    assert r["best_pick"]["program_number"] == "1"


def test_no_picks_resolved():
    entries = _entries()
    r = consensus.aggregate(entries, [])
    assert r["best_pick"] is None
    assert r["num_sources"] == 0
    assert r["confidence"] == 0.0
    # Rows still present (all zero points) so the table can render entries-only
    assert len(r["rows"]) == 4


def test_deep_rank_scores_zero_not_first_place():
    """An explicit 4th choice is NOT an unranked mention: zero points, no #1
    vote. (Regression: rank>=4 used to be promoted to a first-place vote.)"""
    entries = _entries()
    picks = [
        # Source A ranks 4 deep: 1 > 2 > 3 > 4
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2},
        {"source": "A", "horse_name": "Midnight Run", "program_number": "3", "rank": 3},
        {"source": "A", "horse_name": "Long Shot Lou", "program_number": "4", "rank": 4},
    ]
    r = consensus.aggregate(entries, picks)
    by_prog = {row["program_number"]: row for row in r["rows"]}
    assert by_prog["4"]["points"] == 0
    assert by_prog["4"]["first_votes"] == 0
    assert r["num_sources"] == 1

    # A genuinely unranked mention (rank=None) still scores as a #1 vote.
    picks2 = [
        {"source": "B", "horse_name": "Long Shot Lou", "program_number": "4", "rank": None},
    ]
    r2 = consensus.aggregate(entries, picks2)
    by_prog2 = {row["program_number"]: row for row in r2["rows"]}
    assert by_prog2["4"]["points"] == 5
    assert by_prog2["4"]["first_votes"] == 1


def test_format_table_runs():
    entries = _entries()
    picks = [
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2},
    ]
    r = consensus.aggregate(entries, picks)
    out = consensus.format_table(r, max_rows=3)
    assert "Speed Star" in out
    assert "Pts" in out