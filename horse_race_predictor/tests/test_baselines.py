"""Tests for the synthetic comparison baselines (post_position, random)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import post_position_baseline as pp  # noqa: E402
import random_baseline as rnd  # noqa: E402


def _field():
    """Production-shaped entry dicts (as from db.get_entries / HRN)."""
    return [
        {"program_number": "1", "horse_name": "Inside Edge", "morning_line_odds": 5.0, "post_position": 3},
        {"program_number": "2", "horse_name": "Rail Trip", "morning_line_odds": 3.0, "post_position": 1},
        {"program_number": "3", "horse_name": "Wide Awalker", "morning_line_odds": 2.5, "post_position": 8},
    ]


def test_post_position_ranks_lowest_pp_first():
    picks = pp.predict(_field(), top_n=3)
    assert picks[0]["horse_name"] == "Rail Trip"      # post 1
    assert picks[1]["horse_name"] == "Inside Edge"    # post 3
    assert picks[2]["horse_name"] == "Wide Awalker"   # post 8
    assert [p["rank"] for p in picks] == [1, 2, 3]
    assert all(p["source"] == pp.SOURCE_NAME for p in picks)


def test_post_position_missing_pp_sorts_last():
    field = _field()
    field.append({"program_number": "4", "horse_name": "No Post", "morning_line_odds": 10.0, "post_position": None})
    picks = pp.predict(field, top_n=4)
    assert picks[-1]["horse_name"] == "No Post"


def test_post_position_outside_picks_highest_post_first():
    picks = pp.predict_outside(_field(), top_n=3)
    assert picks[0]["horse_name"] == "Wide Awalker"   # post 8 (highest)
    assert picks[0]["source"] == pp.SOURCE_NAME_OUTSIDE
    assert [p["horse_name"] for p in picks] == ["Wide Awalker", "Inside Edge", "Rail Trip"]


def test_post_position_outside_missing_pp_sorts_last():
    field = _field()
    field.append({"program_number": "4", "horse_name": "No Post", "morning_line_odds": 10.0, "post_position": None})
    picks = pp.predict_outside(field, top_n=4)
    # Highest real post (Wide Awalker=8) is picked first; No Post is NOT treated as highest.
    assert picks[0]["horse_name"] == "Wide Awalker"
    assert picks[-1]["horse_name"] == "No Post"


def test_random_is_deterministic_and_within_field():
    field = _field()
    a = rnd.predict(field, top_n=3, seed_key="GP-1-2026-06-27")
    b = rnd.predict(field, top_n=3, seed_key="GP-1-2026-06-27")
    assert [p["horse_name"] for p in a] == [p["horse_name"] for p in b]
    names = {p["horse_name"] for p in a}
    assert names <= {e["horse_name"] for e in field}
    assert len({p["horse_name"] for p in a}) == 3   # no dupes
    assert all(p["source"] == rnd.SOURCE_NAME for p in a)


def test_random_different_seed_different_picks():
    field = _field()
    a = rnd.predict(field, top_n=3, seed_key="A")
    c = rnd.predict(field, top_n=3, seed_key="C")
    # Not guaranteed to differ on every seed, but these two were chosen to differ.
    assert [p["horse_name"] for p in a] != [p["horse_name"] for p in c]


def test_random_seed_derived_from_entries_is_stable():
    field = _field()
    a = rnd.predict(field, top_n=2)        # no seed_key -> derive from entries
    b = rnd.predict(field, top_n=2)
    assert [p["horse_name"] for p in a] == [p["horse_name"] for p in b]