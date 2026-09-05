"""
Weekly backtest runner for Horse Race Predictor.

Runs the full predict -> results -> score pipeline across a date range and a set
of tracks, using:
  - HRN (server-rendered, no bot wall) for entries + morning-line odds + scratch
    status (one page load per track/date via hrn.fetch_card).
  - Three synthetic baselines (MLO favorite, post position, random) - fully
    automated, no manual picks needed.
  - HRN again for results: the same entries-results page renders per-race
    payouts tables with full top-4 finish order + $2 WPS payoffs (one page per
    track/date via hrn.fetch_results_card) - free and unlimited.
  - parse.bot (bloodhorse + equibase_parse) as a per-race fallback for any race
    HRN hasn't populated.

After scoring, it hands off to report.py to generate the final HTML accuracy
report and persist it to the DB (reports table).

Usage:
  python horse_race_predictor/weekly_runner.py \\
      --start 2026-06-27 --end 2026-07-03 \\
      --tracks CD,BEL,SAR,GP,SA
"""

import argparse
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import accuracy as accuracy_mod
import mlo_baseline
import mlo_variants
import post_position_baseline
import random_baseline
import connections_baseline
import schedule as schedule_mod
import report as report_mod
from race import Race, filter_active, normalize_horse_name
from sources import hrn
import sources as sources_mod

# Pure baselines: computed from the entry sheet alone, so they're saved in
# Phase 1 (predict) before results exist. Each is a (source_name, predict_fn)
# pair; random_baseline takes a seed_key kwarg.
PURE_BASELINES = [
    ("mlo_baseline", mlo_baseline.predict),
    ("mlo_second", mlo_variants.predict_second),
    ("mlo_third", mlo_variants.predict_third),
    ("mlo_longshot", mlo_variants.predict_longshot),
    ("post_position_baseline", post_position_baseline.predict),
    ("post_position_outside", post_position_baseline.predict_outside),
    ("random_baseline", random_baseline.predict),
]
# Connection baselines (leading_jockey / leading_trainer) are saved in Phase 2c
# via connections_baseline.save_picks_for_races (running tally per track).


def run_backtest(start_date, end_date, tracks, do_report=True, html_path=None,
                 skip_equibase_fill=False):
    """Run the backtest for [start_date, end_date] across `tracks`.

    Phases:
      1. Predict: for each track/date with a card, persist race + entries +
         pure-baseline picks for every race. The HRN page is fetched once and
         its results tables are cached for Phase 2.
      2. Results: save HRN finish order + payoffs for each predicted race, score.
      2b. parse.bot fallback for any predicted race HRN didn't populate.
      2c. Connection baselines (leading jockey / trainer): compute picks from
          meet standings-as-of (prior results only), save, re-score.
      3. Report: generate the HTML accuracy report, save to DB + file.

    Returns a summary dict with counts, html path, and a `timings` dict
    {e2e, phase1_predict, phase2_results, phase2b_fallback, phase2c_connections,
    phase3_report} in seconds.
    """
    db.init_db()
    tracks = [t.upper().strip() for t in tracks if t.strip()]

    print(f"\n=== Backtest: {start_date} -> {end_date} | tracks={tracks} ===\n")
    t_start = time.time()

    # ── Phase 1: predict + persist (cache results tables off the same fetch) ──
    t_phase1 = time.time()
    predicted = []  # {race_id, track, race_number, date, n_active, best_pick, best_prog}
    pages = {}      # (track, date) -> results_card (parsed off the Phase-1 fetch)
    for date in _daterange(start_date, end_date):
        print(f"-- {date} --")
        for track in tracks:
            card, results_card = hrn.fetch_card_and_results(track, date)
            pages[(track, date)] = results_card
            if not card:
                print(f"   {track}: no card (dark or no data)")
                continue
            n_races = len(card)
            for race_number, entries in sorted(card.items()):
                race = Race.from_inputs(track, race_number, date)
                active, excluded = filter_active(entries)
                if not active:
                    print(f"   {track} R{race_number}: no active entries "
                          f"({len(excluded)} excluded) - skip")
                    continue
                race_id = db.save_race(race)
                db.save_entries(race_id, entries)
                seed_key = f"{track}-{race_number}-{date}"
                best = None
                for name, fn in PURE_BASELINES:
                    kwargs = {"seed_key": seed_key} if name == "random_baseline" else {}
                    picks = fn(active, **kwargs)
                    db.save_picks(race_id, name, picks)
                    if name == mlo_baseline.SOURCE_NAME:
                        best = picks[0] if picks else None
                predicted.append({
                    "race_id": race_id, "track": track,
                    "race_number": race_number, "date": date,
                    "n_active": len(active),
                    "best_pick": best["horse_name"] if best else None,
                    "best_prog": best["program_number"] if best else None,
                })
            print(f"   {track}: {n_races} race(s) predicted")
    phase1_dt = time.time() - t_phase1
    print(f"\nPhase 1 done: {len(predicted)} races predicted across "
          f"{len({p['track'] for p in predicted})} tracks ({phase1_dt:.1f}s).\n")

    # ── Phase 2: results + score (HRN direct, from cached pages) ───────────
    t_phase2 = time.time()
    scored = 0
    for date in _daterange(start_date, end_date):
        for track in tracks:
            results_card = pages.get((track, date), {})
            if not results_card:
                continue
            for race_number, results in sorted(results_card.items()):
                race_id = db.get_race_id(track, race_number, date)
                if not race_id:
                    continue  # HRN has a race we didn't predict
                results = _attach_prog_from_entries(results, db.get_entries(race_id))
                db.save_results(race_id, results)
                snaps = accuracy_mod.run_accuracy_checks(race_id)
                if snaps:
                    scored += 1
    phase2_dt = time.time() - t_phase2
    print(f"Phase 2 done: {scored} races scored via HRN ({phase2_dt:.1f}s).\n")

    # ── Phase 2b: parse.bot fallback for races HRN didn't populate ─────────
    t_phase2b = time.time()
    filled = 0
    if not skip_equibase_fill:
        refetched = {}  # (track, date) -> results card from ONE fresh HRN page load
        for p in predicted:
            race_id = p["race_id"]
            if db.get_results(race_id):
                continue  # already scored via HRN
            key = (p["track"], p["date"])
            if key not in refetched:
                # One second-chance fetch per (track, date): HRN may have
                # populated payouts since Phase 1. Per-race refetches of the
                # same page cost ~1s each (rate limit) for identical content.
                refetched[key] = hrn.fetch_results_card(p["track"], p["date"])
            results = refetched[key].get(p["race_number"])
            if not results:
                # parse.bot fallback only (HRN page was just re-fetched above).
                race = Race.from_inputs(p["track"], p["race_number"], p["date"])
                results = sources_mod.fetch_results(
                    race, sources=["bloodhorse", "equibase_parse", "equibase_results"])
            if not results:
                continue
            results = _attach_prog_from_entries(results, db.get_entries(race_id))
            db.save_results(race_id, results)
            snaps = accuracy_mod.run_accuracy_checks(race_id)
            if snaps:
                filled += 1
        if filled:
            print(f"Phase 2b done: parse.bot fallback scored {filled} more races.\n")
    phase2b_dt = time.time() - t_phase2b

    # ── Phase 2c: connection baselines (leading jockey / trainer) ──────────
    # These need prior results (meet standings-as-of), so they run after Phase
    # 2/2b. save_picks_for_races uses a running tally per track (O(n) per track,
    # no look-ahead, same-date races mutually excluded).
    t_phase2c = time.time()
    connection_touched = connections_baseline.save_picks_for_races(predicted)
    # Re-score only the races whose connection picks were just written - every
    # other source's snapshots are unchanged, so a full re-score is redundant
    # upserts (run_accuracy_checks upserts all sources for a race).
    for p in predicted:
        if p["race_id"] in connection_touched:
            accuracy_mod.run_accuracy_checks(p["race_id"])
    phase2c_dt = time.time() - t_phase2c
    print(f"Phase 2c done: {len(connection_touched)} races with connection picks "
          f"(re-scored; {len(predicted)} predicted total) ({phase2c_dt:.1f}s).\n")

    # ── Phase 3: report ───────────────────────────────────────────────────
    # Report generation time is itself Phase 3, so measure it with a dry render,
    # then save with the complete timings (e2e + phase3 included in the HTML).
    t_phase3 = time.time()
    timings = {
        "e2e": 0.0,
        "phase1_predict": phase1_dt,
        "phase2_results": phase2_dt,
        "phase2b_fallback": phase2b_dt,
        "phase2c_connections": phase2c_dt,
        "phase3_report": 0.0,
        "predicted": len(predicted),
        "scored": scored,
        "fallback_filled": filled,
    }
    html = None
    out_path = None
    if do_report:
        # Render once with sentinel timing values, measure the render, then
        # splice the real timings block into the HTML. (Rendering the full
        # report twice just to update two numbers doubles Phase 3.)
        timings["phase3_report"] = -1.0
        timings["e2e"] = -1.0
        html = report_mod.generate(start_date, end_date, tracks, timings=timings)
        sentinel_block = report_mod.timings_block_html(timings)
        timings["phase3_report"] = time.time() - t_phase3
        timings["e2e"] = time.time() - t_start
        html = html.replace(sentinel_block, report_mod.timings_block_html(timings), 1)
        html, out_path = report_mod.generate_and_save(
            start_date, end_date, tracks, html_path=html_path, timings=timings, html=html)
    else:
        timings["phase3_report"] = time.time() - t_phase3
        timings["e2e"] = time.time() - t_start
    print(f"Phase 3 done: report ({timings['phase3_report']:.1f}s).")
    print(f"Total e2e: {timings['e2e']:.1f}s "
          f"(P1 {phase1_dt:.1f} / P2 {phase2_dt:.1f} / P2b {phase2b_dt:.1f} / "
          f"P2c {phase2c_dt:.1f} / P3 {timings['phase3_report']:.1f}).\n")

    tracks_with = sorted({p["track"] for p in predicted})
    tracks_empty = [t for t in tracks if t not in tracks_with]
    return {
        "predicted_races": len(predicted),
        "scored_races": scored,
        "tracks_with_races": tracks_with,
        "tracks_empty": tracks_empty,
        "html": html,
        "html_path": out_path,
        "timings": timings,
    }


def _attach_prog_from_entries(results, entries):
    """Fill in program_number from stored entries by fuzzy name where missing.

    Used after a results source returns finishers that may have an ambiguous or
    missing program number (coupled-entry letter, name-only finishers); a name
    match to the stored HRN entries gives the canonical program number for
    scoring. HRN-direct results already carry program numbers, so this is a
    no-op for them and mainly helps the parse.bot fallback sources.
    """
    name_to_prog = {}
    for e in entries:
        nm = e.get("horse_name")
        if nm:
            name_to_prog.setdefault(normalize_horse_name(nm), e.get("program_number"))
    for r in results:
        if not r.get("program_number") and r.get("horse_name"):
            r["program_number"] = name_to_prog.get(normalize_horse_name(r["horse_name"]))
    return results


def _daterange(start, end):
    """Yield YYYY-MM-DD strings for each day from start to end inclusive."""
    from datetime import date, timedelta
    sy, sm, sd = map(int, start.split("-"))
    ey, em, ed = map(int, end.split("-"))
    cur = date(sy, sm, sd)
    last = date(ey, em, ed)
    while cur <= last:
        yield cur.isoformat()
        cur += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser(prog="weekly_runner.py",
                                 description="Weekly backtest runner.")
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD.")
    ap.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive).")
    ap.add_argument("--tracks", default=None,
                    help="Comma-separated track codes. Omit with --auto-tracks to "
                         "auto-select tracks whose 2026 meets overlap the window.")
    ap.add_argument("--auto-tracks", action="store_true",
                    help="Ignore --tracks; use schedule.active_tracks(start, end) so "
                         "only tracks in session during the window are fetched.")
    ap.add_argument("--html", default=None,
                    help="Path to write the HTML report (default: reports/backtest_<start>_<end>.html).")
    ap.add_argument("--no-report", action="store_true", help="Skip HTML report generation.")
    ap.add_argument("--skip-equibase-fill", action="store_true",
                    help="Skip the Equibase Parse gap-fill (BloodHorse-only results).")
    args = ap.parse_args()

    if args.auto_tracks:
        tracks = schedule_mod.active_tracks(args.start, args.end)
        inactive = schedule_mod.inactive_tracks(args.start, args.end)
        print(f"Auto-tracks: {len(tracks)} active in {args.start}..{args.end}: {tracks}")
        print(f"Skipped (out of session): {inactive}\n")
    else:
        if not args.tracks:
            ap.error("--tracks is required unless --auto-tracks is set")
        tracks = args.tracks.split(",")

    summary = run_backtest(
        args.start, args.end, tracks,
        do_report=not args.no_report, html_path=args.html,
        skip_equibase_fill=args.skip_equibase_fill)

    print("\n=== Backtest summary ===")
    print(f"  Predicted races : {summary['predicted_races']}")
    print(f"  Scored races    : {summary['scored_races']}")
    print(f"  Tracks w/ racing : {summary['tracks_with_races']}")
    print(f"  Tracks empty    : {summary['tracks_empty']}")
    tm = summary.get("timings") or {}
    if tm:
        print(f"  Timings (s)     : e2e={tm.get('e2e', 0):.1f} "
              f"P1={tm.get('phase1_predict', 0):.1f} "
              f"P2={tm.get('phase2_results', 0):.1f} "
              f"P2b={tm.get('phase2b_fallback', 0):.1f} "
              f"P2c={tm.get('phase2c_connections', 0):.1f} "
              f"P3={tm.get('phase3_report', 0):.1f}")
    if summary["html_path"]:
        print(f"  HTML report     : {summary['html_path']}")
        print(f"  (also saved in DB reports table, key='weekly_accuracy')")


if __name__ == "__main__":
    main()