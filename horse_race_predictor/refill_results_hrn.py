"""
Refill official results from HRN (direct) for already-predicted races.

Replaces whatever results were stored (BloodHorse top-3 / Equibase-parse
gap-fill) with HRN's full top-4 finish order + $2 WPS payoffs, then recomputes
accuracy. This is the migration script for switching the results source to HRN
direct; it makes ROI computable over every race (payoffs for all, not just the
Equibase subset) and resolves 4th-place finish positions.

Idempotent: save_results replaces per race, run_accuracy_checks upserts per
(race, source). One HRN page fetch per (track, date).

Usage:
  python horse_race_predictor/refill_results_hrn.py
"""

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import accuracy as accuracy_mod
from race import Race
from sources import hrn


def refill():
    db.init_db()
    # All races we predicted (have entries + picks), grouped by (track, date).
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, track_code, race_number, race_date FROM races ORDER BY race_date, track_code, race_number")
    races = [dict(r) for r in cur.fetchall()]
    conn.close()

    by_card = defaultdict(list)
    for r in races:
        by_card[(r["track_code"], r["race_date"])].append(r)

    n_filled = 0
    n_cards = 0
    for (track, date), card_races in sorted(by_card.items()):
        card = hrn.fetch_results_card(track, date)
        n_cards += 1
        if not card:
            print(f"  {track} {date}: no HRN results (dark or not yet run)")
            continue
        matched = 0
        for r in card_races:
            results = card.get(r["race_number"])
            if not results:
                continue
            results = _attach_prog(results, db.get_entries(r["id"]))
            db.save_results(r["id"], results)
            accuracy_mod.run_accuracy_checks(r["id"])
            matched += 1
            n_filled += 1
        print(f"  {track} {date}: {matched}/{len(card_races)} races refilled")
    print(f"\nDone: {n_filled} races refilled from HRN across {n_cards} cards.")


def _attach_prog(results, entries):
    """Fill missing program_number by fuzzy name match to stored entries."""
    from race import normalize_horse_name
    name_to_prog = {}
    for e in entries:
        nm = e.get("horse_name")
        if nm:
            name_to_prog.setdefault(normalize_horse_name(nm), e.get("program_number"))
    for r in results:
        if not r.get("program_number") and r.get("horse_name"):
            r["program_number"] = name_to_prog.get(normalize_horse_name(r["horse_name"]))
    return results


if __name__ == "__main__":
    refill()