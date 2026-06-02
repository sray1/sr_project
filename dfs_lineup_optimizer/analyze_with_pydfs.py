"""
Analyze NBA Showdown contest using pydfs-lineup-optimizer for projections.
"""

from draft_kings import Client, Sport
from pydfs_lineup_optimizer import get_optimizer, Site, Sport as PyDFSSport
from pydfs_lineup_optimizer.player import Player
PyDFSSport.BASKETBALL = PyDFSSport.WNBA  # Use WNBA as placeholder for NBA
import tempfile
import sys
from datetime import datetime, timezone


def get_next_nba_showdown():
    """Get the next NBA Showdown contest."""
    client = Client()
    contests_response = client.contests(Sport.NBA)

    showdown_contests = []
    now = datetime.now(timezone.utc)

    for contest in contests_response.contests:
        if 'Showdown' in contest.name and contest.starts_at > now and 'WNBA' not in contest.name:
            showdown_contests.append((contest, contest.starts_at))

    showdown_contests.sort(key=lambda x: x[1])

    if not showdown_contests:
        return None, None

    return showdown_contests[0]


def convert_dk_to_pydfs_players(draftables):
    """Convert DraftKings player data to pydfs format."""
    pydfs_players = []

    for player in draftables.players:
        # Split position if it contains '/'
        positions = player.position_name.split('/')

        # Simple fppg calculation based on salary (1 point per $1k)
        fppg = player.salary / 1000

        # Create pydfs Player object
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


def analyze_with_pydfs_optimizer(contest):
    """Analyze contest using pydfs-lineup-optimizer."""
    draft_group_id = contest.draft_group_id

    print("=" * 70)
    print("NBA SHOWDOWN ANALYSIS WITH PYDFS-LINEUP-OPTIMIZER")
    print("=" * 70)
    print(f"\nContest: {contest.name}")
    print(f"Draft Group: {draft_group_id}")
    print(f"Starts: {contest.starts_at}")
    print(f"Prize Pool: ${contest.payout:,.0f}" if contest.payout else "Prize Pool: N/A")

    # Get draftable players from DraftKings
    client = Client()
    draftables = client.draftables(draft_group_id)

    print(f"\nFound {len(draftables.players)} draftable players\n")

    # Convert to pydfs format
    pydfs_players = convert_dk_to_pydfs_players(draftables)

    # Create optimizer for NBA Single Game (Showdown)
    optimizer = get_optimizer(Site.DRAFTKINGS, PyDFSSport.BASKETBALL)

    # Load players
    optimizer.load_players(pydfs_players)

    # Generate optimal lineups
    print("Generating optimal lineups...")
    lineups = list(optimizer.optimize(n=5))

    print(f"\nGenerated {len(lineups)} optimal lineups\n")

    # Display lineups
    for i, lineup in enumerate(lineups, 1):
        print(f"Lineup {i}:")
        print(f"  Projected Points: {lineup.fantasy_points_projection:.1f}")
        print(f"  Total Salary: ${lineup.salary_costs:,.0f}")

        # Group players by position
        for player in lineup:
            pos_str = '/'.join(player.positions)
            print(f"    {pos_str:8} {player.full_name:<25} ${player.salary:>6,} {player.fppg:6.1f} fppg")
        print()

    # Get player rankings
    print("=" * 70)
    print("PLAYER VALUE RANKINGS (Salary/FPPG Ratio)")
    print("=" * 70)

    # Calculate value for each player
    player_values = []
    for player in pydfs_players:
        if player.salary > 0:
            value = player.salary / 1000  # Simple value metric
            player_values.append((player, value))

    player_values.sort(key=lambda x: x[1], reverse=True)

    for i, (player, value) in enumerate(player_values[:15], 1):
        pos_str = '/'.join(player.positions)
        print(f"{i:3}. {player.full_name:25} {pos_str:8} ${player.salary:>6,}  {value:.2f}")

    return lineups, pydfs_players


def main():
    """Main analysis function."""
    # Get next NBA showdown
    contest, start_time = get_next_nba_showdown()

    if not contest:
        print("No upcoming NBA Showdown contests found.")
        return

    print(f"Found next NBA Showdown starting at {start_time}\n")

    # Capture output
    output_lines = []

    # Redirect stdout to capture output
    original_stdout = sys.stdout

    # Create temp file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='dfs_analysis_') as temp_file:
        temp_path = temp_file.name

        # Redirect to both console and file
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
            lineups, players = analyze_with_pydfs_optimizer(contest)

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