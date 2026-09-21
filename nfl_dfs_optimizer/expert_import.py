"""Import a manually-transcribed expert lineup into nfl_accuracy.db.

For expert sources that publish a finished lineup but have no parser
(SI.com, Sporting News, fantasyleagues.info, ...): the picks are typed
in on the command line and validated against the contest's own
draftables (same gates as the Stokastic parser — distinct players,
stated salaries must match DK's, legal roster, under the cap). Any
failure aborts loudly; nothing is ever fabricated.

The lineup is saved to lineup_predictions under the given source label
with total_projection = 0.0 unless --total-projection is passed (most
articles don't state a projection) — the sentinel that keeps the row
out of --summary's lineup-level error stats while its actual still gets
graded. Contests already scored are re-graded from recorded actuals
(no network).

Usage:
  python expert_import.py --contest-id N --source NAME --mode showdown \
      --picks "CPT Josh Allen $17100; Jahmyr Gibbs $12000; ..."

  Picks are ';'-separated. Each pick: optional 'CPT ' prefix (showdown
  captain, stated at the 1.5x CPT-slot price the article prints), the
  player name, and '$X,XXX' (the DK salary as printed).
"""

import argparse
import re

import accuracy_db as db
from dk_client import fetch_draftables
from expert_lineups import build_expert_lineup, print_expert_lineup
from prediction_tracker import _print_contest_accuracy, grade_saved_lineups

PICK_RE = re.compile(r"^\s*(CPT\s+)?(.+?)\s+\$([\d,]+)\s*$",
                     re.IGNORECASE)


def parse_picks(picks_str):
    """'CPT Josh Allen $17100; Jahmyr Gibbs $12000' -> pick tuples."""
    picks = []
    for token in picks_str.split(';'):
        if not token.strip():
            continue
        match = PICK_RE.match(token)
        if not match:
            raise ValueError(f"cannot parse pick: {token.strip()!r} "
                             f"(expected 'Name $X,XXX' or 'CPT Name $X,XXX')")
        picks.append((match.group(2).strip(),
                      int(match.group(3).replace(',', '')),
                      bool(match.group(1))))
    return picks


def main():
    parser = argparse.ArgumentParser(
        description="Import + validate a transcribed expert lineup")
    parser.add_argument('--contest-id', type=int, required=True)
    parser.add_argument('--source', required=True,
                        help="Source label (e.g. 'si', 'sportingnews')")
    parser.add_argument('--mode', choices=['showdown', 'classic'],
                        required=True)
    parser.add_argument('--picks', required=True,
                        help="';'-separated 'Name $X,XXX' picks, optional "
                             "'CPT ' prefix for the showdown captain")
    parser.add_argument('--total-projection', type=float, default=0.0,
                        help="The article's stated projection total "
                             "(0.0 = none published)")
    args = parser.parse_args()

    db.init_db()
    contest = db.get_contest(args.contest_id)
    if not contest:
        print(f"No saved contest {args.contest_id} — run "
              f"prediction_tracker --save first")
        return
    if contest['mode'] != args.mode:
        print(f"Contest {args.contest_id} is mode={contest['mode']}, not "
              f"{args.mode} — nothing saved")
        return

    picks = parse_picks(args.picks)
    draftables = fetch_draftables(contest['draft_group_id'])
    print(f"Validating {len(picks)} picks against draft group "
          f"{contest['draft_group_id']} ({len(draftables)} entries)")
    lineup = build_expert_lineup(draftables, picks, args.mode,
                                 args.source,
                                 total_projection=args.total_projection)
    if lineup is None:
        print("Nothing saved")
        return

    db.save_lineup_prediction(args.contest_id, args.source, args.mode,
                              lineup)
    print_expert_lineup(lineup, source=args.source)
    print(f"\nSaved {args.source} lineup to nfl_accuracy.db "
          f"(contest {args.contest_id})")

    grade_saved_lineups(args.contest_id)
    _print_contest_accuracy(args.contest_id)


if __name__ == "__main__":
    main()