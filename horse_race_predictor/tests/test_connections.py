"""Tests for standings-as-of and the leading jockey/trainer predictors.

Uses the shared temp DB (HRP_DB_PATH set by conftest.py).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import db  # noqa: E402
import standings  # noqa: E402
import connections_baseline as cb  # noqa: E402
from race import Entry  # noqa: E402


def _save_race(track, num, date, entries, winner_prog):
    class _R:
        pass
    _R.track_code = track
    _R.race_number = num
    _R.race_date = date
    _R.post_time = _R.distance = _R.surface = _R.race_type = ""
    rid = db.save_race(_R())
    db.save_entries(rid, [e.to_dict() if isinstance(e, Entry) else e for e in entries])
    results = []
    for e in entries:
        prog = e.program_number if isinstance(e, Entry) else e["program_number"]
        fin = 1 if prog == winner_prog else (2 if prog != winner_prog else None)
        # only the winner gets a finish entry; others omitted (top-3 not needed here)
        if prog == winner_prog:
            results.append({"program_number": prog,
                            "horse_name": e.horse_name if isinstance(e, Entry) else e["horse_name"],
                            "finish_position": 1})
    db.save_results(rid, results)
    return rid


def test_standings_as_of_excludes_same_day_and_future():
    db.init_db()
    # Day 1: jockey A wins (prog 1). Day 2: jockey B wins (prog 2).
    _save_race("TST", 1, "2026-06-20",
               [Entry("1", "A-Horse", jockey="A. Rider", trainer="A. Trainer"),
                Entry("2", "B-Horse", jockey="B. Rider", trainer="B. Trainer")],
               winner_prog="1")
    _save_race("TST", 1, "2026-06-21",
               [Entry("1", "C-Horse", jockey="A. Rider", trainer="X. Trainer"),
                Entry("2", "D-Horse", jockey="B. Rider", trainer="Y. Trainer")],
               winner_prog="2")
    # As of 2026-06-21 (strict <): only 6/20 counts -> A. Rider has 1 win, B has 0.
    j, t = standings.meet_standings_as_of("TST", "2026-06-21")
    assert j == {"A. Rider": 1}
    # As of 2026-06-22: both days count -> A=1, B=1.
    j2, _ = standings.meet_standings_as_of("TST", "2026-06-22")
    assert j2 == {"A. Rider": 1, "B. Rider": 1}


def test_leader_tiebreak_by_name():
    assert standings.leader({}) is None
    name, cnt = standings.leader({"Zed": 2, "Amy": 2, "Bob": 1})
    assert cnt == 2 and name == "Amy"   # tie -> name ascending


def test_leading_jockey_picks_leaders_mount_and_abstains_when_no_data():
    db.init_db()
    # Seed a prior race where jockey "A. Rider" won.
    _save_race("TST", 1, "2026-06-20",
               [Entry("1", "A-Horse", jockey="A. Rider", trainer="A. Trainer"),
                Entry("2", "B-Horse", jockey="B. Rider", trainer="B. Trainer")],
               winner_prog="1")
    # Today's race (6/21): A. Rider is on prog 3.
    today = [
        {"program_number": "3", "horse_name": "C-Horse", "jockey": "A. Rider", "trainer": "Z. Trainer", "post_position": 1},
        {"program_number": "4", "horse_name": "D-Horse", "jockey": "B. Rider", "trainer": "Y. Trainer", "post_position": 2},
    ]
    picks = cb.predict_leading_jockey(today, track_code="TST", race_date="2026-06-21", top_n=3)
    assert picks[0]["source"] == cb.SOURCE_JOCKEY
    assert picks[0]["horse_name"] == "C-Horse"     # leader A. Rider's mount
    assert picks[0]["rank"] == 1
    # No prior data at a brand-new track -> abstain (empty).
    none = cb.predict_leading_jockey(today, track_code="NEW", race_date="2026-06-21")
    assert none == []


def test_leading_trainer_abstains_when_leader_has_no_mount():
    db.init_db()
    _save_race("TST", 1, "2026-06-20",
               [Entry("1", "A-Horse", jockey="A. Rider", trainer="Top Trainer"),
                Entry("2", "B-Horse", jockey="B. Rider", trainer="Other Trainer")],
               winner_prog="1")
    # Today's race has no horse trained by "Top Trainer" -> abstain.
    today = [
        {"program_number": "1", "horse_name": "X", "jockey": "J", "trainer": "Someone Else", "post_position": 1},
    ]
    picks = cb.predict_leading_trainer(today, track_code="TST", race_date="2026-06-21")
    assert picks == []