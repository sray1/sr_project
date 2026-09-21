"""
Track NFL DFS projection-source accuracy over time (per contest).

Pre-game:  --save      snapshot every source's projections + each source's
                        optimal lineup into nfl_accuracy.db
Post-game: --score     fetch actual results via ESPN (game_results.py),
                        fill actual DK points, score every saved lineup
           --rescore   re-grade saved lineups from already-recorded
                        actuals (no network) — e.g. after expert lineups
                        were added to a graded contest
History:   --history   list saved contests + scoring status
           --summary   cumulative per-source accuracy (lineup + player MAE)

Usage:
  python prediction_tracker.py --save [--mode showdown] [--contest-id N]
  python prediction_tracker.py --score [--contest-id N]
  python prediction_tracker.py --history
  python prediction_tracker.py --summary
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone, timedelta

import accuracy_db as db
from analyzer import prepare_contest
from comparison import build_lineups_per_source
from expert_lineups import fetch_stokastic_lineup, print_expert_lineup
from game_results import fetch_slate_results
from player_builder import build_auto_exclusions, build_player_pool
from projections import (get_source_projections, normalize_dst_name,
                         normalize_name)

ET = timezone(timedelta(hours=-5))


def save_snapshot(args):
    """Pre-game: snapshot per-source projections + optimal lineups."""
    contest, mode, draftables = prepare_contest(args)
    if not contest:
        return

    source_projections = get_source_projections(
        draftables, csv_path=args.csv, week=args.week,
        allow_scrape=not args.no_scrape)
    drop_backup_qbs = not getattr(args, 'keep_backup_qbs', False)
    exclude = build_auto_exclusions(
        draftables, getattr(args, 'exclude', None),
        allow_scrape=not args.no_scrape)
    lineups_by_source = build_lineups_per_source(
        draftables, source_projections, mode,
        stack_rule=args.stack, allow_dst_captain=not args.no_dst_captain,
        drop_backup_qbs=drop_backup_qbs, exclude=exclude)

    if not lineups_by_source:
        print("No source produced a feasible lineup — nothing to save")
        return

    slate_games = sorted({p['game'] for p in draftables if p.get('game')})
    db.init_db()
    db.save_contest(contest.contest_id, contest.draft_group_id, contest.name,
                    mode, slate_games, starts_at=contest.starts_at)

    n_rows = 0
    for source, projections in source_projections.items():
        # One row per deduped pool player per source (same filters the
        # lineups were built with, so DB rows match the lineup pool).
        # verbose=False: the filter note printed during lineup building.
        pool = build_player_pool(draftables, projections,
                                 drop_backup_qbs=drop_backup_qbs,
                                 exclude=exclude, verbose=False)
        for player in pool:
            db.save_source_projection(contest.contest_id, source, player)
            n_rows += 1

    for source, lineup in lineups_by_source.items():
        db.save_lineup_prediction(contest.contest_id, source, mode, lineup)

    # Expert published lineup (classic slates only): Stokastic's weekly
    # cheat sheet publishes a fully-worked lineup — a roster-construction
    # yardstick, graded alongside the projection sources. Its players get
    # scored by normalized name like any other lineup.
    if mode == 'classic' and not args.no_scrape:
        expert = fetch_stokastic_lineup(draftables, week=args.week,
                                         starts_at=contest.starts_at)
        if expert:
            db.save_lineup_prediction(contest.contest_id, 'stokastic',
                                      mode, expert)
            print_expert_lineup(expert)

    print(f"\nSaved snapshot: {len(lineups_by_source)} sources, "
          f"{n_rows} projection rows, {len(slate_games)} slate games")
    for source in lineups_by_source:
        matched = sum(1 for v in source_projections[source].values()
                     if v['source'] != 'fallback')
        total = len(source_projections[source])
        print(f"  {source}: lineup saved ({matched}/{total} players matched)")


def _lineup_norm_name(player_name):
    """Normalize a lineup player name to the key actuals are stored under."""
    if 'dst' in player_name.lower() or 'defense' in player_name.lower():
        return normalize_dst_name(player_name)
    return normalize_name(player_name)


def grade_saved_lineups(contest_id):
    """Score every saved lineup for a contest from recorded actuals.

    Reads actual_fppg from source_projections (filled by --score, or by a
    previous run) and fills lineup_predictions.total_actual — no network.
    Used by --score, --rescore, and expert_import (lineups added to an
    already-graded contest).
    """
    conn = db.get_connection()
    actual_rows = conn.execute("""
        SELECT norm_name, actual_fppg FROM source_projections
        WHERE contest_id = ? AND actual_fppg IS NOT NULL
    """, (contest_id,)).fetchall()
    actuals = {row['norm_name']: row['actual_fppg'] for row in actual_rows}
    lineups = conn.execute("""
        SELECT id, source, mode, players_json FROM lineup_predictions
        WHERE contest_id = ?
    """, (contest_id,)).fetchall()
    conn.close()

    for lineup in lineups:
        players = json.loads(lineup['players_json'])
        total_actual = 0.0
        n_missing = 0
        for player in players:
            norm = _lineup_norm_name(player['name'])
            if norm in actuals:
                points = actuals[norm]
            else:
                points = 0.0  # did not play (or name mismatch)
                n_missing += 1
            if player.get('is_captain'):
                points *= 1.5
            total_actual += points

        if n_missing:
            print(f"  {lineup['source']}: {n_missing}/{len(players)} lineup "
                  f"players had no recorded actual (counted as 0)")
        conn = db.get_connection()
        conn.execute("UPDATE lineup_predictions SET total_actual = ? "
                     "WHERE id = ?", (round(total_actual, 2), lineup['id']))
        conn.commit()
        conn.close()


def _print_contest_accuracy(contest_id):
    """Report per-source accuracy for one contest."""
    accuracy = db.contest_accuracy(contest_id)
    if not accuracy:
        return
    print(f"\n  {'source':<22} {'proj':>7} {'actual':>7} {'diff':>7} "
          f"{'player MAE':>11}")
    for a in accuracy:
        mae = f"{a['player_mae']:.2f}" if a['player_mae'] is not None else '--'
        if a['projected_known']:
            proj = f"{a['lineup_projected']:>7.2f}"
            diff = f"{a['lineup_projected'] - a['lineup_actual']:>+7.2f}"
        else:  # 0.0 sentinel: published lineup, no stated projection
            proj = f"{'--':>7}"
            diff = f"{'--':>7}"
        print(f"  {a['source']:<22} {proj} {a['lineup_actual']:>7.2f} "
              f"{diff} {mae:>11}")


def _score_one_contest(contest_row, date_compact):
    """Fetch actuals for one contest, fill player rows, score all lineups."""
    contest_id = contest_row['contest_id']
    games = json.loads(contest_row['slate_json'])
    print(f"\nScoring contest [{contest_id}] {contest_row['name']} "
          f"(mode={contest_row['mode']}, {len(games)} games)")

    results = fetch_slate_results(date_compact, games)
    if not results:
        print("  No actual results found — game may not be finished yet")
        return

    n_updated = db.record_player_actuals(contest_id, results)
    print(f"  Recorded actual points for {n_updated} projection rows "
          f"({len(results)} players/DSTs found)")

    grade_saved_lineups(contest_id)
    _print_contest_accuracy(contest_id)


def _et_date_compact(iso_date):
    """Convert a stored ISO start time to an ESPN scoreboard date (ET)."""
    if not iso_date:
        return None
    try:
        dt = datetime.fromisoformat(iso_date)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).strftime('%Y%m%d')


def score_contests(args):
    """Post-game: fetch actuals and score saved contests."""
    db.init_db()

    if args.contest_id:
        rows = [r for r in db.list_contests()
                if r['contest_id'] == args.contest_id]
        if not rows:
            print(f"No saved contest {args.contest_id} — run --save first")
            return
    else:
        rows = [r for r in db.list_contests() if r['n_scored'] == 0]
        if not rows:
            print("No unscored contests (use --contest-id N to re-score)")

    for contest_row in rows:
        date_compact = args.date_compact or _et_date_compact(
            contest_row['starts_at'])
        if not date_compact:
            print(f"\nContest {contest_row['contest_id']} has no start time "
                  f"— pass --date YYYYMMDD")
            continue
        try:
            _score_one_contest(contest_row, date_compact)
        except sqlite3.Error as e:
            print(f"  Database error scoring {contest_row['contest_id']}: {e}")


def rescore_contests(args):
    """Re-grade saved lineups from recorded actuals (no network)."""
    db.init_db()

    if args.contest_id:
        rows = [r for r in db.list_contests()
                if r['contest_id'] == args.contest_id]
        if not rows:
            print(f"No saved contest {args.contest_id} — run --save first")
            return
    else:
        rows = [r for r in db.list_contests() if r['n_scored'] > 0]
        if not rows:
            print("No graded contests (only contests with recorded "
                  "actuals can be rescored)")
            return

    for contest_row in rows:
        contest_id = contest_row['contest_id']
        print(f"\nRescoring contest [{contest_id}] {contest_row['name']} "
              f"from recorded actuals")
        grade_saved_lineups(contest_id)
        _print_contest_accuracy(contest_id)


def parse_args():
    parser = argparse.ArgumentParser(
        description="NFL DFS projection-source accuracy tracker")
    parser.add_argument('--save', action='store_true',
                        help="Pre-game: snapshot projections + lineups")
    parser.add_argument('--score', action='store_true',
                        help="Post-game: fetch actuals, score saved lineups")
    parser.add_argument('--rescore', action='store_true',
                        help="Re-grade saved lineups from recorded actuals "
                             "(no network)")
    parser.add_argument('--history', action='store_true',
                        help="List saved contests + scoring status")
    parser.add_argument('--summary', action='store_true',
                        help="Cumulative per-source accuracy")
    # Contest selection (used by --save, mirroring analyzer.py)
    parser.add_argument('--mode', choices=['auto', 'showdown', 'classic'],
                        default='auto', help="Contest mode (default: auto)")
    parser.add_argument('--contest-id', type=int, default=None,
                        help="Specific DK contest ID (skips auto-selection)")
    parser.add_argument('--csv', default=None,
                        help="Manual projections CSV (a 'csv' source)")
    parser.add_argument('--week', type=int, default=None,
                        help="NFL week for projection scrapers")
    parser.add_argument('--stack', default='qbwr',
                        help="Classic stacking rule (default qbwr)")
    parser.add_argument('--no-scrape', action='store_true',
                        help="Skip web projection scrapers")
    parser.add_argument('--no-dst-captain', action='store_true',
                        help="Showdown: forbid DST as captain")
    parser.add_argument('--exclude', default=None,
                        help="Comma-separated player names to drop from all "
                             "lineups (backups, injuries)")
    parser.add_argument('--keep-backup-qbs', action='store_true',
                        help="Do NOT auto-drop backup QBs (default: keep "
                             "only each team's top-salaried QB)")
    # Scoring options
    parser.add_argument('--date', default=None, dest='date_compact',
                        help="Scoreboard date YYYYMMDD for --score "
                             "(default: contest start date, ET)")
    return parser.parse_args()


def main():
    args = parse_args()

    actions = [a for a, flag in
               [('save', args.save), ('score', args.score),
                ('rescore', args.rescore), ('history', args.history),
                ('summary', args.summary)]
               if flag]
    if not actions:
        print("Pick an action: --save, --score, --rescore, --history, "
              "or --summary")
        return

    def _run():
        for action in actions:
            {'save': lambda: save_snapshot(args),
             'score': lambda: score_contests(args),
             'rescore': lambda: rescore_contests(args),
             'history': db.display_history,
             'summary': db.display_accuracy_summary}[action]()

    from utils import run_and_save
    run_and_save(_run, prefix='nfl_tracker_', output_dir='output')


if __name__ == "__main__":
    main()