"""
Script to fetch DraftKings contest information using the draft-kings client.
Includes contest type detection (Classic vs Showdown).
"""

from draft_kings import Client, Sport
from contest_detector import detect_contest_type
import tempfile
import sys


def fetch_contests(sport="NBA"):
    """
    Fetch available contests for a given sport.

    Args:
        sport: Sport abbreviation (e.g., 'NBA', 'NFL', 'MLB')
    """
    client = Client()
    print(f"Fetching contests for {sport}...")
    response = client.contests(sport)
    contests = response.contests if hasattr(response, 'contests') else []

    if not contests:
        print(f"No contests found for {sport}")
        return []

    print(f"Found {len(contests)} contests for {sport}\n")

    for i, contest in enumerate(contests[:5], 1):
        contest_type = detect_contest_type(contest.name)
        print(f"Contest {i}:")
        print(f"  Type: {contest_type.value.upper()}")
        print(f"  ID: {contest.contest_id}")
        print(f"  Name: {contest.name}")
        print(f"  Draft Group ID: {contest.draft_group_id}")
        print(f"  Starts At: {contest.starts_at}")
        print(f"  Guaranteed: {contest.is_guaranteed}")
        print(f"  Prize Pool: ${contest.payout:,}" if contest.payout else "  Prize Pool: N/A")
        print()

    return contests


def fetch_draftable_players(draft_group_id, limit=10):
    """
    Fetch draftable players for a given draft group.

    Args:
        draft_group_id: The draft group ID
        limit: Number of players to display
    """
    client = Client()
    print(f"Fetching draftable players for draft group {draft_group_id}...")

    draftables_response = client.draftables(draft_group_id)
    players = draftables_response.players if hasattr(draftables_response, 'players') else []

    print(f"\nFound {len(players)} players in draft group {draft_group_id}\n")

    for i, player in enumerate(players[:limit], 1):
        print(f"Player {i}:")
        print(f"  Name: {player.name_details.display}")
        print(f"  Position: {player.position_name}")
        print(f"  Team: {player.team_details.abbreviation}")
        print(f"  Competition: {player.competition_details.name}")
        print(f"  Salary: ${player.salary:,.0f}")
        print(f"  Starts At: {player.competition_details.starts_at}")
        print(f"  Draftable: {player.is_disabled == False}")
        print()


if __name__ == "__main__":
    # Save output to temp file
    original_stdout = sys.stdout

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='dk_fetch_') as temp_file:
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
            # Fetch NBA contests
            contests = fetch_contests(Sport.NBA)

            # If contests found, get players from the first contest's draft group
            if contests:
                first_contest = contests[0]
                print(f"Fetching players for contest: {first_contest.name}\n")
                fetch_draftable_players(first_contest.draft_group_id)

            print(f"\nResults saved to: {temp_path}")
        finally:
            sys.stdout = original_stdout

    print(f"\nResults saved to temporary file: {temp_path}")
    print("(This file will not be committed to git)")