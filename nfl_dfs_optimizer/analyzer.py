"""
NFL DFS analyzer: main entry point for lineup generation.

Supports both DraftKings NFL contest formats:
- Showdown (single game): 1 Captain + 5 FLEX, pulp MILP optimizer
- Classic (Sunday main slate): 1 QB/2 RB/3 WR/1 TE/1 FLEX/1 DST, pydfs optimizer

Usage:
    python nfl_dfs_optimizer/analyzer.py                          # auto mode
    python nfl_dfs_optimizer/analyzer.py --mode showdown
    python nfl_dfs_optimizer/analyzer.py --mode classic --stack qb2
    python nfl_dfs_optimizer/analyzer.py --contest-id 12345678
    python nfl_dfs_optimizer/analyzer.py --csv my_projections.csv --lineups 3
"""

import argparse
from datetime import datetime, timezone, timedelta

from contest_detector import ContestType, detect_contest_type, get_contest_info, display_contest_info
from dk_client import (fetch_nfl_contests, select_showdown_contest,
                      select_main_slate_contest, fetch_draftables, show_contest_details)
from player_builder import build_player_pool, build_pydfs_players
from projections import get_player_projections, display_projection_sources
from showdown_optimizer import generate_showdown_lineups, validate_lineup as validate_showdown
from classic_optimizer import (generate_classic_lineups, lineup_to_dict,
                                validate_classic_lineup, STACK_RULES)
from utils import run_and_save

ET = timezone(timedelta(hours=-5))


def print_showdown_lineup(lineup, rank):
    """Pretty-print one showdown lineup."""
    captain = lineup['captain']
    print(f"\nLineup {rank}: {lineup['total_projection']:.2f} projected | "
          f"${lineup['total_salary']:,.0f} salary")

    print(f"  CPT  {captain['name']:<25} {captain['team']:<5} "
          f"${captain['salary'] * 1.5:>8,.0f}  "
          f"{lineup['captain_cap_projection']:>6.2f} proj")

    for player in lineup['flex']:
        value = player['projection'] / (player['salary'] / 1000) if player['salary'] else 0
        print(f"  FLEX {player['name']:<25} {player['team']:<5} "
              f"${player['salary']:>8,.0f}  {player['projection']:>6.2f} proj "
              f"({value:.2f}x)")


def print_classic_lineup(lineup_dict, rank):
    """Pretty-print one classic lineup."""
    print(f"\nLineup {rank}: {lineup_dict['total_projection']:.2f} projected | "
          f"${lineup_dict['total_salary']:,.0f} salary")

    for player in lineup_dict['players']:
        value = (player['projection'] / (player['salary'] / 1000)
                 if player['salary'] else 0)
        opponent = f"  {player['opponent']}" if player.get('opponent') else ''
        print(f"  {player['lineup_position']:<5} {player['name']:<25} "
              f"{player['team']:<5} ${player['salary']:>8,.0f}  "
              f"{player['projection']:>6.2f} proj ({value:.2f}x){opponent}")


def select_contest(args):
    """Select the contest to analyze based on CLI args.

    Returns:
        (contest, mode) where mode is 'showdown' or 'classic'
    """
    contests = fetch_nfl_contests()
    if not contests:
        print("ERROR: No NFL contests found (off-season or API unavailable)")
        return None, None

    if args.contest_id:
        contest = next((c for c in contests if c.contest_id == args.contest_id), None)
        if not contest:
            print(f"ERROR: Contest {args.contest_id} not found in upcoming NFL contests")
            return None, None
        mode = 'showdown' if (detect_contest_type(contest.name) == ContestType.SHOWDOWN
                               or args.mode == 'showdown') else 'classic'
        return contest, mode

    # Auto mode: main slate on Sundays, showdown otherwise
    mode = args.mode
    if mode == 'auto':
        today_et = datetime.now(ET)
        mode = 'classic' if today_et.weekday() == 6 else 'showdown'

    if mode == 'showdown':
        contest = select_showdown_contest(contests)
    else:
        contest = select_main_slate_contest(contests)

    if not contest:
        print(f"ERROR: No {'showdown' if mode == 'showdown' else 'main-slate classic'} "
              f"contest available")
        return None, None

    return contest, mode


def run_analysis(args):
    """Main pipeline: contest -> draftables -> projections -> lineups."""
    contest, mode = select_contest(args)
    if not contest:
        return

    print(f"Selected contest: {contest.name}")
    show_contest_details(contest)
    print()

    # Draftables & projections
    print(f"Fetching draftables for draft group {contest.draft_group_id}...")
    draftables = fetch_draftables(contest.draft_group_id)
    print(f"Found {len(draftables)} draftable entries")

    player_projections = get_player_projections(
        draftables, csv_path=args.csv, week=args.week, allow_scrape=not args.no_scrape)

    pool = build_player_pool(draftables, player_projections)
    display_projection_sources(player_projections, pool)
    print(f"\nPlayer pool: {len(pool)} players "
          f"(deduped, min salary, active only)")

    # Optimize
    if mode == 'showdown':
        lineups = generate_showdown_lineups(
            pool, n_lineups=args.lineups, allow_dst_captain=not args.no_dst_captain)

        print(f"\n{'=' * 70}")
        print(f"OPTIMAL SHOWDOWN LINEUPS ({len(lineups)})")
        print(f"{'=' * 70}")

        for rank, lineup in enumerate(lineups, 1):
            print_showdown_lineup(lineup, rank)
            violations = validate_showdown(lineup)
            if violations:
                print(f"  RULE VIOLATIONS: {violations}")

        if not lineups:
            print("No feasible lineups found — check the player pool and filters")
    else:
        pydfs_players = build_pydfs_players(pool)
        lineups = generate_classic_lineups(
            pydfs_players, n_lineups=args.lineups, stack_rule=args.stack)

        print(f"\n{'=' * 70}")
        print(f"OPTIMAL CLASSIC LINEUPS ({len(lineups)}) — stack rule: {args.stack}")
        print(f"{'=' * 70}")

        for rank, lineup in enumerate(lineups, 1):
            lineup_dict = lineup_to_dict(lineup)
            print_classic_lineup(lineup_dict, rank)
            violations = validate_classic_lineup(lineup_dict)
            if violations:
                print(f"  RULE VIOLATIONS: {violations}")

        if not lineups:
            print("No feasible lineups found — check stack rule and player pool")


def parse_args():
    parser = argparse.ArgumentParser(description="NFL DFS lineup optimizer (DraftKings)")
    parser.add_argument('--mode', choices=['auto', 'showdown', 'classic'],
                        default='auto',
                        help="Contest mode (default: auto = classic on Sundays, "
                             "showdown otherwise)")
    parser.add_argument('--contest-id', type=int, default=None,
                        help="Specific DK contest ID (skips auto-selection)")
    parser.add_argument('--csv', default=None,
                        help="Manual projections CSV (name, points columns)")
    parser.add_argument('--week', type=int, default=None,
                        help="NFL week for projection scrapers")
    parser.add_argument('--lineups', type=int, default=5,
                        help="Number of lineups to generate (default 5; "
                             "classic default top-only is 1 with --lineups 1)")
    parser.add_argument('--stack', choices=STACK_RULES, default='qbwr',
                        help="Classic stacking rule (default qbwr: QB + 1 WR/TE)")
    parser.add_argument('--no-scrape', action='store_true',
                        help="Skip web projection scrapers (CSV + fallback only)")
    parser.add_argument('--no-dst-captain', action='store_true',
                        help="Showdown: forbid DST as captain")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.mode == 'classic' and args.lineups == 5 and not args.contest_id:
        args.lineups = 1  # classic defaults to top lineup only

    def _run():
        run_analysis(args)

    prefix = 'nfl_dfs_analysis_'
    run_and_save(_run, prefix=prefix, output_dir='output')


if __name__ == "__main__":
    main()