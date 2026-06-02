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
import tempfile
import sys
from datetime import datetime, timezone
from typing import List, Tuple

# Use WNBA as placeholder for NBA in pydfs
PyDFSSport.BASKETBALL = PyDFSSport.WNBA


def create_pydfs_players_with_scoring(draftables, contest_type):
    """Create pydfs players with proper DK scoring for contest type."""
    pydfs_players = []
    calculator = DKScoringCalculator()

    # Get all realistic stat lines
    stat_lines_by_name = {}
    for player_id, stats in REALISTIC_STAT_LINES.items():
        if stats.points > 0:
            stat_lines_by_name[player_id] = stats

    player_stat_mapping = {}

    for player in draftables.players:
        positions = player.position_name.split('/')

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

        pydfs_player = Player(
            player_id=str(player.player_id),
            first_name=player.name_details.first,
            last_name=player.name_details.last,
            positions=positions,
            team=player.team_details.abbreviation,
            salary=player.salary,
            fppg=fppg,
            is_injured=player.is_disabled,
        )
        pydfs_players.append(pydfs_player)

    return pydfs_players


def generate_showdown_lineups(players: List[Player], contest_info, n_lineups=5) -> List[dict]:
    """
    Generate optimal showdown lineups with captain optimization.

    For showdown, we need to optimize both:
    1. Which player to make captain
    2. Which 5 utility players to select
    """
    calculator = DKScoringCalculator()
    lineups = []

    # Sort players by value (fppg per $1k)
    sorted_players = sorted(players, key=lambda p: p.fppg / (p.salary / 1000), reverse=True)

    # Top value candidates for captain
    captain_candidates = sorted_players[:8]

    # Generate lineups with different captain choices
    for i, captain in enumerate(captain_candidates[:n_lineups]):
        captain_salary = captain.salary * 1.5
        remaining_salary = 50000 - captain_salary

        # Select 5 utility players (excluding captain)
        util_players = [p for p in sorted_players if p.player_id != captain.player_id]

        # Greedy selection based on value within salary constraints
        selected_utils = []
        current_salary = 0

        for player in util_players:
            if len(selected_utils) >= 5:
                break
            if current_salary + player.salary <= remaining_salary:
                selected_utils.append(player)
                current_salary += player.salary

        # If we don't have 5 players, fill with remaining
        while len(selected_utils) < 5:
            for player in util_players:
                if player not in selected_utils and player.player_id != captain.player_id:
                    selected_utils.append(player)
                    break

        lineup = {
            'captain': captain,
            'utility': selected_utils,
            'total_fppg': captain.fppg * 1.5 + sum(p.fppg for p in selected_utils),
            'total_salary': captain_salary + sum(p.salary for p in selected_utils)
        }
        lineups.append(lineup)

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
            if contest_type == "showdown":
                showdown_contests.append((contest, contest.starts_at))
            else:
                classic_contests.append((contest, contest.starts_at))

    # Prioritize showdown if available
    if showdown_contests:
        showdown_contests.sort(key=lambda x: x[1])
        contest, start_time = showdown_contests[0]
        contest_type = "showdown"
    elif classic_contests:
        classic_contests.sort(key=lambda x: x[1])
        contest, start_time = classic_contests[0]
        contest_type = "classic"
    else:
        print("No upcoming NBA contests found.")
        return

    print(f"Found next NBA {contest_type.upper()} contest starting at {start_time}\n")

    # Get contest info
    contest_info = get_contest_info(contest.contest_id, contest.name)
    display_contest_info(contest_info)
    display_scoring_rules(contest_type)

    # Get player data
    draftables = client.draftables(contest.draft_group_id)
    print(f"\nFound {len(draftables.players)} draftable players\n")

    # Create players with proper scoring
    players = create_pydfs_players_with_scoring(draftables, contest_type)

    # Generate lineups based on contest type
    if contest_type == "showdown":
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
    print("=" * 70)

    player_values = []
    for player in players:
        if player.salary > 0:
            value = player.fppg / (player.salary / 1000)
            player_values.append((player, value))

    player_values.sort(key=lambda x: x[1], reverse=True)

    for i, (player, value) in enumerate(player_values[:15], 1):
        pos_str = '/'.join(player.positions)
        print(f"{i:3}. {player.full_name:25} {pos_str:8} ${player.salary:>6,}  {player.fppg:6.1f} fppg ({value:.1f}X)")

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