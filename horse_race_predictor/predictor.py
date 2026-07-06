"""
Horse Race Predictor - Main CLI entry point.

Fetches expert picks for a specified today's race from multiple free web
sources, aggregates them into a consensus, and prints the best pick plus a
ranked consensus table. Picks + entries are stored in a SQLite database so
official results can later be reconciled and source accuracy tracked.

Usage:
  python horse_race_predictor/predictor.py predict --track SAR --race 1
  python horse_race_predictor/predictor.py predict --track SAR --race 1 --date 2026-07-04
  python horse_race_predictor/predictor.py sources
  python horse_race_predictor/predictor.py results --track SAR --race 1
  python horse_race_predictor/predictor.py summary
  python horse_race_predictor/predictor.py detail <race_id>
"""

import argparse
import os
import sys

# Add this module's directory to the path so sibling modules import cleanly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import consensus as consensus_mod
import manual_input
import accuracy as accuracy_mod
from race import Race, filter_active, normalize_horse_name
from sources import (
    fetch_entries, fetch_all_picks, fetch_results,
    get_available_entry_sources, get_available_pick_sources,
    get_available_result_sources,
)


# ── predict ──────────────────────────────────────────────────────────────

def cmd_predict(args):
    """Resolve entries + picks (web or manual), account for scratches, print consensus, persist."""
    if not args.input and (not args.track or not args.race):
        print("ERROR: --track and --race are required (or supply --input FILE with them).")
        return

    # Resolve race identity + entries + picks
    if args.input:
        meta, entries, picks, _results = manual_input.load_input_file(args.input)
        track = meta.get("track") or args.track
        race_num = meta.get("race") or args.race
        date = meta.get("date") or args.date
        if not track or not race_num:
            print("ERROR: --input file missing track/race; supply --track and --race.")
            return
        race = Race.from_inputs(track, race_num, date)
        print(f"\n=== Predict: {race} ===\n")
    else:
        race = Race.from_inputs(args.track, args.race, args.date)
        print(f"\n=== Predict: {race} ===\n")
        # Entries: manual --field, else web (HRN, which detects scratches/MTO/AE)
        if args.field:
            entries = manual_input.parse_field(args.field)
        else:
            print("Fetching entries (web via HRN)...")
            entries = fetch_entries(race)
        if not entries:
            print(f"No entries found for {race.track_code} R{race.race_number} on "
                  f"{race.race_date}. Use --field or --input, or check the track/date. "
                  f"Aborting.")
            return
        # Picks: manual --picks, else web (best-effort stubs)
        if args.picks:
            picks = manual_input.parse_picks(args.picks, entries)
        else:
            print("Fetching expert picks (web, best-effort)...")
            picks, _web_stats = fetch_all_picks(race)

    if not entries:
        print("ERROR: no entries to predict. Aborting.")
        return
    race.entries = entries

    # Per-source pick counts
    stats = {}
    for p in picks:
        stats[p["source"]] = stats.get(p["source"], 0) + 1

    # Scratch override + active-field filter (excludes scratched; MTO/AE unless flagged)
    extra_scratched = _split_csv(args.scratch)
    active, excluded = filter_active(
        entries, include_mto=args.include_mto, include_ae=args.include_ae,
        extra_scratched=extra_scratched)

    # Void picks whose target is an excluded horse (scratch/MTO/AE)
    active_picks, voided = _void_picks(picks, excluded)

    # Print field with status
    n_active = len(active)
    print(f"Field of {len(entries)} ({n_active} active, {len(excluded)} excluded):\n")
    for e in entries:
        st = e.get("status", "in")
        mlo = f"{e.get('morning_line_odds'):.1f}" if e.get("morning_line_odds") else "-"
        flag = "" if st == "in" else f"  [{st.upper()}]"
        print(f"  {e.get('program_number','?'):>3}  {e.get('horse_name',''):<24} "
              f"J:{e.get('jockey','')[:18]:<18} T:{e.get('trainer','')[:18]:<18} "
              f"MLO:{mlo}{flag}")
    if excluded:
        print("\nExcluded from consensus:")
        for e, reason in excluded:
            print(f"  #{e.get('program_number','?')} {e.get('horse_name','')} - {reason}")
    if voided:
        print("\nPicks voided (target excluded):")
        for p, reason in voided:
            print(f"  {p.get('source')}: #{p.get('rank')} {p.get('horse_name')} - {reason}")

    print(f"\nPick sources: {stats}")

    # Consensus on the active field + non-voided picks
    result = consensus_mod.aggregate(active, active_picks)
    print("\n" + "=" * 60)
    if result["best_pick"]:
        bp = result["best_pick"]
        conf_pct = result["confidence"] * 100
        print(f"BEST PICK: #{bp['program_number']} {bp['horse_name']} "
              f"(MLO {bp['morning_line_odds']})")
        print(f"  Points: {bp['points']:.0f}  |  #1 votes: {bp['first_votes']}"
              f"/{result['num_sources']} sources  |  "
              f"Confidence: {conf_pct:.0f}%  |  Margin: +{result['margin']:.0f}")
    else:
        print("BEST PICK: (no picks resolved - entries-only table below)")
    print("\nConsensus table:")
    print(consensus_mod.format_table(result))

    if result["unmatched_picks"]:
        print(f"\nNote: {len(result['unmatched_picks'])} pick(s) could not be "
              f"matched to an active entry (name/number mismatch).")

    # Persist: all entries (with status) + all picks (raw source selections)
    race_id = db.save_race(race)
    db.save_entries(race_id, entries)
    for source_name in stats:
        source_picks = [p for p in picks if p.get("source") == source_name]
        if source_picks:
            db.save_picks(race_id, source_name, source_picks)
    print(f"\nSaved to DB (race_id={race_id}). Run 'results --track {race.track_code} "
          f"--race {race.race_number} --date {race.race_date}' after the race goes "
          f"official to score accuracy.")


def _split_csv(s):
    """Split a comma-separated string into a stripped, non-empty list."""
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _void_picks(picks, excluded):
    """Split picks into (active_picks, voided) where voided target an excluded entry.

    `excluded` is a list of (entry, reason) tuples from filter_active(). A pick is
    voided if its program number or fuzzy horse name matches an excluded entry.
    `voided` is a list of (pick, reason) tuples.
    """
    excluded_progs = {e.get("program_number") for e, _ in excluded if e.get("program_number")}
    excluded_names = {normalize_horse_name(e.get("horse_name", "")): r
                      for e, r in excluded if e.get("horse_name")}
    active_picks = []
    voided = []
    for p in picks:
        prog = (p.get("program_number") or "").strip()
        name = p.get("horse_name") or ""
        reason = None
        if prog and prog in excluded_progs:
            for e, r in excluded:
                if e.get("program_number") == prog:
                    reason = r
                    break
        elif name:
            norm = normalize_horse_name(name)
            if norm in excluded_names:
                reason = excluded_names[norm]
        if reason:
            voided.append((p, reason))
        else:
            active_picks.append(p)
    return active_picks, voided


# ── sources ──────────────────────────────────────────────────────────────

def cmd_sources(args):
    """List available entry/pick/result sources."""
    print("Entry sources : ", ", ".join(get_available_entry_sources()))
    print("Pick sources  : ", ", ".join(get_available_pick_sources()))
    print("Result sources: ", ", ".join(get_available_result_sources()))


# ── reconcile subcommands (Task #6) ──────────────────────────────────────

def cmd_results(args):
    """Fetch or accept manual results for a race, then score source accuracy."""
    race = Race.from_inputs(args.track, args.race, args.date)
    print(f"\n=== Results: {race} ===\n")
    race_id = db.get_race_id(race.track_code, race.race_number, race.race_date)
    if not race_id:
        print(f"No stored race found for {race.track_code} R{race.race_number} on "
              f"{race.race_date}. Run 'predict' first so picks are on record.")
        return

    # Resolve results: manual --finish / --input file, else web fetch (best-effort)
    if args.finish:
        entries = db.get_entries(race_id)
        results = manual_input.parse_finish(args.finish, entries)
        if not results:
            print("ERROR: no finish positions parsed from --finish.")
            return
    elif args.input:
        _meta, _entries, _picks, results = manual_input.load_input_file(args.input)
        if not results:
            print("ERROR: --input file has no 'results' section.")
            return
    else:
        print("Fetching official results (web, best-effort)...")
        results = fetch_results(race)
        if not results:
            print("\nNo results available. Free racing sites are bot-walled/JS-rendered; "
                  "use --finish \"1,3,2\" (program numbers in finish order) or --input "
                  "FILE.json with a results section.")
            return

    db.save_results(race_id, results)
    print(f"Saved {len(results)} finishers.")
    _print_results_table(results)

    # Score accuracy
    snaps = accuracy_mod.run_accuracy_checks(race_id)
    if snaps:
        print("\nAccuracy (top pick -> finish):")
        print(f"  {'Source':<14} {'Top pick':<22} {'Fin':>4} {'Win':>4} {'Plc':>4} {'Shw':>4}")
        for s in snaps:
            print(f"  {s['source']:<14} {s['top_pick'][:22]:<22} "
                  f"{s['finish']!s:>4} {s['hit_win']:>4} {s['hit_place']:>4} {s['hit_show']:>4}")
    else:
        print("\nNo picks stored for this race; nothing to score.")


def _print_results_table(results):
    print("\nFinish order:")
    print(f"  {'Fin':<4} {'Prog':<5} {'Horse':<24} {'Win':>7} {'Place':>7} {'Show':>7}")
    for r in results:
        win = f"{r.get('win_payoff'):.2f}" if r.get("win_payoff") else "-"
        place = f"{r.get('place_payoff'):.2f}" if r.get("place_payoff") else "-"
        show = f"{r.get('show_payoff'):.2f}" if r.get("show_payoff") else "-"
        print(f"  {r.get('finish_position','-')!s:<4} {r.get('program_number','-'):<5} "
              f"{r.get('horse_name','')[:24]:<24} {win:>7} {place:>7} {show:>7}")


def cmd_accuracy(args):
    """Recompute accuracy snapshots for every stored race that has results."""
    print("\nRecomputing accuracy for all scored races...")
    total = accuracy_mod.recompute_all()
    print(f"Recomputed accuracy for {total} race(s).")


def cmd_summary(args):
    """Console table of per-source and consensus hit rates."""
    rows = accuracy_mod.summary()
    if not rows:
        print("\nNo accuracy data yet. Run 'results' on races you've predicted.")
        return
    print("\nAccuracy summary across all scored races:\n")
    print(accuracy_mod.format_summary(rows))


def cmd_detail(args):
    """Race card: entries, per-source picks, result, accuracy."""
    race_id = int(args.race_id)
    race = db.get_race(race_id)
    if not race:
        print(f"No race with id={race_id}.")
        return
    print(f"\n=== {race['track_code']} R{race['race_number']} on {race['race_date']} "
          f"(race_id={race_id}) ===\n")
    entries = db.get_entries(race_id)
    print("Entries:")
    for e in entries:
        mlo = f"{e['morning_line_odds']:.1f}" if e["morning_line_odds"] else "-"
        scratch = " (SCR)" if e["scratched"] else ""
        print(f"  {e['program_number']:>3}  {e['horse_name']:<24} MLO:{mlo}{scratch}")
    picks = db.get_picks(race_id)
    if picks:
        print("\nPicks:")
        by_src = {}
        for p in picks:
            by_src.setdefault(p["source"], []).append(p)
        for src, ps in by_src.items():
            ps.sort(key=lambda x: (x["rank"] if x["rank"] else 99))
            line = ", ".join(f"#{p['rank']} {p['horse_name']}" for p in ps if p["rank"])
            print(f"  {src}: {line}")
    results = db.get_results(race_id)
    if results:
        print("\nResults:")
        _print_results_table(results)
    snaps = db.get_accuracy_snapshots(race_id)
    if snaps:
        print("\nAccuracy:")
        for s in snaps:
            print(f"  {s['source']}: top={s['top_pick_horse']} fin={s['finish_position']} "
                  f"win={s['hit_win']} place={s['hit_place']} show={s['hit_show']}")


# ── argument parsing ─────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog="predictor.py",
        description="Horse race prediction by aggregating free public expert picks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pred = sub.add_parser("predict", help="Predict a race: fetch picks, output best pick.")
    p_pred.add_argument("--track", default=None, help="Track code or name (e.g. SAR or Saratoga). Required unless --input supplies it.")
    p_pred.add_argument("--race", default=None, type=int, help="Race number. Required unless --input supplies it.")
    p_pred.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today).")
    p_pred.add_argument("--input", default=None,
                        help="Manual JSON input file (entries + picks). See manual_input.py docstring.")
    p_pred.add_argument("--field", default=None,
                        help='Manual entries inline: "1:Speed Star:5/2, 2:Lazy Day:3/1, ..."')
    p_pred.add_argument("--picks", default=None,
                        help='Manual picks inline: "drf_free:1,3,2 | abr:3,1,2 | tip:Speed Star,2"')
    p_pred.add_argument("--scratch", default=None,
                        help='Comma-separated program numbers to mark scratched (override): "3,7"')
    p_pred.add_argument("--include-mto", action="store_true",
                        help="Include Main-Track-Only horses (use if race moves off turf to dirt).")
    p_pred.add_argument("--include-ae", action="store_true",
                        help="Include Also-Eligible horses (they draw in only on a scratch).")
    p_pred.set_defaults(func=cmd_predict)

    p_res = sub.add_parser("results", help="Fetch official results and score accuracy.")
    p_res.add_argument("--track", required=True)
    p_res.add_argument("--race", required=True, type=int)
    p_res.add_argument("--date", default=None)
    p_res.add_argument("--finish", default=None,
                        help='Manual finish order: "2,1,3" (program numbers in finish order).')
    p_res.add_argument("--input", default=None,
                        help="JSON input file with a results section (see manual_input.py).")
    p_res.set_defaults(func=cmd_results)

    sub.add_parser("accuracy", help="Recompute accuracy for stored races.").set_defaults(func=cmd_accuracy)
    sub.add_parser("summary", help="Per-source + consensus accuracy summary.").set_defaults(func=cmd_summary)

    p_det = sub.add_parser("detail", help="Show a stored race card.")
    p_det.add_argument("race_id", type=int)
    p_det.set_defaults(func=cmd_detail)

    sub.add_parser("sources", help="List available sources.").set_defaults(func=cmd_sources)
    return parser


def main():
    db.init_db()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()