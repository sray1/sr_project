"""
Backfill / refresh synthetic-baseline picks for already-scored races.

The MLO baseline picks were saved when each race was first predicted. The two
comparison baselines (post_position_baseline, random_baseline) were added later;
this script recomputes ALL THREE baselines from the stored entries for every
scored race, re-saves their picks, and re-runs accuracy so each baseline gets its
own accuracy_snapshots row alongside MLO.

Idempotent: save_picks replaces per (race, source) and run_accuracy_checks
upserts per (race, source), so re-running is safe.

Usage:
  python horse_race_predictor/backfill_baselines.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import accuracy as accuracy_mod
import mlo_baseline
import mlo_variants
import post_position_baseline
import random_baseline
import connections_baseline
from race import filter_active

# Pure baselines: need only entries.
PURE_BASELINES = [
    ("mlo_baseline", mlo_baseline.predict),
    ("mlo_second", mlo_variants.predict_second),
    ("mlo_third", mlo_variants.predict_third),
    ("mlo_longshot", mlo_variants.predict_longshot),
    ("post_position_baseline", post_position_baseline.predict),
    ("post_position_outside", post_position_baseline.predict_outside),
    ("random_baseline", random_baseline.predict),
]

# Connection baselines: need track_code + race_date (meet standings-as-of from
# prior results). Results are already stored when this runs, so they work here.
CONNECTION_BASELINES = [
    ("leading_jockey", connections_baseline.predict_leading_jockey),
    ("leading_trainer", connections_baseline.predict_leading_trainer),
]


def backfill():
    db.init_db()
    scored = db.get_scored_races()
    print(f"Backfilling baselines for {len(scored)} scored races...")
    # Pure baselines: per race (need only entries).
    for r in scored:
        race_id = r["id"]
        entries = db.get_entries(race_id)
        active, _ = filter_active(entries)
        if not active:
            continue
        track, race_number, date = r["track_code"], r["race_number"], r["race_date"]
        seed_key = f"{track}-{race_number}-{date}"
        for name, fn in PURE_BASELINES:
            kwargs = {"seed_key": seed_key} if name == "random_baseline" else {}
            picks = fn(active, **kwargs)
            db.save_picks(race_id, name, picks)
    # Connection baselines: running tally per track (O(n), no look-ahead).
    races_for_conn = [{"race_id": r["id"], "track": r["track_code"],
                       "race_number": r["race_number"], "date": r["race_date"]} for r in scored]
    connections_baseline.save_picks_for_races(races_for_conn)
    # Re-score everything so all sources get fresh accuracy snapshots.
    n = 0
    for r in scored:
        accuracy_mod.run_accuracy_checks(r["id"])
        n += 1
    print(f"Done: {n} races re-scored with "
          f"{len(PURE_BASELINES) + len(CONNECTION_BASELINES)} baselines.")
    rows = accuracy_mod.summary()
    print("\n" + accuracy_mod.format_summary(rows))


if __name__ == "__main__":
    backfill()