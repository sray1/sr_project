"""
Compare predicted DFS lineups to actual game results.

Fetches NBA box scores via nba_api with StatMuse verification,
calculates actual DK fantasy points, compares against projected lineups,
finds the best possible lineup within the salary cap, and saves everything
to a SQLite database for tracking accuracy over time.

Usage:
  python dfs_lineup_optimizer/prediction_tracker.py              # Run tracker (default: NYK @ SAS)
  python dfs_lineup_optimizer/prediction_tracker.py --away SAS --home NYK --date 2026-06-08
  python dfs_lineup_optimizer/prediction_tracker.py --history     # View past results
  python dfs_lineup_optimizer/prediction_tracker.py --summary     # View accuracy summary
  python dfs_lineup_optimizer/prediction_tracker.py --player "Josh Hart"  # Player history
"""

import argparse
from draftkings_scoring import DKScoringCalculator
from game_results import (
    confirm_game_played, fetch_box_score, fetch_statmuse_box_score,
    verify_box_score, find_best_possible_lineup
)
from db import (
    init_db, save_game, save_player_performance, save_lineup,
    get_game_details, display_history, display_accuracy_summary,
    display_player_history
)
from nba_rotations import is_starter
from utils import SALARY_CAP, run_and_save


def fetch_game_results(away_team="NYK", home_team="SAS", date="2026-06-04"):
    """
    Fetch actual game results with multi-source verification.

    Flow:
      1. Confirm the game was played (nba_api scoreboard + StatMuse)
      2. Fetch box score from nba_api (primary source)
      3. Fetch box score from StatMuse (secondary source)
      4. Cross-verify and merge data from both sources
      5. Build player_scores dict with DK fantasy points + salary estimates

    Returns:
        tuple: (player_scores, actual_data, game_info) or (None, None, None) if game not found
    """
    calc = DKScoringCalculator()

    # Step 1: Confirm the game was played
    print(f"Confirming game: {away_team} @ {home_team} on {date}...")
    game_info = confirm_game_played(date, away_team, home_team)

    if not game_info["played"]:
        print(f"\n  ERROR: Game not found or not yet played for {away_team} @ {home_team} on {date}")
        print(f"  The game may not have started, or the teams/date may be incorrect.")
        print(f"  Check the schedule and try again with --away and --home flags.")
        return None, None, None

    print(f"  Game confirmed: {game_info['final_score']} (source: {game_info['source']})")

    # Step 2: Try nba_api as primary source
    actual_data = None
    source = "unknown"
    nba_api_data = None
    statmuse_data = None

    try:
        print(f"\n  Fetching box score from nba_api...")
        nba_api_data = fetch_box_score(date=date, home_team=home_team, away_team=away_team)
        if nba_api_data:
            print(f"  Got data for {len(nba_api_data)} players from nba_api")
            source = "nba_api"
        else:
            print(f"  nba_api returned no data")
    except Exception as e:
        print(f"  nba_api error: {e}")

    # Step 3: Try StatMuse as secondary source
    try:
        print(f"\n  Fetching box score from StatMuse...")
        statmuse_data = fetch_statmuse_box_score(date, away_team, home_team)
        if statmuse_data:
            print(f"  Got data for {len(statmuse_data)} players from StatMuse")
            if not nba_api_data:
                source = "statmuse"
        else:
            print(f"  StatMuse returned no data")
    except Exception as e:
        print(f"  StatMuse error: {e}")

    # Step 4: Merge/verify data
    if nba_api_data and statmuse_data:
        print(f"\n  Cross-verifying nba_api vs StatMuse data...")
        actual_data = verify_box_score(nba_api_data, statmuse_data)
        source = "merged (nba_api + statmuse)"
    elif nba_api_data:
        actual_data = nba_api_data
    elif statmuse_data:
        actual_data = statmuse_data
    else:
        print(f"\n  ERROR: Could not fetch game results from any source")
        print(f"  The game may not have box score data available yet.")
        return None, None, None

    print(f"\n  Using data from: {source}")
    print(f"  Total players: {len(actual_data)}")

    # Step 5: Build player_scores dict
    player_scores = {}
    for name, data in actual_data.items():
        base_fppg = calc.calculate_fantasy_points(data["stats"])
        # Estimate salary from fppg if no salary data available (~200x fppg)
        salary = data.get("salary", int(base_fppg * 200))
        player_scores[name] = {
            "fppg": base_fppg,
            "salary": salary,
            "team": data["team"],
        }

    game_info["source"] = source
    return player_scores, actual_data, game_info


def display_actual_scores(player_scores, actual_data):
    """Display actual DK fantasy points for all players."""
    calc = DKScoringCalculator()

    print("=" * 80)
    print("ACTUAL DK FANTASY POINTS (from game stats)")
    print("=" * 80)

    sorted_players = sorted(player_scores.items(), key=lambda x: x[1]["fppg"], reverse=True)

    for name, data in sorted_players:
        base = data["fppg"]
        captain = base * 1.5

        # Get stat details if available
        stats_entry = actual_data.get(name)
        if stats_entry and "stats" in stats_entry:
            s = stats_entry["stats"]
            dd = (s.points >= 10 and s.rebounds >= 10) or \
                 (s.points >= 10 and s.assists >= 10) or \
                 (s.rebounds >= 10 and s.assists >= 10)
            td = s.points >= 10 and s.rebounds >= 10 and s.assists >= 10

            bonus_str = ""
            if td:
                bonus_str = " [TRIPLE-DOUBLE +3.0]"
            elif dd:
                bonus_str = " [DOUBLE-DOUBLE +1.5]"

            print(f"  {name:<25} PTS:{s.points:3} REB:{s.rebounds:2} AST:{s.assists:2} "
                  f"STL:{s.steals} BLK:{s.blocks} TO:{s.turnovers} 3PM:{s.three_pointers}"
                  f"  -> {base:6.1f} UTIL / {captain:6.1f} CPT{bonus_str}")
        else:
            print(f"  {name:<25} {base:6.1f} UTIL / {captain:6.1f} CPT")


def display_lineup_comparison(player_scores, actual_data, predicted_lineups, projected_fppg):
    """Compare predicted lineups against actual results.

    Args:
        player_scores: Dict of {name: {"fppg": float, "salary": float, "team": str}}
        actual_data: Dict of actual game data
        predicted_lineups: List of predicted lineup dicts
        projected_fppg: Dict of {name: projected_fppg}
    """
    if not predicted_lineups:
        print("\n  No predicted lineups to compare against.")
        return 0, 0

    print("\n" + "=" * 80)
    print("LINEUP-BY-LINEUP COMPARISON (Projected vs Actual)")
    print("=" * 80)

    best_lineup_num = 0
    best_actual_total = 0

    for i, lineup in enumerate(predicted_lineups, 1):
        captain = lineup["captain"]

        print(f"\nLineup {i}: Captain = {captain}")
        print(f"  {'Role':<8} {'Player':<25} {'Projected':>8} {'Actual':>8} {'Diff':>8}")
        print(f"  {'-'*8} {'-'*25} {'-'*8} {'-'*8} {'-'*8}")

        # Captain row
        cap_proj_base = projected_fppg.get(captain, 0)
        cap_proj_cpt = cap_proj_base * 1.5
        cap_actual_base = player_scores.get(captain, {}).get("fppg", 0)
        cap_actual_cpt = cap_actual_base * 1.5

        diff = cap_actual_cpt - cap_proj_cpt
        print(f"  {'CPT':<8} {captain:<25} {cap_proj_cpt:8.1f} {cap_actual_cpt:8.1f} {diff:+8.1f}")

        lineup_projected_total = cap_proj_cpt
        lineup_actual_total = cap_actual_cpt

        for name, salary in lineup["utility"]:
            proj = projected_fppg.get(name, 0)
            actual = player_scores.get(name, {}).get("fppg", 0)
            diff = actual - proj

            print(f"  {'UTIL':<8} {name:<25} {proj:8.1f} {actual:8.1f} {diff:+8.1f}")

            lineup_projected_total += proj
            lineup_actual_total += actual

        total_diff = lineup_actual_total - lineup_projected_total
        pct_diff = (total_diff / lineup_projected_total * 100) if lineup_projected_total > 0 else 0
        print(f"  {'-'*57}")
        print(f"  {'TOTAL':<8} {'':<25} {lineup_projected_total:8.1f} {lineup_actual_total:8.1f} {total_diff:+8.1f} ({pct_diff:+.1f}%)")

        if lineup_actual_total > best_actual_total:
            best_actual_total = lineup_actual_total
            best_lineup_num = i

    return best_lineup_num, best_actual_total


def display_best_possible_lineup(player_scores, predicted_lineups=None, projected_fppg=None):
    """Find and display the best possible showdown lineup within the salary cap.

    Args:
        player_scores: Dict of {name: {"fppg": float, "salary": float, "team": str}}
        predicted_lineups: Optional list of predicted lineups for efficiency comparison
        projected_fppg: Optional dict of projected fppg for each player
    """
    print("\n" + "=" * 80)
    print("BEST POSSIBLE LINEUP (Highest actual fppg within $50,000 salary cap)")
    print("=" * 80)

    best_lineups = find_best_possible_lineup(player_scores, salary_cap=SALARY_CAP, min_salary=3000, top_n=5)

    if not best_lineups:
        print("\n  Could not find any valid lineups within the salary cap.")
        print("  This may happen if there are not enough players with salary >= $3,000.")
        return 0

    for i, lineup in enumerate(best_lineups, 1):
        print(f"\n  Best Lineup {i}:")
        print(f"  Captain: {lineup['captain']}")
        print(f"    Salary: ${lineup['captain_salary']:,} (cap: ${lineup['captain_salary'] * 1.5:,.0f})")
        cap_actual = lineup['captain_actual_fppg'] * 1.5
        print(f"    Actual: {lineup['captain_actual_fppg']:.1f} fppg -> {cap_actual:.1f} fppg (CPT)")
        print(f"\n  Utility Players:")

        for name, data in lineup["utility"]:
            print(f"    {name:<25} ${data['salary']:>6,}  {data['fppg']:6.1f} fppg")

        print(f"\n  Total: {lineup['total_fppg']:.1f} actual fppg, ${lineup['total_salary']:,.0f} salary")

    # Compare our best predicted lineup to the theoretical best
    theoretical_best = best_lineups[0]["total_fppg"]

    if predicted_lineups and projected_fppg:
        best_predicted = 0
        for lineup in predicted_lineups:
            cap_actual = player_scores.get(lineup["captain"], {}).get("fppg", 0) * 1.5
            util_actual = sum(player_scores.get(name, {}).get("fppg", 0) for name, _ in lineup["utility"])
            total = cap_actual + util_actual
            if total > best_predicted:
                best_predicted = total

        efficiency = (best_predicted / theoretical_best * 100) if theoretical_best > 0 else 0
        print(f"\n  Our best lineup: {best_predicted:.1f} fppg")
        print(f"  Theoretical best: {theoretical_best:.1f} fppg")
        print(f"  Lineup efficiency: {efficiency:.1f}%")
    else:
        print(f"\n  Theoretical best: {theoretical_best:.1f} fppg")

    return theoretical_best


def save_results_to_db(player_scores, actual_data, best_lineup_num, best_actual_total,
                       away_team="NYK", home_team="SAS", date="2026-06-04",
                       game_info=None, predicted_lineups=None, projected_fppg=None):
    """Save all tracking results to the SQLite database.

    Args:
        player_scores: Dict of {name: {"fppg": float, "salary": float, "team": str}}
        actual_data: Dict of actual game data
        best_lineup_num: Number of the best predicted lineup
        best_actual_total: Total actual fppg of the best predicted lineup
        away_team: Away team abbreviation
        home_team: Home team abbreviation
        date: Game date in YYYY-MM-DD format
        game_info: Dict with game confirmation info (final_score, source, etc.)
        predicted_lineups: List of predicted lineup dicts
        projected_fppg: Dict of {name: projected_fppg}
    """
    init_db()

    # Save game
    contest_name = f"NBA Showdown ({away_team} @ {home_team})"
    if game_info and game_info.get("final_score"):
        contest_name += f" - {game_info['final_score']}"

    game_id = save_game(
        date=date, away_team=away_team, home_team=home_team,
        contest_name=contest_name,
        contest_type="showdown"
    )
    print(f"\n  [DB] Saved game: {away_team} @ {home_team} on {date} (id={game_id})")

    # Save player performances
    saved_players = 0
    for name, data in player_scores.items():
        # Use projected_fppg if available, otherwise estimate from salary
        proj = projected_fppg.get(name, data["fppg"]) if projected_fppg else data["fppg"]
        actual = data["fppg"]
        team = data["team"]
        salary = data["salary"]
        starter = is_starter(name, team)

        # Get box score stats if available
        stats_entry = actual_data.get(name)
        stats_dict = None
        if stats_entry and "stats" in stats_entry:
            s = stats_entry["stats"]
            stats_dict = {
                "points": s.points, "rebounds": s.rebounds,
                "assists": s.assists, "steals": s.steals,
                "blocks": s.blocks, "turnovers": s.turnovers,
                "three_pointers": s.three_pointers,
            }

        save_player_performance(
            game_id=game_id, player_name=name, team=team,
            salary=salary, projected_fppg=proj,
            actual_fppg=actual, is_starter=starter,
            stats=stats_dict
        )
        saved_players += 1

    print(f"  [DB] Saved {saved_players} player performances")

    # Save predicted lineups (if available)
    if predicted_lineups and projected_fppg:
        for i, lineup in enumerate(predicted_lineups, 1):
            captain = lineup["captain"]
            captain_proj = projected_fppg.get(captain, 0) * 1.5
            captain_actual = player_scores.get(captain, {}).get("fppg", 0) * 1.5

            total_projected = captain_proj
            total_actual = captain_actual
            total_salary = lineup["captain_salary"] * 1.5
            utility_players = []

            for name, salary in lineup["utility"]:
                proj = projected_fppg.get(name, 0)
                actual = player_scores.get(name, {}).get("fppg", 0)
                total_projected += proj
                total_actual += actual
                total_salary += salary
                utility_players.append({
                    "name": name, "salary": salary,
                    "projected": proj, "actual": actual
                })

            save_lineup(
                game_id=game_id, lineup_type="predicted", rank=i,
                captain_name=captain, captain_salary=lineup["captain_salary"],
                captain_projected=captain_proj, captain_actual=captain_actual,
                total_projected=total_projected, total_actual=total_actual,
                total_salary=total_salary, utility_players=utility_players
            )

        print(f"  [DB] Saved {len(predicted_lineups)} predicted lineups")

    # Save best possible lineups
    best_lineups = find_best_possible_lineup(player_scores, salary_cap=SALARY_CAP, min_salary=3000, top_n=5)
    for i, lineup in enumerate(best_lineups, 1):
        captain_actual_cpt = lineup["captain_actual_fppg"] * 1.5

        utility_players = []
        for name, data in lineup["utility"]:
            utility_players.append({
                "name": name, "salary": data["salary"],
                "projected": 0, "actual": data["fppg"]
            })

        save_lineup(
            game_id=game_id, lineup_type="best_possible", rank=i,
            captain_name=lineup["captain"], captain_salary=lineup["captain_salary"],
            captain_projected=0, captain_actual=captain_actual_cpt,
            total_projected=0, total_actual=lineup["total_fppg"],
            total_salary=lineup["total_salary"], utility_players=utility_players
        )

    print(f"  [DB] Saved {len(best_lineups)} best possible lineups")


def main():
    """Main entry point for the prediction tracker."""
    # Parse CLI arguments
    parser = argparse.ArgumentParser(description="DFS Prediction Tracker")
    parser.add_argument("--history", action="store_true", help="Show past game tracking history")
    parser.add_argument("--summary", action="store_true", help="Show accuracy summary across all games")
    parser.add_argument("--player", type=str, help="Show projection history for a specific player")
    parser.add_argument("--away", type=str, default="NYK", help="Away team abbreviation (default: NYK)")
    parser.add_argument("--home", type=str, default="SAS", help="Home team abbreviation (default: SAS)")
    parser.add_argument("--date", type=str, default="2026-06-04", help="Game date YYYY-MM-DD (default: 2026-06-04)")
    args = parser.parse_args()

    init_db()

    # Handle history/summary/player modes
    if args.history:
        display_history()
        return
    if args.summary:
        display_accuracy_summary()
        return
    if args.player:
        display_player_history(args.player)
        return

    # Default: run full prediction tracking
    print("=" * 80)
    print("DFS PREDICTION TRACKER")
    print(f"{args.away} @ {args.home} | {args.date}")
    print("=" * 80)

    # Fetch game results with multi-source verification
    result = fetch_game_results(away_team=args.away, home_team=args.home, date=args.date)

    if result is None or result[0] is None:
        print("\n  No game data available. Exiting.")
        print("  Use --history or --summary to view past tracking data.")
        return

    player_scores, actual_data, game_info = result

    # Display actual scores
    display_actual_scores(player_scores, actual_data)

    # Try to load predicted lineups from DB for this game
    # (In production, these would come from the showdown analyzer run before the game)
    predicted_lineups = None
    projected_fppg = None

    # Compare predicted lineups vs actual (if available)
    best_lineup_num, best_actual_total = display_lineup_comparison(
        player_scores, actual_data, predicted_lineups or [], projected_fppg or {}
    )

    # Display best possible lineup
    theoretical_best = display_best_possible_lineup(
        player_scores, predicted_lineups=predicted_lineups, projected_fppg=projected_fppg
    )

    # Save results to database
    save_results_to_db(
        player_scores, actual_data, best_lineup_num, best_actual_total,
        away_team=args.away, home_team=args.home, date=args.date,
        game_info=game_info,
        predicted_lineups=predicted_lineups, projected_fppg=projected_fppg
    )

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if game_info:
        print(f"\n  Game: {game_info.get('final_score', 'N/A')}")
        print(f"  Data source: {game_info.get('source', 'unknown')}")

    print(f"  Players tracked: {len(player_scores)}")

    if predicted_lineups and best_lineup_num > 0:
        print(f"\n  Best predicted lineup by actual score: Lineup {best_lineup_num} ({best_actual_total:.1f} fppg)")

    if theoretical_best > 0:
        print(f"  Best possible lineup: {theoretical_best:.1f} fppg")

    print(f"\n  Results saved to database. Use --history or --summary to view past data.")


if __name__ == "__main__":
    run_and_save(main, prefix='prediction_tracker_')