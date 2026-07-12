"""
Record manually-gathered expert picks (and optional results) for one or more
races into the DB, then score accuracy and print a summary. Generalizes the
one-off _record_july10.py / _record_bowlinggreen.py scripts.

Typical flow: run `predictor.py predict --track SAR --race 8 --date 2026-07-10`
first so the race + entries (fetched from HRN) are already in the DB. Then call
this script with a JSON file that identifies the race by track/race/date and
supplies only the picks (program numbers + ranks) you gathered by hand. You do
NOT re-enter the field -- entries are read back from the DB and used to
back-fill horse names for picks given by program number alone.

Input JSON (multi-race):
  {
    "add_mlo_favorite": true,            // optional; default true; CLI flag overrides
    "races": [
      {
        "track": "SAR", "race": 8, "date": "2026-07-10",
        "entries": [...],                // optional; only if the race is NOT yet in DB
        "picks": [
          {"source": "theracingbiz", "picks": [
             {"program_number": "5", "rank": 1},
             {"program_number": "2", "rank": 2},
             {"program_number": "6", "rank": 3}
          ]},
          {"source": "my_tip", "picks": [
             {"horse_name": "Jackie the Joker", "rank": 1}
          ]}
        ],
        "results": ["2", "1", "8", "9"]  // optional; program numbers in finish
                                         //   order, OR result dicts with payoffs:
                                         //   [{"program_number":"2","finish_position":1,
                                         //     "win_payoff":15.72,...}]
      }
    ]
  }

For each race:
  1. Resolve race_id (must already exist; if not, "entries" must be supplied to
     create it).
  2. Save every source's picks (replaces prior picks for that source -- idempotent).
  3. If add_mlo_favorite: save a synthetic "mlo_favorite" source whose rank-1
     pick is the lowest-morning-line ACTIVE entry (the naive baseline).
  4. If "results" given (or already in the DB): save them and score every source's
     top pick + the consensus blend -> accuracy_snapshots.
  5. Print the live consensus table + best pick. When the MLO baseline is added,
     also print a "consensus (experts only)" pick that excludes it, since the
     baseline otherwise skews the blend toward the favorite.

Usage:
  python horse_race_predictor/record_picks.py --input picks.json
  python horse_race_predictor/record_picks.py --input picks.json --no-mlo-favorite
  python horse_race_predictor/record_picks.py --input picks.json --no-summary
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import accuracy as accuracy_mod
import consensus as consensus_mod
import manual_input
from race import Race, normalize_horse_name


# ── helpers ───────────────────────────────────────────────────────────────

def _entry_indexes(entries):
    """Build {program_number: entry} and {normalized_name: entry} from DB rows."""
    by_prog = {e["program_number"]: e for e in entries if e.get("program_number")}
    by_name = {normalize_horse_name(e["horse_name"]): e
               for e in entries if e.get("horse_name")}
    return by_prog, by_name


def mlo_favorite_pick(entries):
    """Lowest-morning-line ACTIVE entry (not scratched/MTO/AE) -> (prog, name, mlo)."""
    active = [e for e in entries
              if e.get("status") == "in" and not e.get("scratched")
              and e.get("morning_line_odds") is not None]
    active.sort(key=lambda e: e["morning_line_odds"])
    if not active:
        return None
    top = active[0]
    return top["program_number"], top["horse_name"], top["morning_line_odds"]


def _normalize_pick_dicts(src_picks, by_prog, by_name):
    """Turn a source's raw pick list into DB-shape pick dicts.

    Back-fills horse_name from program number (and vice versa) so the DB's
    UNIQUE(race_id, source, horse_name) constraint is satisfiable. Rank defaults
    to order of appearance if omitted.
    """
    out = []
    rank = 0
    for p in src_picks:
        rank += 1
        prog = str(p.get("program_number", "") or "").strip()
        name = p.get("horse_name", "") or ""
        if not name and prog and prog in by_prog:
            name = by_prog[prog]["horse_name"]
        if not prog and name:
            ent = by_name.get(normalize_horse_name(name))
            if ent:
                prog = ent["program_number"]
        out.append({
            "horse_name": name,
            "program_number": prog,
            "rank": p.get("rank", rank),
            "comment": p.get("comment", ""),
        })
    return out


def _ensure_race(spec):
    """Resolve race_id for a race spec, creating the race from spec['entries'] if needed."""
    track = spec.get("track")
    race_num = spec.get("race")
    date = spec.get("date")
    if not track or not race_num or not date:
        print("  !! spec missing track/race/date - skipping")
        return None
    race_id = db.get_race_id(track, race_num, date)
    if race_id:
        return race_id
    # Race not in DB: create it from an inline entries block, if supplied.
    raw_entries = spec.get("entries")
    if not raw_entries:
        print(f"  !! no stored race for {track} R{race_num} on {date} and no "
              f"'entries' to create it - skipping")
        return None
    race = Race.from_inputs(track, race_num, date)
    race.entries = [_coerce_entry(e) for e in raw_entries]
    race_id = db.save_race(race)
    db.save_entries(race_id, race.entries)
    print(f"  created race {track} R{race_num} {date} (race_id={race_id}) "
          f"with {len(race.entries)} entries")
    return race_id


def _coerce_entry(e):
    """Coerce a JSON entry dict to the shape db.save_entries expects."""
    return {
        "program_number": str(e.get("program_number", "")),
        "horse_name": e.get("horse_name", ""),
        "jockey": e.get("jockey", ""),
        "trainer": e.get("trainer", ""),
        "morning_line_odds": e.get("morning_line_odds"),
        "post_position": e.get("post_position"),
        "scratched": bool(e.get("scratched", False)),
        "status": e.get("status", "in"),
    }


# ── per-race recording ────────────────────────────────────────────────────

def record_race(spec, add_mlo):
    """Record picks (+ optional results + optional MLO baseline) for one race."""
    track, race_num, date = spec.get("track"), spec.get("race"), spec.get("date")
    print(f"\n=== {track} R{race_num} on {date} ===")

    race_id = _ensure_race(spec)
    if not race_id:
        return

    entries = db.get_entries(race_id)
    by_prog, by_name = _entry_indexes(entries)

    # Save expert picks
    sources_written = []
    for src in spec.get("picks", []):
        source = src.get("source") or "manual"
        picks = _normalize_pick_dicts(src.get("picks", []), by_prog, by_name)
        if not picks:
            continue
        db.save_picks(race_id, source, picks)
        sources_written.append(f"{source}({len(picks)})")
    print(f"  saved picks: {', '.join(sources_written) or '(none)'}")

    # Optional MLO-favorite baseline
    if add_mlo:
        mlo = mlo_favorite_pick(entries)
        if mlo:
            db.save_picks(race_id, "mlo_favorite",
                          [{"horse_name": mlo[1], "program_number": mlo[0],
                            "rank": 1, "comment": ""}])
            print(f"  mlo_favorite: #{mlo[0]} {mlo[1]} (MLO {mlo[2]})")
        else:
            print("  mlo_favorite: (no active entries - skipped)")

    # Optional results: save if supplied
    if spec.get("results"):
        results = manual_input._parse_results_section(
            spec["results"], by_prog, by_name)
        if results:
            db.save_results(race_id, results)
            print(f"  saved results: {len(results)} finishers")

    # Score if results are present (whether just saved or already in DB)
    stored_results = db.get_results(race_id)
    if stored_results:
        snaps = accuracy_mod.run_accuracy_checks(race_id)
        print("  accuracy:")
        for s in snaps:
            print(f"    {s['source']:<18} top={s['top_pick']:<22} "
                  f"fin={s['finish']!s:<4} W={s['hit_win']} P={s['hit_place']} S={s['hit_show']}")
    else:
        print("  (no results yet - race not official; picks recorded, unscored)")

    # Consensus (live). If the MLO baseline was added, also show experts-only.
    all_picks = db.get_picks(race_id)
    res = consensus_mod.aggregate(entries, all_picks)
    print("  consensus table:")
    for line in consensus_mod.format_table(res).splitlines():
        print("    " + line)
    if res["best_pick"]:
        bp = res["best_pick"]
        print(f"  BEST PICK (consensus): #{bp['program_number']} {bp['horse_name']} "
              f"(MLO {bp['morning_line_odds']})  pts={bp['points']:.0f}  "
              f"#1={bp['first_votes']}/{res['num_sources']}  "
              f"conf={res['confidence']*100:.0f}%  margin=+{res['margin']:.0f}")
    if add_mlo:
        expert_picks = [p for p in all_picks if p["source"] != "mlo_favorite"]
        eres = consensus_mod.aggregate(entries, expert_picks)
        if eres["best_pick"]:
            bp = eres["best_pick"]
            print(f"  consensus (experts only, excl. MLO baseline): "
                  f"#{bp['program_number']} {bp['horse_name']}  "
                  f"pts={bp['points']:.0f}  #1={bp['first_votes']}/"
                  f"{eres['num_sources']}")


def main():
    ap = argparse.ArgumentParser(
        prog="record_picks.py",
        description="Record manual expert picks (+ optional results) for one or "
                    "more races, score accuracy, and print a summary.")
    ap.add_argument("--input", required=True, help="Path to the multi-race JSON file.")
    ap.add_argument("--no-mlo-favorite", action="store_true",
                    help="Do not add the synthetic mlo_favorite baseline source.")
    ap.add_argument("--no-summary", action="store_true",
                    help="Skip the cross-race accuracy summary at the end.")
    args = ap.parse_args()

    db.init_db()
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    add_mlo = not args.no_mlo_favorite and bool(data.get("add_mlo_favorite", True))
    races = data.get("races", [])
    if not races:
        print("No 'races' in input file.")
        return

    print(f"Recording {len(races)} race(s); add_mlo_favorite={add_mlo}")
    for spec in races:
        record_race(spec, add_mlo)

    if not args.no_summary:
        print("\n=== Accuracy summary (all scored races in DB) ===")
        print(accuracy_mod.format_summary(accuracy_mod.summary()))


if __name__ == "__main__":
    main()