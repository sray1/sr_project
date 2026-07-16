"""Tests for the expert-vs-baseline tracker (expert_tracker.py)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import db  # noqa: E402
import accuracy as acc  # noqa: E402
import expert_tracker as et  # noqa: E402
from race import Race  # noqa: E402


def _race(n=1, date="2026-07-15", track="IND"):
    return Race.from_inputs(track, n, date)


def _entries():
    return [
        {"program_number": "1", "horse_name": "Figurine", "morning_line_odds": 1.6, "post_position": 1},
        {"program_number": "2", "horse_name": "Voluntold", "morning_line_odds": 4.0, "post_position": 2},
        {"program_number": "3", "horse_name": "Oondiri", "morning_line_odds": 6.0, "post_position": 3},
    ]


def _setup_one_race():
    """One race with an expert pick, baseline picks, and a result the expert hits."""
    db.init_db()
    race_id = db.save_race(_race())
    db.save_entries(race_id, _entries())
    # Expert picks Voluntold (#2) to win; baseline mlo_baseline picks Figurine (#1).
    db.save_picks(race_id, "ultimatecapper",
                  [{"horse_name": "Voluntold", "program_number": "2", "rank": 1, "comment": ""}])
    db.save_picks(race_id, "ehlers_drf",
                  [{"horse_name": "Figurine", "program_number": "1", "rank": 1, "comment": ""}])
    # Result: Voluntold wins at $7.40.
    db.save_results(race_id, [
        {"program_number": "2", "horse_name": "Voluntold", "finish_position": 1,
         "win_payoff": 7.40, "place_payoff": 4.0, "show_payoff": 2.8},
        {"program_number": "1", "horse_name": "Figurine", "finish_position": 2},
        {"program_number": "3", "horse_name": "Oondiri", "finish_position": 3},
    ])
    acc.run_accuracy_checks(race_id)
    return race_id


def test_expert_sources_excludes_baselines_and_consensus():
    """leading_jockey / leading_trainer / consensus / canonical baselines are NOT
    experts; only human/site pick sources are."""
    _setup_one_race()
    # Add a connection-baseline pick so leading_jockey is present in the picks table.
    race_id = db.get_race_id("IND", 1, "2026-07-15")
    db.save_picks(race_id, "leading_jockey",
                  [{"horse_name": "Figurine", "program_number": "1", "rank": 1, "comment": ""}])
    acc.run_accuracy_checks(race_id)
    experts = et._expert_sources()
    assert "leading_jockey" not in experts
    assert "consensus" not in experts
    assert "mlo_baseline" not in experts
    assert "ultimatecapper" in experts
    assert "ehlers_drf" in experts


def test_ensure_baselines_backfills_canonical_slate():
    """A race with only expert picks gets the canonical baselines added + rescored."""
    race_id = _setup_one_race()
    # Wipe any baselines that run_accuracy_checks may have left — start with only
    # the expert picks so ensure_baselines has work to do.
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM picks WHERE race_id=? AND source IN "
                "('mlo_baseline','mlo_second','mlo_third','mlo_longshot',"
                "'post_position_baseline','post_position_outside','random_baseline')", (race_id,))
    conn.commit(); conn.close()
    et.ensure_baselines(race_id)
    have = {p["source"] for p in db.get_picks(race_id)}
    for b in ("mlo_baseline", "mlo_second", "mlo_third", "mlo_longshot",
              "post_position_baseline", "post_position_outside", "random_baseline"):
        assert b in have, f"{b} missing after ensure_baselines"


def test_tally_roi_and_hits():
    """Expert who picked the winner gets a win + positive ROI; the favorite
    baseline that picked the runner-up gets a place hit and a -$2 loss."""
    _setup_one_race()
    race_id = db.get_race_id("IND", 1, "2026-07-15")
    et.ensure_baselines(race_id)
    experts = et._expert_sources()
    races = et._expert_races(experts)
    per_source, per_race = et.tally(races)

    # ultimatecapper picked Voluntold, the $7.40 winner -> +$5.40 ROI on $2.
    uc = per_source["ultimatecapper"]
    assert uc["races"] == 1 and uc["wins"] == 1
    assert uc["wagered"] == 2.0 and abs(uc["pl"] - 5.40) < 0.01

    # ehlers_drf picked Figurine, who ran 2nd -> place hit, -$2 on the win bet.
    eh = per_source["ehlers_drf"]
    assert eh["wins"] == 0 and eh["places"] == 1
    assert abs(eh["pl"] - (-2.0)) < 0.01

    # mlo_baseline (canonical) also picked the favorite Figurine -> place, -$2.
    mb = per_source["mlo_baseline"]
    assert mb["places"] == 1 and abs(mb["pl"] - (-2.0)) < 0.01

    # per_race carries the winner + per-source top pick / finish. Other test
    # modules share this temp DB and may save pick-source stubs (drf_free,
    # brisnet_free) that count as experts, so locate THIS race rather than
    # asserting a single-row list.
    mine = [pr for pr in per_race
            if pr["track"] == "IND" and pr["date"] == "2026-07-15" and pr["race_number"] == 1]
    assert len(mine) == 1
    pr = mine[0]
    assert pr["winner"][0] == "Voluntold"
    assert pr["picks"]["ultimatecapper"]["hit_win"] == 1
    assert pr["picks"]["ehlers_drf"]["finish"] == 2


def test_legacy_mlo_favorite_alias_folds_into_mlo_baseline():
    """A prior session saved the MLO favorite under the label `mlo_favorite`;
    the tally must fold it into the canonical `mlo_baseline` row, not show both."""
    race_id = _setup_one_race()
    # Save the same favorite pick under the legacy label and score it.
    db.save_picks(race_id, "mlo_favorite",
                  [{"horse_name": "Figurine", "program_number": "1", "rank": 1, "comment": ""}])
    acc.run_accuracy_checks(race_id)
    et.ensure_baselines(race_id)
    experts = et._expert_sources()
    assert "mlo_favorite" not in experts
    races = et._expert_races(experts)
    per_source, _ = et.tally(races)
    # Only the canonical mlo_baseline row should exist (alias folded in).
    assert "mlo_favorite" not in per_source
    assert "mlo_baseline" in per_source