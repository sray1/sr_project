"""
NBA Showdown analyzer with captain optimization and proper DK scoring.
"""

from draft_kings import Client, Sport
from contest_detector import detect_contest_type, get_contest_info, display_contest_info
from draftkings_scoring import DKScoringCalculator, REALISTIC_STAT_LINES
from pydfs_lineup_optimizer.player import Player
import tempfile
import sys
from datetime import datetime, timezone


def create_pydfs_players_with_scoring(draftables):
    """Create players with proper DK scoring."""
    pydfs_players = []
    calculator = DKScoringCalculator()

    # Get realistic stat lines
    stat_lines_by_name = {}
    for player_id, stats in REALISTIC_STAT_LINES.items():
        if stats.points > 0:
            stat_lines_by_name[player_id] = stats

    player_stat_mapping = {}

    for player in draftables.players:
        positions = player.position_name.split('/')
        fppg = None

        # Try to find by player ID
        for stat_id, stats in stat_lines_by_name.items():
            if str(player.player_id) == stat_id:
                fppg = calculator.calculate_fantasy_points(stats)
                break

        # Find by position and salary if no ID match
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


def generate_showdown_lineups(players, n_lineups=5):
    """Generate optimal showdown lineups with captain optimization."""
    # Sort players by value (fppg per $1k)
    sorted_players = sorted(players, key=lambda p: p.fppg / (p.salary / 1000), reverse=True)

    # Identify starting players (highest salary players are typically starters)
    # In NBA showdown, starters are usually the top 6-8 players by salary
    starting_players = sorted_players[:8]

    # Captain candidates should be from starting players only
    captain_candidates = starting_players[:6]
    lineups = []

    # Generate lineups with different captain choices
    for i, captain in enumerate(captain_candidates[:n_lineups]):
        captain_salary = captain.salary * 1.5
        remaining_salary = 50000 - captain_salary

        # Select 5 utility players (excluding captain)
        util_players = [p for p in sorted_players if p.id != captain.id]

        # Greedy selection based on value within salary constraints
        selected_utils = []
        current_salary = 0

        for player in util_players:
            if len(selected_utils) >= 5:
                break
            if current_salary + player.salary <= remaining_salary:
                selected_utils.append(player)
                current_salary += player.salary

        # Fill remaining spots if needed
        while len(selected_utils) < 5:
            for player in util_players:
                if player not in selected_utils and player.id != captain.id:
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


def main():
    """Main showdown analysis."""
    client = Client()
    contests_response = client.contests(Sport.NBA)

    # Find NBA showdown contests
    showdown_contests = []

    for contest in contests_response.contests:
        contest_type = detect_contest_type(contest.name)
        if contest_type.value == "showdown" and 'WNBA' not in contest.name:
            total_entries = contest.entries_details.total if hasattr(contest, 'entries_details') else 0
            showdown_contests.append((contest, contest.starts_at, total_entries))

    # Sort by total entries (most entries first), then by start time
    showdown_contests.sort(key=lambda x: (-x[2], x[1]))

    # Debug: print what we found
    print(f"DEBUG: Found {len(showdown_contests)} showdown contests")
    for contest, start_time, entries in showdown_contests[:5]:
        print(f"  - {contest.name} at {start_time} ({entries:,} entries)")

    if not showdown_contests:
        print("No upcoming NBA Showdown contests found.")
        return

    # Get the showdown contest with most entries
    contest, start_time, entries = showdown_contests[0]
    print(f"\nAnalyzing showdown contest with most entries: {entries:,} entries")

    print(f"Found next NBA SHOWDOWN contest starting at {start_time}\n")

    # Get contest info
    contest_info = get_contest_info(contest.contest_id, contest.name)
    display_contest_info(contest_info)

    # Display entry count
    print(f"Total Entries: {entries:,}")
    print("=" * 70)

    # Display DK scoring rules
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

    print("\nShowdown-Specific Rules:")
    print("  - Roster: 6 players (1 Captain + 5 UTIL)")
    print("  - Captain: 1.5x multiplier on BOTH points AND salary")
    print("  - Salary Cap: $50,000")
    print("  - Captain counts as 1.5 spots in salary calculation")
    print("=" * 70)

    # Get player data
    draftables = client.draftables(contest.draft_group_id)
    print(f"\nFound {len(draftables.players)} draftable players\n")

    # Create players with proper scoring
    players = create_pydfs_players_with_scoring(draftables)

    # Generate showdown lineups
    print("Generating optimal showdown lineups with captain optimization...")
    lineups = generate_showdown_lineups(players, n_lineups=5)

    print(f"\nGenerated {len(lineups)} optimal showdown lineups\n")

    # Display showdown lineups
    for i, lineup in enumerate(lineups, 1):
        print(f"Lineup {i}:")
        print(f"  Captain: {lineup['captain'].full_name}")
        print(f"    Team: {lineup['captain'].team}")
        print(f"    Base Salary: ${lineup['captain'].salary:,}")
        print(f"    Captain Salary: ${lineup['captain'].salary * 1.5:,.0f} (1.5x multiplier)")
        print(f"    Base FPPG: {lineup['captain'].fppg:.1f}")
        print(f"    Captain FPPG: {lineup['captain'].fppg * 1.5:.1f} (1.5x multiplier)")
        print(f"    Value: {(lineup['captain'].fppg / (lineup['captain'].salary / 1000)):.1f}X")

        print(f"\n  Utility Players (5 spots):")
        for j, util in enumerate(lineup['utility'], 1):
            value = util.fppg / (util.salary / 1000) if util.salary > 0 else 0
            print(f"    {j}. {util.full_name:<25} {util.team:3}  ${util.salary:>6,}  {util.fppg:5.1f} fppg ({value:.1f}X)")

        print(f"\n  Lineup Totals:")
        print(f"    Total FPPG: {lineup['total_fppg']:.1f}")
        print(f"    Total Salary: ${lineup['total_salary']:,.0f}")
        print(f"    Salary Cap Remaining: ${50000 - lineup['total_salary']:,.0f}")
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
        captain_value = value * 1.5  # What their value would be as captain
        print(f"{i:3}. {player.full_name:<25} {pos_str:8} ${player.salary:>6,}  {player.fppg:6.1f} fppg ({value:.1f}X util / {captain_value:.1f}X cpt)")

    # Captain optimization analysis
    print("\n" + "=" * 70)
    print("CAPTAIN OPTIMIZATION ANALYSIS")
    print("=" * 70)

    print("\nBest Captain Candidates:")
    for i, (player, value) in enumerate(player_values[:6], 1):
        pos_str = '/'.join(player.positions)
        cpt_fppg = player.fppg * 1.5
        cpt_salary = player.salary * 1.5
        cpt_value = cpt_fppg / (cpt_salary / 1000)
        print(f"{i}. {player.full_name:<25} {pos_str:8}")
        print(f"   UTIL: {player.fppg:5.1f} fppg @ ${player.salary:>6,} ({value:.1f}X)")
        print(f"   CPT:  {cpt_fppg:5.1f} fppg @ ${cpt_salary:>6,} ({cpt_value:.1f}X)")

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    # Save output to temp file
    original_stdout = sys.stdout

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='nba_showdown_') as temp_file:
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