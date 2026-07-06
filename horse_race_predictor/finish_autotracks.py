"""Finish the auto-tracks backtest from existing DB state (no re-fetch).

After weekly_runner was stopped during Phase 2b (parse.bot fallback too slow),
this finishes the job: compute + save connection-baseline picks (running
tally), re-score every predicted race, and generate the report with the
authentic Phase 1/2 timings captured from the run log.

Usage:
  python horse_race_predictor/finish_autotracks.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import accuracy as accuracy_mod
import connections_baseline
import report as report_mod
import schedule as schedule_mod

WINDOW_START = "2026-06-20"
WINDOW_END = "2026-07-03"
# Only tracks whose 2026 meets overlap the window (auto-tracks selection).
TRACKS = schedule_mod.active_tracks(WINDOW_START, WINDOW_END)


def main():
    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, track_code, race_number, race_date FROM races "
        "WHERE race_date BETWEEN ? AND ? ORDER BY race_date, track_code, race_number",
        (WINDOW_START, WINDOW_END),
    )
    predicted = [{"race_id": r["id"], "track": r["track_code"],
                  "race_number": r["race_number"], "date": r["race_date"]}
                 for r in cur.fetchall()]
    conn.close()
    print(f"Predicted races in window: {len(predicted)}")

    # Phase 2c: connection baselines via running tally (fast).
    t2c = time.time()
    saved = connections_baseline.save_picks_for_races(predicted)
    for p in predicted:
        accuracy_mod.run_accuracy_checks(p["race_id"])
    p2c = time.time() - t2c
    print(f"Phase 2c: {saved} connection pick-lists, re-scored {len(predicted)} races ({p2c:.1f}s)")

    # Phase 3: report with authentic Phase 1/2 timings from the run log.
    t3 = time.time()
    timings = {
        "e2e": 0.0,
        "phase1_predict": 842.8,   # from autotracks_run.log
        "phase2_results": 45.7,    # from autotracks_run.log
        "phase2b_fallback": 0.0,   # skipped (parse.bot fallback too slow for 86 gaps)
        "phase2c_connections": p2c,
        "phase3_report": 0.0,
        "predicted": len(predicted),
        "scored": sum(1 for p in predicted if db.get_results(p["race_id"])),
        "fallback_filled": 0,
    }
    html, path = report_mod.generate_and_save(
        WINDOW_START, WINDOW_END, TRACKS, timings=timings)
    p3 = time.time() - t3
    timings["phase3_report"] = p3
    timings["e2e"] = timings["phase1_predict"] + timings["phase2_results"] + p2c + p3
    # Re-save the report so the timings section reflects the complete e2e + P3.
    html, path = report_mod.generate_and_save(
        WINDOW_START, WINDOW_END, TRACKS, timings=timings)
    print(f"Phase 3: report {p3:.1f}s -> {path}")
    print(f"e2e (P1+P2+P2c+P3, fallback skipped): {timings['e2e']:.1f}s")
    print(f"scored: {timings['scored']} / {len(predicted)}")


if __name__ == "__main__":
    main()