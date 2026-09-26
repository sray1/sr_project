"""Tests for horse_race_predictor/common_underneath.py - pure logic, no network."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import common_underneath as cu  # noqa: E402
from race import Entry  # noqa: E402


def _entries():
    return [
        Entry("1", "Speed Star", "J. Ortiz", "T. Pletcher", 5.0, 1),
        Entry("2", "Lazy Day", "I. Ortiz", "S. Asmussen", 3.0, 2),
        Entry("3", "Midnight Run", "F. Geroux", "B. Cox", 2.5, 3),
        Entry("4", "Long Shot Lou", "R. Santana", "D. Lukas", 20.0, 4),
    ]


def test_deepest_common_ballot_horse():
    """Both sources name 1 and 3 on every ballot; 3 is the deeper mention."""
    entries = _entries()
    picks = [
        # A: 1 > 2 > 3
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1, "comment": ""},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2, "comment": ""},
        {"source": "A", "horse_name": "Midnight Run", "program_number": "3", "rank": 3, "comment": ""},
        # B: 3 > 1 > 4
        {"source": "B", "horse_name": "Midnight Run", "program_number": "3", "rank": 1},
        {"source": "B", "horse_name": "Speed Star", "program_number": "1", "rank": 2},
        {"source": "B", "horse_name": "Long Shot Lou", "program_number": "4", "rank": 3},
    ]
    out = cu.predict(entries, picks)
    assert len(out) == 1
    # Common to both ballots: #1 (ranks 1,2 -> avg 1.5) and #3 (ranks 3,1
    # -> avg 2.0). Deepest average is #3.
    assert out[0]["program_number"] == "3"
    assert out[0]["source"] == cu.SOURCE_NAME
    assert out[0]["rank"] == 1


def test_no_common_horse_abstains():
    entries = _entries()
    picks = [
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "B", "horse_name": "Lazy Day", "program_number": "2", "rank": 1},
    ]
    assert cu.predict(entries, picks) == []


def test_single_ballot_abstains():
    """One ballot's 3rd choice is not a shared afterthought."""
    entries = _entries()
    picks = [
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2},
        {"source": "A", "horse_name": "Midnight Run", "program_number": "3", "rank": 3},
    ]
    assert cu.predict(entries, picks) == []


def test_non_expert_sources_filtered():
    """Baselines and consensus must not count as ballots."""
    entries = _entries()
    picks = [
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "B", "horse_name": "Speed Star", "program_number": "1", "rank": 3},
        # These three are the only other mentions; if counted as ballots,
        # the common set would change.
        {"source": "mlo_favorite", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "consensus", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "random_baseline", "horse_name": "Long Shot Lou", "program_number": "4", "rank": 1},
    ]
    out = cu.predict(entries, picks)
    # A: {1:1}, B: {1:3} -> common {1}, only candidate -> #1
    assert len(out) == 1
    assert out[0]["program_number"] == "1"


def test_tiebreak_by_price_then_prog():
    """Equal average depth -> higher morning line wins the tiebreak."""
    entries = _entries()
    picks = [
        # A: 1 > 2 ; B: 2 > 1  -> both common, both avg 1.5
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2},
        {"source": "B", "horse_name": "Lazy Day", "program_number": "2", "rank": 1},
        {"source": "B", "horse_name": "Speed Star", "program_number": "1", "rank": 2},
    ]
    out = cu.predict(entries, picks)
    # Lazy Day 3.0 > Speed Star 5.0? No: higher MLO is the deeper price.
    # Speed Star (5.0) beats Lazy Day (3.0) on the price tiebreak.
    assert out[0]["program_number"] == "1"


def test_deep_rank_and_unranked_mentions():
    """A 4th choice is deeper than a 3rd; an unranked mention counts as 4."""
    entries = _entries()
    picks = [
        # A: 1 > 3 > 2 > 4
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "A", "horse_name": "Midnight Run", "program_number": "3", "rank": 2},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 3},
        {"source": "A", "horse_name": "Long Shot Lou", "program_number": "4", "rank": 4},
        # B: 1 > 3 > 2 > (unranked 4)
        {"source": "B", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "B", "horse_name": "Midnight Run", "program_number": "3", "rank": 3},
        {"source": "B", "horse_name": "Lazy Day", "program_number": "2", "rank": 3},
        {"source": "B", "horse_name": "Long Shot Lou", "program_number": "4", "rank": None},
    ]
    out = cu.predict(entries, picks)
    # Common: 1 (1,1=1.0), 3 (2,3=2.5), 2 (3,3=3.0), 4 (4,None->4=4.0)
    # Deepest average is #4.
    assert out[0]["program_number"] == "4"