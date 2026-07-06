"""Tests for horse_race_predictor/accuracy.py - uses a temp DB via HRP_DB_PATH
(set by conftest.py before this module imports db).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import db  # noqa: E402
import accuracy  # noqa: E402
import consensus  # noqa: E402
from race import Entry  # noqa: E402


def _setup_race(entries, picks, results):
    db.init_db()
    # Build a Race via db.save_race using a lightweight object
    class _R:
        track_code = "TST"
        race_number = 1
        race_date = "2026-07-04"
        post_time = ""
        distance = ""
        surface = ""
        race_type = ""
    race_id = db.save_race(_R())
    db.save_entries(race_id, [e.to_dict() if isinstance(e, Entry) else e for e in entries])
    # Save picks grouped by source
    by_src = {}
    for p in picks:
        by_src.setdefault(p["source"], []).append(p)
    for src, ps in by_src.items():
        db.save_picks(race_id, src, ps)
    db.save_results(race_id, results)
    return race_id


def test_per_source_and_consensus_scoring():
    entries = [
        Entry("1", "Speed Star", morning_line_odds=5.0, post_position=1),
        Entry("2", "Lazy Day", morning_line_odds=3.0, post_position=2),
        Entry("3", "Midnight Run", morning_line_odds=2.5, post_position=3),
    ]
    picks = [
        {"source": "A", "horse_name": "Speed Star", "program_number": "1", "rank": 1},
        {"source": "A", "horse_name": "Lazy Day", "program_number": "2", "rank": 2},
        {"source": "B", "horse_name": "Midnight Run", "program_number": "3", "rank": 1},
        {"source": "B", "horse_name": "Speed Star", "program_number": "1", "rank": 2},
    ]
    # Finish: Midnight Run(3) wins, Speed Star(1) 2nd, Lazy Day(2) 3rd
    results = [
        {"program_number": "3", "horse_name": "Midnight Run", "finish_position": 1},
        {"program_number": "1", "horse_name": "Speed Star", "finish_position": 2},
        {"program_number": "2", "horse_name": "Lazy Day", "finish_position": 3},
    ]
    race_id = _setup_race(entries, picks, results)
    snaps = accuracy.run_accuracy_checks(race_id)
    by_src = {s["source"]: s for s in snaps}

    # Source A top pick = Speed Star -> finished 2nd -> place+show hit, no win
    assert by_src["A"]["top_pick"] == "Speed Star"
    assert by_src["A"]["finish"] == 2
    assert by_src["A"]["hit_win"] == 0
    assert by_src["A"]["hit_place"] == 1
    assert by_src["A"]["hit_show"] == 1
    # Source B top pick = Midnight Run -> won
    assert by_src["B"]["top_pick"] == "Midnight Run"
    assert by_src["B"]["hit_win"] == 1
    # Consensus: Speed Star (A=5,B=3=8) vs Midnight Run (B=5=5) -> Speed Star best
    assert by_src["consensus"]["top_pick"] == "Speed Star"
    assert by_src["consensus"]["finish"] == 2
    assert by_src["consensus"]["hit_win"] == 0


def test_summary_aggregation():
    # Reuses DB from the previous test (same HRP_DB_PATH) - one scored race
    rows = accuracy.summary()
    srcs = {r["source"]: r for r in rows}
    assert "A" in srcs and "B" in srcs and "consensus" in srcs
    assert srcs["B"]["wins"] == 1
    assert srcs["A"]["wins"] == 0
    out = accuracy.format_summary(rows)
    assert "Source" in out and "Win%" in out


def test_no_results_returns_empty():
    entries = [Entry("1", "Solo Star", post_position=1)]
    picks = [{"source": "A", "horse_name": "Solo Star", "program_number": "1", "rank": 1}]
    race_id = _setup_race(entries, picks, results=[])
    # save_results with [] leaves no rows
    assert accuracy.run_accuracy_checks(race_id) == []