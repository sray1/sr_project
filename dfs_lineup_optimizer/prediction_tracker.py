"""
Compare predicted DFS lineups to actual game results.

Fetches NBA box scores via nba_api, calculates actual DK fantasy points,
compares against projected lineups, finds the best possible lineup
within the salary cap, and saves everything to a SQLite database
for tracking accuracy over time.

Usage:
  python dfs_lineup_optimizer/prediction_tracker.py              # Run tracker
  python dfs_lineup_optimizer/prediction_tracker.py --history     # View past results
  python dfs_lineup_optimizer/prediction_tracker.py --summary     # View accuracy summary
  python dfs_lineup_optimizer/prediction_tracker.py --player "Josh Hart"  # Player history
"""

import sys
import argparse
import tempfile
from draftkings_scoring import DKScoringCalculator, PlayerStats
from game_results import fetch_box_score, find_best_possible_lineup
from db import (
    init_db, save_game, save_player_performance, save_lineup,
    display_history, display_accuracy_summary, display_player_history
)
from nba_rotations import is_starter


# ============================================================
# FALLBACK: Hardcoded NYK @ SAS Game 1 results (June 4, 2026)
# Used when nba_api cannot fetch the data (offseason, API down, etc.)
# ============================================================

FALLBACK_NYK_STATS = {
    "Jalen Brunson": {"stats": PlayerStats(points=30, rebounds=3, assists=2, steals=0, blocks=0, turnovers=3, three_pointers=2), "team": "NYK"},
    "Karl-Anthony Towns": {"stats": PlayerStats(points=18, rebounds=12, assists=4, steals=0, blocks=1, turnovers=2, three_pointers=0), "team": "NYK"},
    "OG Anunoby": {"stats": PlayerStats(points=17, rebounds=3, assists=0, steals=1, blocks=1, turnovers=1, three_pointers=3), "team": "NYK"},
    "Landry Shamet": {"stats": PlayerStats(points=13, rebounds=1, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=3), "team": "NYK"},
    "Mikal Bridges": {"stats": PlayerStats(points=9, rebounds=3, assists=3, steals=2, blocks=0, turnovers=1, three_pointers=0), "team": "NYK"},
    "Josh Hart": {"stats": PlayerStats(points=3, rebounds=15, assists=6, steals=4, blocks=1, turnovers=2, three_pointers=0), "team": "NYK"},
    "Miles McBride": {"stats": PlayerStats(points=6, rebounds=1, assists=4, steals=0, blocks=1, turnovers=1, three_pointers=2), "team": "NYK"},
    "Jose Alvarado": {"stats": PlayerStats(points=7, rebounds=4, assists=1, steals=1, blocks=0, turnovers=0, three_pointers=1), "team": "NYK"},
    "Mitchell Robinson": {"stats": PlayerStats(points=2, rebounds=6, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=0), "team": "NYK"},
    "Jordan Clarkson": {"stats": PlayerStats(points=0, rebounds=1, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=0), "team": "NYK"},
    "Ariel Hukporti": {"stats": PlayerStats(points=0, rebounds=0, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=0), "team": "NYK"},
}

FALLBACK_SAS_STATS = {
    "Victor Wembanyama": {"stats": PlayerStats(points=26, rebounds=12, assists=2, steals=1, blocks=3, turnovers=4, three_pointers=2), "team": "SAS"},
    "Stephon Castle": {"stats": PlayerStats(points=17, rebounds=8, assists=3, steals=0, blocks=0, turnovers=2, three_pointers=1), "team": "SAS"},
    "Julian Champagnie": {"stats": PlayerStats(points=16, rebounds=9, assists=1, steals=0, blocks=0, turnovers=1, three_pointers=5), "team": "SAS"},
    "Dylan Harper": {"stats": PlayerStats(points=16, rebounds=8, assists=1, steals=1, blocks=0, turnovers=2, three_pointers=1), "team": "SAS"},
    "Devin Vassell": {"stats": PlayerStats(points=9, rebounds=10, assists=3, steals=0, blocks=1, turnovers=1, three_pointers=1), "team": "SAS"},
    "De'Aaron Fox": {"stats": PlayerStats(points=7, rebounds=4, assists=5, steals=1, blocks=0, turnovers=3, three_pointers=0), "team": "SAS"},
    "Keldon Johnson": {"stats": PlayerStats(points=3, rebounds=0, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=1), "team": "SAS"},
    "Luke Kornet": {"stats": PlayerStats(points=0, rebounds=2, assists=1, steals=0, blocks=0, turnovers=1, three_pointers=0), "team": "SAS"},
    "Carter Bryant": {"stats": PlayerStats(points=1, rebounds=0, assists=0, steals=0, blocks=0, turnovers=0, three_pointers=0), "team": "SAS"},
    "Harrison Barnes": {"stats": PlayerStats(points=0, rebounds=1, assists=1, steals=0, blocks=0, turnovers=1, three_pointers=0), "team": "SAS"},
}

FALLBACK_ALL_STATS = {**FALLBACK_NYK_STATS, **FALLBACK_SAS_STATS}

# Fallback salary data from our prediction run
FALLBACK_SALARIES = {
    "Victor Wembanyama": 12600, "Jalen Brunson": 10600, "Karl-Anthony Towns": 10200,
    "Stephon Castle": 8600, "Josh Hart": 8200, "OG Anunoby": 7200,
    "De'Aaron Fox": 7600, "Mikal Bridges": 6600, "Devin Vassell": 6200,
    "Julian Champagnie": 5800, "Dylan Harper": 5400, "Landry Shamet": 4800,
    "Miles McBride": 4400, "Mitchell Robinson": 4000, "Keldon Johnson": 3600,
    "Carter Bryant": 2800, "Luke Kornet": 2400, "Ariel Hukporti": 2000,
    "Harrison Barnes": 1600, "Jordan Clarkson": 1200,
}

# Our predicted lineups from the comprehensive analyzer run
PREDICTED_LINEUPS = [
    {
        "captain": "Landry Shamet",
        "captain_salary": 4800,
        "utility": [
            ("Karl-Anthony Towns", 10200),
            ("Jalen Brunson", 10600),
            ("Victor Wembanyama", 12600),
            ("Josh Hart", 8200),
            ("Keldon Johnson", 3600),
        ],
    },
    {
        "captain": "Miles McBride",
        "captain_salary": 4400,
        "utility": [
            ("Karl-Anthony Towns", 10200),
            ("Jalen Brunson", 10600),
            ("Victor Wembanyama", 12600),
            ("Josh Hart", 8200),
            ("Keldon Johnson", 3600),
        ],
    },
    {
        "captain": "Mitchell Robinson",
        "captain_salary": 4000,
        "utility": [
            ("Karl-Anthony Towns", 10200),
            ("Jalen Brunson", 10600),
            ("Victor Wembanyama", 12600),
            ("Josh Hart", 8200),
            ("Keldon Johnson", 3600),
        ],
    },
    {
        "captain": "Luke Kornet",
        "captain_salary": 2400,
        "utility": [
            ("Karl-Anthony Towns", 10200),
            ("Jalen Brunson", 10600),
            ("Victor Wembanyama", 12600),
            ("Josh Hart", 8200),
            ("Julian Champagnie", 5800),
        ],
    },
    {
        "captain": "Ariel Hukporti",
        "captain_salary": 2000,
        "utility": [
            ("Karl-Anthony Towns", 10200),
            ("Jalen Brunson", 10600),
            ("Victor Wembanyama", 12600),
            ("Josh Hart", 8200),
            ("Devin Vassell", 6200),
        ],
    },
]

# Projected fppg from comprehensive analyzer
PROJECTED_FPPG = {
    "Karl-Anthony Towns": 47.2, "Jalen Brunson": 47.2,
    "Victor Wembanyama": 56.0, "Josh Hart": 35.8,
    "OG Anunoby": 30.5, "De'Aaron Fox": 26.5,
    "Mikal Bridges": 23.0, "Devin Vassell": 21.2,
    "Julian Champagnie": 19.2, "Keldon Johnson": 15.2,
    "Dylan Harper": 15.5, "Stephon Castle": 37.2,
    "Landry Shamet": 4.8, "Miles McBride": 4.4,
    "Mitchell Robinson": 4.0, "Luke Kornet": 2.4,
    "Ariel Hukporti": 2.0, "Carter Bryant": 1.0,
    "Harrison Barnes": 0.5, "Jordan Clarkson": 0.5,
}


def fetch_game_results(away_team="NYK", home_team="SAS", date="2026-06-04"):
    """
    Fetch actual game results from nba_api. Falls back to hardcoded data
    if the API is unavailable (offseason, no game found, etc.).
    """
    calc = DKScoringCalculator()

    try:
        print(f"Fetching box score for {away_team} @ {home_team} on {date}...")
        actual_data = fetch_box_score(date=date, home_team=home_team, away_team=away_team)

        if not actual_data:
            raise ValueError("No player data returned")

        print(f"  Successfully fetched data for {len(actual_data)} players\n")

        # Build player_scores dict for find_best_possible_lineup
        player_scores = {}
        for name, data in actual_data.items():
            base_fppg = calc.calculate_fantasy_points(data["stats"])
            salary = FALLBACK_SALARIES.get(name, int(base_fppg * 200))
            player_scores[name] = {
                "fppg": base_fppg,
                "salary": salary,
                "team": data["team"],
            }

        return player_scores, actual_data

    except Exception as e:
        print(f"  Could not fetch from nba_api: {e}")
        print(f"  Using fallback data (NYK @ SAS Game 1, June 4, 2026)\n")

        # Use fallback data
        player_scores = {}
        for name, data in FALLBACK_ALL_STATS.items():
            base_fppg = calc.calculate_fantasy_points(data["stats"])
            salary = FALLBACK_SALARIES.get(name, int(base_fppg * 200))
            player_scores[name] = {
                "fppg": base_fppg,
                "salary": salary,
                "team": data["team"],
            }

        return player_scores, FALLBACK_ALL_STATS


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


def display_lineup_comparison(player_scores, actual_data):
    """Compare predicted lineups against actual results."""
    print("\n" + "=" * 80)
    print("LINEUP-BY-LINEUP COMPARISON (Projected vs Actual)")
    print("=" * 80)

    best_lineup_num = 0
    best_actual_total = 0

    for i, lineup in enumerate(PREDICTED_LINEUPS, 1):
        captain = lineup["captain"]

        print(f"\nLineup {i}: Captain = {captain}")
        print(f"  {'Role':<8} {'Player':<25} {'Projected':>8} {'Actual':>8} {'Diff':>8}")
        print(f"  {'-'*8} {'-'*25} {'-'*8} {'-'*8} {'-'*8}")

        # Captain row
        cap_proj_base = PROJECTED_FPPG.get(captain, 0)
        cap_proj_cpt = cap_proj_base * 1.5
        cap_actual_base = player_scores.get(captain, {}).get("fppg", 0)
        cap_actual_cpt = cap_actual_base * 1.5

        diff = cap_actual_cpt - cap_proj_cpt
        print(f"  {'CPT':<8} {captain:<25} {cap_proj_cpt:8.1f} {cap_actual_cpt:8.1f} {diff:+8.1f}")

        lineup_projected_total = cap_proj_cpt
        lineup_actual_total = cap_actual_cpt

        for name, salary in lineup["utility"]:
            proj = PROJECTED_FPPG.get(name, 0)
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


def display_best_possible_lineup(player_scores):
    """Find and display the best possible showdown lineup within the salary cap."""
    print("\n" + "=" * 80)
    print("BEST POSSIBLE LINEUP (Highest actual fppg within $50,000 salary cap)")
    print("=" * 80)

    best_lineups = find_best_possible_lineup(player_scores, salary_cap=50000, min_salary=3000, top_n=5)

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

    # Calculate best predicted lineup actual score
    best_predicted = 0
    for lineup in PREDICTED_LINEUPS:
        cap_actual = player_scores.get(lineup["captain"], {}).get("fppg", 0) * 1.5
        util_actual = sum(player_scores.get(name, {}).get("fppg", 0) for name, _ in lineup["utility"])
        total = cap_actual + util_actual
        if total > best_predicted:
            best_predicted = total

    efficiency = (best_predicted / theoretical_best * 100) if theoretical_best > 0 else 0
    print(f"\n  Our best lineup: {best_predicted:.1f} fppg")
    print(f"  Theoretical best: {theoretical_best:.1f} fppg")
    print(f"  Lineup efficiency: {efficiency:.1f}%")

    return theoretical_best


def save_results_to_db(player_scores, actual_data, best_lineup_num, best_actual_total,
                       away_team="NYK", home_team="SAS", date="2026-06-04"):
    """Save all tracking results to the SQLite database."""
    init_db()

    # Save game
    game_id = save_game(
        date=date, away_team=away_team, home_team=home_team,
        contest_name=f"NBA Showdown ({away_team} @ {home_team})",
        contest_type="showdown"
    )
    print(f"\n  [DB] Saved game: {away_team} @ {home_team} on {date} (id={game_id})")

    # Save player performances
    saved_players = 0
    for name, data in player_scores.items():
        projected = PROJECTED_FPPG.get(name, data["fppg"])
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
            salary=salary, projected_fppg=projected,
            actual_fppg=actual, is_starter=starter,
            stats=stats_dict
        )
        saved_players += 1

    print(f"  [DB] Saved {saved_players} player performances")

    # Save predicted lineups
    for i, lineup in enumerate(PREDICTED_LINEUPS, 1):
        captain = lineup["captain"]
        captain_proj = PROJECTED_FPPG.get(captain, 0) * 1.5
        captain_actual = player_scores.get(captain, {}).get("fppg", 0) * 1.5

        total_projected = captain_proj
        total_actual = captain_actual
        total_salary = lineup["captain_salary"] * 1.5
        utility_players = []

        for name, salary in lineup["utility"]:
            proj = PROJECTED_FPPG.get(name, 0)
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

    print(f"  [DB] Saved {len(PREDICTED_LINEUPS)} predicted lineups")

    # Save best possible lineups
    best_lineups = find_best_possible_lineup(player_scores, salary_cap=50000, min_salary=3000, top_n=5)
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

    # Fetch game results
    player_scores, actual_data = fetch_game_results(
        away_team=args.away, home_team=args.home, date=args.date
    )

    # Display actual scores
    display_actual_scores(player_scores, actual_data)

    # Compare predicted lineups vs actual
    best_lineup_num, best_actual_total = display_lineup_comparison(player_scores, actual_data)

    # Display best possible lineup
    theoretical_best = display_best_possible_lineup(player_scores)

    # Save results to database
    save_results_to_db(
        player_scores, actual_data, best_lineup_num, best_actual_total,
        away_team=args.away, home_team=args.home, date=args.date
    )

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\n  Best predicted lineup by actual score: Lineup {best_lineup_num} ({best_actual_total:.1f} fppg)")

    # Key observations
    key_players = ["Josh Hart", "Jalen Brunson", "Victor Wembanyama", "Karl-Anthony Towns"]
    print(f"\n  Key player observations:")
    for name in key_players:
        if name in player_scores:
            actual = player_scores[name]["fppg"]
            proj = PROJECTED_FPPG.get(name, 0)
            diff = actual - proj
            sign = "+" if diff >= 0 else ""
            print(f"  - {name}: projected {proj:.1f} fppg, actual {actual:.1f} fppg ({sign}{diff:.1f})")

    print(f"\n  Results saved to database. Use --history or --summary to view past data.")


if __name__ == "__main__":
    # Save output to temp file
    original_stdout = sys.stdout

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='prediction_tracker_') as temp_file:
        temp_path = temp_file.name

        class MultiOutput:
            def __init__(self, file1, file2):
                self.file1 = file1
                self.file2 = file2

            def write(self, text):
                self.file1.write(text)
                self.file2.write(text)

            def flush(self):
                self.file1.flush()
                self.file2.flush()

        sys.stdout = MultiOutput(original_stdout, temp_file)

        try:
            main()
            print(f"\nResults saved to: {temp_path}")
        finally:
            sys.stdout = original_stdout

    print(f"\nResults saved to temporary file: {temp_path}")
    print("(This file will not be committed to git)")