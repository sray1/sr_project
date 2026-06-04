"""
Comprehensive NBA DFS analyzer with contest type detection and proper scoring rules.
"""

from draft_kings import Client, Sport
from pydfs_lineup_optimizer import get_optimizer, Site, Sport as PyDFSSport
from pydfs_lineup_optimizer.player import Player
from contest_detector import (
    detect_contest_type, get_contest_info, display_contest_info,
    apply_captain_multiplier, calculate_lineup_score
)
from draftkings_scoring import DKScoringCalculator, REALISTIC_STAT_LINES, PlayerStats
from nba_rotations import get_rotation_status, is_starter, NBA_ROTATIONS
from db import init_db, save_game, save_player_performance, save_lineup
import tempfile
import sys
from datetime import datetime, timezone
from typing import List, Tuple

# Use WNBA as placeholder for NBA in pydfs
PyDFSSport.BASKETBALL = PyDFSSport.WNBA


def create_pydfs_players_with_scoring(draftables, contest_type):
    """Create pydfs players with proper DK scoring for contest type. Filters out injured/unavailable players."""
    pydfs_players = []
    calculator = DKScoringCalculator()

    # Get all realistic stat lines
    stat_lines_by_name = {}
    for player_id, stats in REALISTIC_STAT_LINES.items():
        if stats.points > 0:
            stat_lines_by_name[player_id] = stats

    player_stat_mapping = {}

    # Track players by full name to deduplicate
    player_name_map = {}

    for player in draftables.players:
        # Skip injured or unavailable players
        if player.is_disabled:
            continue

        positions = player.position_name.split('/')
        full_name = player.name_details.display

        # Find matching stat line
        fppg = None

        # Try to find by player ID first
        for stat_id, stats in stat_lines_by_name.items():
            if str(player.player_id) == stat_id:
                fppg = calculator.calculate_fantasy_points(stats)
                break

        # If no ID match, find by position and salary proximity
        if fppg is None:
            best_match = None
            best_diff = float('inf')

            for stat_id, stats in stat_lines_by_name.items():
                if stat_id not in player_stat_mapping:
                    expected_fppg = calculator.calculate_fantasy_points(stats)
                    expected_salary = expected_fppg * 200

                    # Position matching
                    pos_match = False
                    if 'C' in positions and stats.rebounds >= 8:
                        pos_match = True
                    elif 'PG' in positions and stats.assists >= 5:
                        pos_match = True
                    elif 'SG' in positions and stats.points >= 12:
                        pos_match = True
                    elif 'SF' in positions or 'PF' in positions:
                        pos_match = True

                    if pos_match:
                        salary_diff = abs(expected_salary - player.salary)
                        if salary_diff < best_diff:
                            best_diff = salary_diff
                            best_match = stat_id

            if best_match:
                fppg = calculator.calculate_fantasy_points(stat_lines_by_name[best_match])
                player_stat_mapping[best_match] = True

        # Fallback calculation
        if fppg is None:
            fppg = player.salary / 1000

        # Deduplicate players by name - keep lower salary (utility position)
        if full_name in player_name_map:
            existing_player = player_name_map[full_name]
            if player.salary < existing_player['salary']:
                # Replace with lower salary entry
                player_name_map[full_name] = {
                    'salary': player.salary,
                    'fppg': fppg,
                    'player_id': str(player.player_id),
                    'first_name': player.name_details.first,
                    'last_name': player.name_details.last,
                    'positions': positions,
                    'team': player.team_details.abbreviation
                }
        else:
            player_name_map[full_name] = {
                'salary': player.salary,
                'fppg': fppg,
                'player_id': str(player.player_id),
                'first_name': player.name_details.first,
                'last_name': player.name_details.last,
                'positions': positions,
                'team': player.team_details.abbreviation
            }

    # Create Player objects from deduplicated data
    for player_data in player_name_map.values():
        pydfs_player = Player(
            player_id=player_data['player_id'],
            first_name=player_data['first_name'],
            last_name=player_data['last_name'],
            positions=player_data['positions'],
            team=player_data['team'],
            salary=player_data['salary'],
            fppg=player_data['fppg'],
            is_injured=False,
        )
        pydfs_players.append(pydfs_player)

    return pydfs_players


def generate_showdown_lineups(players: List[Player], contest_info, n_lineups=5) -> List[dict]:
    """
    Generate optimal showdown lineups with captain optimization.

    For showdown, we need to optimize both:
    1. Which player to make captain
    2. Which 5 utility players to select

    Captain candidates are the top 15 by value (fppg per $1k).
    Players under $3000 salary are excluded from lineups (unless user requests).
    Utility players must have at least 5 fppg.
    Starters are prioritized over rotation players.
    """
    calculator = DKScoringCalculator()
    lineups = []

    # Sort players by value (fppg per $1k)
    sorted_players = sorted(players, key=lambda p: p.fppg / (p.salary / 1000), reverse=True)

    # Top 15 by value are captain candidates, plus any starters from rotation data
    top_15_by_value = sorted_players[:15]
    starter_names = set()
    for team_data in NBA_ROTATIONS.values():
        for name in team_data["starting"]:
            starter_names.add(name)

    # Add starters not already in top 15
    for player in sorted_players:
        if player.full_name in starter_names and player not in top_15_by_value:
            top_15_by_value.append(player)

    captain_candidates = top_15_by_value

    # Sort captain candidates by salary (ascending) to find viable captains first
    # since high-salary captains may not fit the cap with 1.5x multiplier
    captain_candidates_by_salary = sorted(captain_candidates, key=lambda p: p.salary)

    # Generate lineups with different captain choices
    valid_lineups = 0
    for captain in captain_candidates_by_salary:
        if valid_lineups >= n_lineups:
            break

        # In showdown, captain salary counts as 1.5 spots in salary cap calculation
        # remaining_salary = 50000 - (captain.salary * 1.5)
        remaining_salary = 50000 - (captain.salary * 1.5)

        # Select 5 utility players (excluding captain)
        util_players = [p for p in sorted_players if p.id != captain.id]

        # Filter out players with less than 5 fppg for utility spots
        util_players = [p for p in util_players if p.fppg >= 5]

        # Filter out players under $3000 from utility spots
        util_players = [p for p in util_players if p.salary >= 3000]

        # Sort utility players by value (descending) then pick best that fit budget
        # Use a greedy approach that maximizes total fppg within salary constraint
        util_players.sort(key=lambda p: p.fppg / (p.salary / 1000), reverse=True)

        # First try: greedy by value
        best_lineup = []
        best_fppg = 0

        # Strategy 1: Greedy by value (highest value first)
        selected_utils = []
        current_salary = 0
        for player in util_players:
            if len(selected_utils) >= 5:
                break
            if current_salary + player.salary <= remaining_salary:
                selected_utils.append(player)
                current_salary += player.salary

        # If we don't have 5, fill with cheapest available
        if len(selected_utils) < 5:
            remaining = [p for p in util_players if p not in selected_utils]
            remaining.sort(key=lambda p: p.salary)
            for player in remaining:
                if len(selected_utils) >= 5:
                    break
                if current_salary + player.salary <= remaining_salary:
                    selected_utils.append(player)
                    current_salary += player.salary

        if len(selected_utils) == 5:
            total_fppg = sum(p.fppg for p in selected_utils)
            if total_fppg > best_fppg:
                best_lineup = selected_utils[:]
                best_fppg = total_fppg

        # Strategy 2: Greedy by fppg (highest scoring first), filling all 5 spots
        selected_utils2 = []
        current_salary2 = 0
        util_by_fppg = sorted(util_players, key=lambda p: p.fppg, reverse=True)
        for player in util_by_fppg:
            if len(selected_utils2) >= 5:
                break
            if current_salary2 + player.salary <= remaining_salary:
                selected_utils2.append(player)
                current_salary2 += player.salary

        if len(selected_utils2) < 5:
            remaining = [p for p in util_players if p not in selected_utils2]
            remaining.sort(key=lambda p: p.salary)
            for player in remaining:
                if len(selected_utils2) >= 5:
                    break
                if current_salary2 + player.salary <= remaining_salary:
                    selected_utils2.append(player)
                    current_salary2 += player.salary

        if len(selected_utils2) == 5:
            total_fppg2 = sum(p.fppg for p in selected_utils2)
            if total_fppg2 > best_fppg:
                best_lineup = selected_utils2[:]
                best_fppg = total_fppg2

        # Strategy 3: Budget-aware - start with cheap players then upgrade
        selected_utils3 = []
        current_salary3 = 0
        cheapest = sorted(util_players, key=lambda p: p.salary)
        for player in cheapest:
            if len(selected_utils3) >= 5:
                break
            if current_salary3 + player.salary <= remaining_salary:
                selected_utils3.append(player)
                current_salary3 += player.salary

        # Now try to upgrade each slot with higher-fppg players
        for i, current in enumerate(selected_utils3):
            for player in util_players:
                if player not in selected_utils3 and player.id != captain.id:
                    new_salary = current_salary3 - current.salary + player.salary
                    if new_salary <= remaining_salary and player.fppg > current.fppg:
                        selected_utils3[i] = player
                        current_salary3 = new_salary
                        break

        if len(selected_utils3) == 5:
            total_fppg3 = sum(p.fppg for p in selected_utils3)
            if total_fppg3 > best_fppg:
                best_lineup = selected_utils3[:]
                best_fppg = total_fppg3

        selected_utils = best_lineup

        # Calculate total salary (captain 1.5x + utilities)
        total_salary = (captain.salary * 1.5) + sum(p.salary for p in selected_utils)

        # Only add lineup if it stays within salary cap and has 5 players
        if total_salary <= 50000 and len(selected_utils) >= 5:
            lineup = {
                'captain': captain,
                'utility': selected_utils,
                'total_fppg': captain.fppg * 1.5 + sum(p.fppg for p in selected_utils),
                'total_salary': total_salary,
                'captain_cap_salary': captain.salary * 1.5
            }
            lineups.append(lineup)
            valid_lineups += 1

    return lineups


def generate_classic_lineups(players: List[Player], n_lineups=5) -> List:
    """Generate optimal classic lineups using pydfs optimizer."""
    optimizer = get_optimizer(Site.DRAFTKINGS, PyDFSSport.BASKETBALL)
    optimizer.load_players(players)
    return list(optimizer.optimize(n=n_lineups))


def display_scoring_rules(contest_type):
    """Display scoring rules for the contest type."""
    print("=" * 70)
    print("DRAFTKINGS NBA SCORING RULES")
    print("=" * 70)

    print("\nBase Scoring:")
    print("  Points: +1.0")
    print("  Rebounds: +1.25")
    print("  Assists: +1.5")
    print("  Steals: +2.0")
    print("  Blocks: +2.0")
    print("  Turnovers: -0.5")
    print("  3-Pointers Made: +0.5")
    print("  Double-Double: +1.5")
    print("  Triple-Double: +3.0")

    if contest_type == "showdown":
        print("\nShowdown-Specific Rules:")
        print("  - Roster: 6 players (1 Captain + 5 UTIL)")
        print("  - Captain: 1.5x multiplier on BOTH points AND salary")
        print("  - Salary Cap: $50,000")
        print("  - Captain counts as 1.5 spots in salary calculation")
    else:
        print("\nClassic-Specific Rules:")
        print("  - Roster: 8 players (PG/SG/SF/PF/C positions)")
        print("  - Salary Cap: $50,000")
        print("  - Standard position requirements")

    print("=" * 70)


def main():
    """Main analysis function."""
    client = Client()
    contests_response = client.contests(Sport.NBA)

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
    draftables = client.draftables(contest.draft_group_id)
    print(f"\nFound {len(draftables.players)} draftable players\n")

    # Create players with proper scoring
    players = create_pydfs_players_with_scoring(draftables, contest_type_str)

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
    # Save output to temp file
    original_stdout = sys.stdout

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='comprehensive_analysis_') as temp_file:
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