"""
List upcoming NBA DFS contests with contest type detection.
"""

from draft_kings import Client, Sport
from contest_detector import detect_contest_type
from datetime import datetime, timezone


def list_upcoming_nba_contests():
    """List all upcoming NBA contests with type detection."""
    client = Client()
    contests_response = client.contests(Sport.NBA)
    now = datetime.now(timezone.utc)

    print("=" * 80)
    print("UPCOMING NBA DFS CONTESTS")
    print("=" * 80)
    print(f"Current time: {now}\n")

    # Separate by contest type
    showdown_contests = []
    classic_contests = []

    for contest in contests_response.contests:
        # Filter out WNBA
        if 'WNBA' in contest.name:
            continue

        contest_type = detect_contest_type(contest.name)
        total_entries = contest.entries_details.total if hasattr(contest, 'entries_details') else 0
        contest_info = {
            'id': contest.contest_id,
            'name': contest.name,
            'type': contest_type.value,
            'draft_group': contest.draft_group_id,
            'starts_at': contest.starts_at,
            'guaranteed': contest.is_guaranteed,
            'prize_pool': contest.payout,
            'total_entries': total_entries
        }

        if contest_type.value == "showdown":
            showdown_contests.append(contest_info)
        else:
            classic_contests.append(contest_info)

    # Sort by total entries (most entries first), then by start time
    showdown_contests.sort(key=lambda x: (-x['total_entries'], x['starts_at']))
    classic_contests.sort(key=lambda x: (-x['total_entries'], x['starts_at']))

    # Limit to 100 contests each
    showdown_contests = showdown_contests[:100]
    classic_contests = classic_contests[:100]

    # Display Showdown contests
    if showdown_contests:
        print("SHOWDOWN CONTESTS (Top 100 by entries):")
        print("-" * 80)
        for i, contest in enumerate(showdown_contests[:10], 1):
            time_until = (contest['starts_at'] - now).total_seconds() / 3600  # hours
            print(f"{i}. {contest['name']}")
            print(f"   ID: {contest['id']} | Draft Group: {contest['draft_group']}")
            print(f"   Starts: {contest['starts_at']} (in {time_until:.1f} hours)")
            print(f"   Entries: {contest['total_entries']:,}" if contest['total_entries'] > 0 else "   Entries: N/A")
            print(f"   Prize Pool: ${contest['prize_pool']:,.0f}" if contest['prize_pool'] else "   Prize Pool: N/A")
            print(f"   Guaranteed: {contest['guaranteed']}")
            print()
    else:
        print("No Showdown contests found.")

    # Display Classic contests
    if classic_contests:
        print("\nCLASSIC CONTESTS (Top 100 by entries):")
        print("-" * 80)
        for i, contest in enumerate(classic_contests[:10], 1):
            time_until = (contest['starts_at'] - now).total_seconds() / 3600  # hours
            print(f"{i}. {contest['name']}")
            print(f"   ID: {contest['id']} | Draft Group: {contest['draft_group']}")
            print(f"   Starts: {contest['starts_at']} (in {time_until:.1f} hours)")
            print(f"   Entries: {contest['total_entries']:,}" if contest['total_entries'] > 0 else "   Entries: N/A")
            print(f"   Prize Pool: ${contest['prize_pool']:,.0f}" if contest['prize_pool'] else "   Prize Pool: N/A")
            print(f"   Guaranteed: {contest['guaranteed']}")
            print()
    else:
        print("\nNo Classic contests found.")

    print("=" * 80)
    print(f"TOTAL: {len(showdown_contests)} showdown, {len(classic_contests)} classic (limited to 100 each)")
    print("=" * 80)

    return showdown_contests, classic_contests


if __name__ == "__main__":
    showdown, classic = list_upcoming_nba_contests()

    # Return the first showdown contest for use in other scripts
    if showdown:
        print(f"\nFirst showdown contest ID: {showdown[0]['id']}")
        print(f"First showdown draft group: {showdown[0]['draft_group']}")