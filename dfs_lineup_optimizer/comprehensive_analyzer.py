"""
Comprehensive NBA DFS analyzer with contest type detection and proper scoring rules.
"""

from draft_kings import Client, Sport
from pydfs_lineup_optimizer.player import Player
from contest_detector import (
    detect_contest_type, get_contest_info, display_contest_info
)
from nba_rotations import is_starter
from db import init_db, save_game, save_player_performance, save_lineup
from player_builder import create_pydfs_players_with_scoring
from utils import SALARY_CAP, display_scoring_rules, run_and_save, get_draftkings_client
from datetime import datetime, timezone
from typing import List


def generate_showdown_lineups(players: List[Player], contest_info, n_lineups=5) -> List[dict]:
    """Generate optimal showdown lineups using exhaustive combinatorial enumeration.

    Delegates to lineup_optimizer for the actual search.
    Uses top-15-by-value + starters as captain candidates (no rotation meta needed).
    """
    from lineup_optimizer import generate_optimal_showdown_lineups

    return generate_optimal_showdown_lineups(
        players, player_meta=None, n_lineups=n_lineups,
        captain_filter=lambda p, m: True,  # All players eligible as captain for classic
        min_util_salary=3000
    )


def generate_classic_lineups(players: List[Player], n_lineups=5) -> List:
    """Generate optimal classic lineups using pydfs optimizer."""
    from lineup_optimizer import generate_classic_lineups as _generate_classic
    return _generate_classic(players, n_lineups)


def main():
    """Main analysis function."""
    client = get_draftkings_client()

    try:
        contests_response = client.contests(Sport.NBA)
    except Exception as e:
        print(f"ERROR: Failed to fetch contests from DraftKings API: {e}")
        print("This may be due to a network issue or API change. Please try again later.")
        return

    # Find next NBA contest
    showdown_contests = []
    classic_contests = []
    now = datetime.now(timezone.utc)

    for contest in contests_response.contests:
        contest_type = detect_contest_type(contest.name)
        if contest.starts_at > now and 'WNBA' not in contest.name:
            if contest_type.value == "showdown":
                showdown_contests.append((contest, contest.starts_at))
            else:
                classic_contests.append((contest, contest.starts_at))

    # Prioritize showdown if available
    if showdown_contests:
        showdown_contests.sort(key=lambda x: x[1])
        contest, start_time = showdown_contests[0]
        contest_type_str = "showdown"
    elif classic_contests:
        classic_contests.sort(key=lambda x: x[1])
        contest, start_time = classic_contests[0]
        contest_type_str = "classic"
    else:
        print("No upcoming NBA contests found.")
        return

    print(f"Found next NBA {contest_type_str.upper()} contest starting at {start_time}\n")

    # Get contest info
    contest_info = get_contest_info(contest.contest_id, contest.name)
    display_contest_info(contest_info)
    display_scoring_rules(contest_type_str)

    # Get player data
    try:
        draftables = client.draftables(contest.draft_group_id)
    except Exception as e:
        print(f"ERROR: Failed to fetch draftable players: {e}")
        return
    print(f"\nFound {len(draftables.players)} draftable players\n")

    # Create players with proper scoring (rotation metadata needed for showdown)
    result = create_pydfs_players_with_scoring(draftables, include_rotation_meta=(contest_type_str == "showdown"))
    if contest_type_str == "showdown":
        players, player_meta = result
    else:
        players = result

    # Generate lineups based on contest type
    if contest_type_str == "showdown":
        print("Generating optimal showdown lineups with captain optimization...")
        lineups = generate_showdown_lineups(players, contest_info, n_lineups=5)

        print(f"\nGenerated {len(lineups)} optimal showdown lineups\n")

        # Display showdown lineups
        for i, lineup in enumerate(lineups, 1):
            print(f"Lineup {i}:")
            print(f"  Captain: {lineup['captain'].full_name}")
            print(f"    {lineup['captain'].team}  ${lineup['captain'].salary:,} (cap: ${lineup['captain'].salary * 1.5:,.0f})")
            print(f"    Projected: {lineup['captain'].fppg:.1f} fppg -> {lineup['captain'].fppg * 1.5:.1f} fppg (with captain multiplier)")
            print(f"\n  Utility Players:")
            for util in lineup['utility']:
                print(f"    {util.full_name:<25} {util.team:3}  ${util.salary:>6,}  {util.fppg:5.1f} fppg")

            print(f"\n  Total: {lineup['total_fppg']:.1f} fppg, ${lineup['total_salary']:,.0f} salary")
            print()

    else:
        print("Generating optimal classic lineups...")
        lineups = generate_classic_lineups(players, n_lineups=5)

        print(f"\nGenerated {len(lineups)} optimal classic lineups\n")

        # Display classic lineups
        for i, lineup in enumerate(lineups, 1):
            print(f"Lineup {i}:")
            print(f"  Projected Points: {lineup.fantasy_points_projection:.1f}")
            print(f"  Total Salary: ${lineup.salary_costs:,.0f}")

            for player in lineup:
                pos_str = '/'.join(player.positions)
                print(f"    {pos_str:8} {player.full_name:<25} ${player.salary:>6,} {player.fppg:6.1f} fppg")
            print()

    # Player value rankings
    print("=" * 70)
    print("PLAYER VALUE RANKINGS (DK Scoring)")
    print("Players under $3,000 salary excluded from rankings")
    print("=" * 70)

    player_values = []
    for player in players:
        if player.salary >= 3000:
            value = player.fppg / (player.salary / 1000)
            player_values.append((player, value))

    player_values.sort(key=lambda x: x[1], reverse=True)

    for i, (player, value) in enumerate(player_values[:15], 1):
        pos_str = '/'.join(player.positions)
        print(f"{i:3}. {player.full_name:25} {pos_str:8} ${player.salary:>6,}  {player.fppg:6.1f} fppg ({value:.1f}X)")

    # Save predictions to database for later tracking
    try:
        init_db()

        # Parse contest name for teams (e.g., "NBA Showdown ... (NYK @ SAS)")
        import re
        team_match = re.search(r'\(([A-Z]{3})\s*@\s*([A-Z]{3})\)', contest.name)
        away_team = team_match.group(1) if team_match else "UNK"
        home_team = team_match.group(2) if team_match else "UNK"

        game_id = save_game(
            date=start_time.strftime('%Y-%m-%d'),
            away_team=away_team,
            home_team=home_team,
            contest_name=contest.name,
            contest_type=contest_type_str
        )
        print(f"  [DB] Saved game: {away_team} @ {home_team} (id={game_id})")

        # Save player projections (no actual fppg yet - that comes from prediction_tracker)
        saved_count = 0
        for player in players:
            if player.salary >= 3000:
                starter = is_starter(player.full_name, player.team)
                save_player_performance(
                    game_id=game_id,
                    player_name=player.full_name,
                    team=player.team,
                    salary=int(player.salary),
                    projected_fppg=player.fppg,
                    is_starter=starter
                )
                saved_count += 1
        print(f"  [DB] Saved {saved_count} player projections")

        # Save predicted lineups (no actual scores yet)
        if contest_type_str == "showdown":
            for i, lineup in enumerate(lineups, 1):
                captain = lineup['captain']
                captain_proj = captain.fppg * 1.5
                total_projected = lineup['total_fppg']
                total_salary = lineup['total_salary']

                utility_players = []
                for util in lineup['utility']:
                    utility_players.append({
                        "name": util.full_name,
                        "salary": int(util.salary),
                        "projected": util.fppg,
                        "actual": None
                    })

                save_lineup(
                    game_id=game_id,
                    lineup_type="predicted",
                    rank=i,
                    captain_name=captain.full_name,
                    captain_salary=int(captain.salary),
                    captain_projected=captain_proj,
                    total_projected=total_projected,
                    total_salary=total_salary,
                    utility_players=utility_players
                )
            print(f"  [DB] Saved {len(lineups)} predicted lineups")

        print(f"\n  Tip: Run prediction_tracker.py after the game to compare against actual results!")
    except Exception as e:
        print(f"  [DB] Warning: Could not save to database: {e}")

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    run_and_save(main, prefix='comprehensive_analysis_')