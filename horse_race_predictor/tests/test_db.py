"""
Tests for horse_race_predictor/db.py - run against a temp DB via HRP_DB_PATH
(set by conftest.py before this module imports db).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import db  # noqa: E402
import race  # noqa: E402


@pytest.fixture
def fresh_db():
    """init_db on the temp DB; yield the connection helper module."""
    db.init_db()
    return db


def _sample_race():
    return race.Race(
        track_code="SAR", race_number=1, race_date="2026-07-04",
        post_time="1:00 PM", distance="6F", surface="Dirt", race_type="MCL",
    )


def _sample_entries():
    return [
        {"program_number": "1", "horse_name": "Speed Star", "jockey": "J. Ortiz",
         "trainer": "T. Pletcher", "morning_line_odds": 5.0, "post_position": 1},
        {"program_number": "2", "horse_name": "Lazy Day", "jockey": "I. Ortiz",
         "trainer": "S. Asmussen", "morning_line_odds": 3.0, "post_position": 2},
        {"program_number": "3", "horse_name": "Midnight Run", "jockey": "F. Geroux",
         "trainer": "B. Cox", "morning_line_odds": 2.5, "post_position": 3},
    ]


def test_init_and_race_roundtrip(fresh_db):
    db = fresh_db
    race_id = db.save_race(_sample_race())
    assert race_id is not None

    fetched = db.get_race(race_id)
    assert fetched["track_code"] == "SAR"
    assert fetched["race_number"] == 1
    assert fetched["race_date"] == "2026-07-04"
    assert fetched["distance"] == "6F"

    # Re-save upserts rather than duplicating
    race_id2 = db.save_race(_sample_race())
    assert race_id2 == race_id


def test_lookup_by_identity(fresh_db):
    db = fresh_db
    race_id = db.save_race(_sample_race())
    assert db.get_race_id("SAR", 1, "2026-07-04") == race_id
    assert db.get_race_id("SAR", 2, "2026-07-04") is None


def test_entries_replace_on_refetch(fresh_db):
    db = fresh_db
    race_id = db.save_race(_sample_race())
    db.save_entries(race_id, _sample_entries())
    assert len(db.get_entries(race_id)) == 3

    # Refetch with a scratch change - old rows must be replaced, not appended
    new_entries = _sample_entries()
    new_entries[0]["scratched"] = True
    db.save_entries(race_id, new_entries)
    entries = db.get_entries(race_id)
    assert len(entries) == 3  # not 6
    by_prog = {e["program_number"]: e for e in entries}
    assert by_prog["1"]["scratched"] == 1
    assert by_prog["2"]["scratched"] == 0


def test_picks_replace_per_source(fresh_db):
    db = fresh_db
    race_id = db.save_race(_sample_race())
    db.save_picks(race_id, "drf_free", [
        {"horse_name": "Speed Star", "program_number": "1", "rank": 1, "comment": "top"},
        {"horse_name": "Midnight Run", "program_number": "3", "rank": 2, "comment": ""},
    ])
    db.save_picks(race_id, "brisnet_free", [
        {"horse_name": "Lazy Day", "program_number": "2", "rank": 1, "comment": ""},
    ])
    assert len(db.get_picks(race_id)) == 3
    assert len(db.get_picks(race_id, "drf_free")) == 2

    # Re-save drf_free -> replaces that source's rows only, leaves brisnet alone
    db.save_picks(race_id, "drf_free", [
        {"horse_name": "Speed Star", "program_number": "1", "rank": 1, "comment": "new"},
    ])
    assert len(db.get_picks(race_id, "drf_free")) == 1
    assert len(db.get_picks(race_id, "brisnet_free")) == 1


def test_results_and_accuracy_roundtrip(fresh_db):
    db = fresh_db
    race_id = db.save_race(_sample_race())
    db.save_picks(race_id, "drf_free", [
        {"horse_name": "Speed Star", "program_number": "1", "rank": 1, "comment": ""},
    ])
    db.save_results(race_id, [
        {"program_number": "1", "horse_name": "Speed Star", "finish_position": 1,
         "win_payoff": 6.0, "place_payoff": 3.0, "show_payoff": 2.4},
        {"program_number": "2", "horse_name": "Lazy Day", "finish_position": 2},
        {"program_number": "3", "horse_name": "Midnight Run", "finish_position": 3},
    ])
    results = db.get_results(race_id)
    assert results[0]["finish_position"] == 1
    assert results[0]["horse_name"] == "Speed Star"

    db.save_accuracy_snapshot(race_id, "drf_free", "Speed Star", 1, 1, 1, 1)
    snap = db.get_accuracy_snapshots(race_id)[0]
    assert snap["hit_win"] == 1
    assert snap["top_pick_horse"] == "Speed Star"

    # Upsert path: re-saving the same (race,source) updates rather than duplicating
    db.save_accuracy_snapshot(race_id, "drf_free", "Speed Star", 1, 1, 1, 1)
    assert len(db.get_accuracy_snapshots(race_id)) == 1

    scored = db.get_scored_races()
    assert len(scored) == 1
    assert scored[0]["id"] == race_id


def test_entries_dedup_duplicate_program_number(fresh_db):
    """Duplicate program numbers (coupled entries / parser dups) must not violate
    the (race_id, program_number) UNIQUE constraint - keep the first, drop dupes."""
    db = fresh_db
    race_id = db.save_race(_sample_race())
    entries = _sample_entries() + [
        {"program_number": "1", "horse_name": "Coupled Twin", "jockey": "X",
         "trainer": "Y", "morning_line_odds": 5.0, "post_position": 1},  # dup prog "1"
        {"program_number": "4", "horse_name": " Lone Four", "jockey": "Z",
         "trainer": "W", "morning_line_odds": 10.0, "post_position": 4},
    ]
    db.save_entries(race_id, entries)  # must not raise
    by_prog = {e["program_number"]: e for e in db.get_entries(race_id)}
    assert set(by_prog) == {"1", "2", "3", "4"}   # dup "1" dropped, "4" kept
    assert by_prog["1"]["horse_name"] == "Speed Star"  # first occurrence wins