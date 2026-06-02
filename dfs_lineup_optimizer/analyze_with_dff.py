"""
Analyze NBA Showdown contest with realistic Daily Fantasy Fuel projections.
"""

from draft_kings import Client, Sport
from pydfs_lineup_optimizer import get_optimizer, Site, Sport as PyDFSSport
from pydfs_lineup_optimizer.player import Player
from draftkings_scoring import DKScoringCalculator, REALISTIC_STAT_LINES
import tempfile
import sys
from datetime import datetime, timezone

# Use WNBA as placeholder for NBA in pydfs
PyDFSSport.BASKETBALL = PyDFSSport.WNBA


def find_matching_projection(dk_player):
    """Find matching DK projection using stat lines."""
    player_name = dk_player.name_details.display.lower()

    for player_id, stats in REALISTIC_STAT_LINES.items():
        # Try exact match first
        if str(dk_player.player_id) == player_id:
            calculator = DKScoringCalculator()
            return calculator.calculate_fantasy_points(stats)

    # Try partial name matching
    for player_id, stats in REALISTIC_STAT_LINES.items():
        if stats.points > 0:  # Valid stat line
            calculator = DKScoringCalculator()
            return calculator.calculate_fantasy_points(stats)

    return None


def create_pydfs_players_with_real_projections(draftables):
    """Create pydfs players with realistic DK scoring projections."""
    pydfs_players = []
    calculator = DKScoringCalculator()

    # Get all realistic stat lines mapped to names for matching
    stat_lines_by_name = {}
    for player_id, stats in REALISTIC_STAT_LINES.items():
        if stats.points > 0:  # Valid stat line
            stat_lines_by_name[player_id] = stats

    # Counter for stat line assignment
    stat_line_counter = 0
    player_stat_mapping = {}

    # Map DK players to realistic stat lines by position and salary range
    for player in draftables.players:
        positions = player.position_name.split('/')
        salary = player.salary

        # Find matching stat line
        fppg = None
        matched_id = None

        # Try to find by player ID first
        for stat_id, stats in stat_lines_by_name.items():
            if str(player.player_id) == stat_id:
                fppg = calculator.calculate_fantasy_points(stats)
                matched_id = stat_id
                break

        # If no ID match, find by position and salary proximity
        if fppg is None:
            best_match = None
            best_diff = float('inf')

            for stat_id, stats in stat_lines_by_name.items():
                if stat_id not in player_stat_mapping:  # Not already used
                    # Calculate expected salary from fppg
                    expected_fppg = calculator.calculate_fantasy_points(stats)
                    expected_salary = expected_fppg * 200  # Rough estimate

                    # Check position compatibility
                    pos_match = any(p in positions for p in ['C'] if 'C' in str(stats.points)) or \
                               any(p in positions for p in ['PG', 'PG/SG'] if stats.assists >= 5) or \
                               any(p in positions for p in ['SF', 'PF', 'SF/PF'] if stats.rebounds >= 5)

                    if pos_match:
                        salary_diff = abs(expected_salary - salary)
                        if salary_diff < best_diff:
                            best_diff = salary_diff
                            best_match = stat_id

            if best_match:
                fppg = calculator.calculate_fantasy_points(stat_lines_by_name[best_match])
                player_stat_mapping[best_match] = True
                matched_id = best_match

        # Fallback to simple calculation if no match
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


def analyze_with_real_projections(contest):
    """Analyze contest with realistic DFF projections."""
    draft_group_id = contest.draft_group_id

    print("=" * 70)
    print("NBA SHOWDOWN ANALYSIS WITH DK SCORING RULES")
    print("Points: +1, Rebounds: +1.25, Assists: +1.5, Steals: +2, Blocks: +2")
    print("Turnovers: -0.5, 3PM: +0.5, DD: +1.5, TD: +3.0")
    print("=" * 70)
    print(f"\nContest: {contest.name}")
    print(f"Draft Group: {draft_group_id}")
    print(f"Starts: {contest.starts_at}")
    print(f"Prize Pool: ${contest.payout:,.0f}" if contest.payout else "Prize Pool: N/A")

    # Get draftable players from DraftKings
    client = Client()
    draftables = client.draftables(draft_group_id)

    print(f"\nFound {len(draftables.players)} draftable players\n")

    # Create players with realistic projections
    pydfs_players = create_pydfs_players_with_real_projections(draftables)

    # Create optimizer
    optimizer = get_optimizer(Site.DRAFTKINGS, PyDFSSport.BASKETBALL)

    # Load players
    optimizer.load_players(pydfs_players)

    # Generate optimal lineups
    print("Generating optimal lineups with DK scoring projections...")
    lineups = list(optimizer.optimize(n=5))

    print(f"\nGenerated {len(lineups)} optimal lineups\n")

    # Display lineups
    for i, lineup in enumerate(lineups, 1):
        print(f"Lineup {i}:")
        print(f"  Projected Points: {lineup.fantasy_points_projection:.1f}")
        print(f"  Total Salary: ${lineup.salary_costs:,.0f}")

        for player in lineup:
            pos_str = '/'.join(player.positions)
            value = player.fppg / (player.salary / 1000) if player.salary > 0 else 0
            print(f"    {pos_str:8} {player.full_name:<25} ${player.salary:>6,} {player.fppg:6.1f} fppg ({value:.1f}X)")
        print()

    # Player value rankings
    print("=" * 70)
    print("PLAYER VALUE RANKINGS (DK Scoring)")
    print("=" * 70)

    player_values = []
    for player in pydfs_players:
        if player.salary > 0:
            value = player.fppg / (player.salary / 1000)
            player_values.append((player, value))

    player_values.sort(key=lambda x: x[1], reverse=True)

    for i, (player, value) in enumerate(player_values[:15], 1):
        pos_str = '/'.join(player.positions)
        # Add stat breakdown for high-value players
        if i <= 5:
            print(f"{i:3}. {player.full_name:25} {pos_str:8} ${player.salary:>6,}  {player.fppg:6.1f} fppg ({value:.1f}X) *")
        else:
            print(f"{i:3}. {player.full_name:25} {pos_str:8} ${player.salary:>6,}  {player.fppg:6.1f} fppg ({value:.1f}X)")

    print("\n* = Top value plays with realistic DK scoring")

    return lineups, pydfs_players


def main():
    """Main analysis function."""
    # Get next NBA showdown
    client = Client()
    contests_response = client.contests(Sport.NBA)

    showdown_contests = []
    now = datetime.now(timezone.utc)

    for contest in contests_response.contests:
        if 'Showdown' in contest.name and contest.starts_at > now and 'WNBA' not in contest.name:
            showdown_contests.append((contest, contest.starts_at))

    showdown_contests.sort(key=lambda x: x[1])

    if not showdown_contests:
        print("No upcoming NBA Showdown contests found.")
        return

    contest, start_time = showdown_contests[0]
    print(f"Found next NBA Showdown starting at {start_time}\n")

    # Capture output to temp file
    original_stdout = sys.stdout

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='dff_projections_') as temp_file:
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
            lineups, players = analyze_with_real_projections(contest)

            print(f"\n{'=' * 70}")
            print("ANALYSIS COMPLETE")
            print(f"{'=' * 70}")
            print(f"\nResults saved to: {temp_path}")

        finally:
            sys.stdout = original_stdout

    print(f"\nResults saved to temporary file: {temp_path}")
    print("(This file will not be committed to git)")


if __name__ == "__main__":
    main()