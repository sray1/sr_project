"""Tests for the MLO-family variant predictors (2nd / 3rd / longshot)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mlo_variants as mv  # noqa: E402


def _field():
    """Ordered by MLO ascending: A(2.0) fav, B(3.0), C(5.0), D(20.0) longshot."""
    return [
        {"program_number": "1", "horse_name": "A", "morning_line_odds": 2.0, "post_position": 1},
        {"program_number": "2", "horse_name": "B", "morning_line_odds": 3.0, "post_position": 2},
        {"program_number": "3", "horse_name": "C", "morning_line_odds": 5.0, "post_position": 3},
        {"program_number": "4", "horse_name": "D", "morning_line_odds": 20.0, "post_position": 4},
    ]


def test_second_picks_the_2nd_favorite():
    picks = mv.predict_second(_field(), top_n=3)
    assert picks[0]["horse_name"] == "B"          # 2nd-lowest MLO
    assert picks[0]["source"] == mv.SOURCE_2ND
    assert [p["horse_name"] for p in picks] == ["B", "C", "D"]


def test_third_picks_the_3rd_favorite():
    picks = mv.predict_third(_field(), top_n=3)
    assert picks[0]["horse_name"] == "C"
    assert picks[0]["source"] == mv.SOURCE_3RD


def test_longshot_picks_highest_mlo_first():
    picks = mv.predict_longshot(_field(), top_n=3)
    assert picks[0]["horse_name"] == "D"          # 20.0 = longest shot
    assert picks[0]["source"] == mv.SOURCE_LONGSHOT
    assert [p["horse_name"] for p in picks] == ["D", "C", "B"]


def test_longshot_treats_missing_mlo_as_last_not_longest():
    field = _field()
    field.append({"program_number": "5", "horse_name": "NoOdds", "morning_line_odds": None, "post_position": 5})
    picks = mv.predict_longshot(field, top_n=4)
    # Highest real MLO (D=20.0) is the longshot; NoOdds (missing MLO) is NOT picked first.
    assert picks[0]["horse_name"] == "D"
    assert "NoOdds" not in [p["horse_name"] for p in picks[:3]]


def test_second_with_tiny_field_returns_empty_or_short():
    picks = mv.predict_second([_field()[0]], top_n=3)   # only the favorite exists
    assert picks == []                                   # no 2nd choice available