"""
Analyze the next NBA Showdown contest with projections.
"""

from draft_kings import Client, Sport
from projections import ProjectionManager
from datetime import datetime, timezone


def get_next_nba_showdown():
    """Get the next NBA Showdown contest."""
    client = Client()
    contests_response = client.contests(Sport.NBA)

    # Filter for showdown contests and find next one
    showdown_contests = []
    now = datetime.now(timezone.utc)

    for contest in contests_response.contests:
        # Look for NBA showdown (not WNBA)
        if 'Showdown' in contest.name and contest.starts_at > now and 'WNBA' not in contest.name:
            showdown_contests.append((contest, contest.starts_at))

    # Sort by start time
    showdown_contests.sort(key=lambda x: x[1])

    if not showdown_contests:
        return None, None

    return showdown_contests[0]  # Return (contest, start_time)


def analyze_showdown(contest):
    """Analyze a showdown contest with projections."""
    draft_group_id = contest.draft_group_id

    print("=" * 70)
    print(f"ANALYZING NEXT NBA SHOWDOWN CONTEST")
    print("=" * 70)
    print(f"\nContest Details:")
    print(f"  ID: {contest.contest_id}")
    print(f"  Name: {contest.name}")
    print(f"  Draft Group: {draft_group_id}")
    print(f"  Starts At: {contest.starts_at}")
    print(f"  Prize Pool: ${contest.payout:,.0f}" if contest.payout else "  Prize Pool: N/A")
    print(f"  Guaranteed: {contest.is_guaranteed}")

    print(f"\n{'=' * 70}")

    # Create projections
    print("\nGenerating projections...")
    manager = ProjectionManager()
    projections_df = manager.create_sample_projections(draft_group_id)

    print(f"Found {len(projections_df)} players in this showdown\n")

    # Merge and analyze
    merged_df = manager.merge_with_draftables(draft_group_id)

    # Show top projected players
    print(f"\n{'=' * 70}")
    print("TOP 10 PROJECTED PLAYERS (by fantasy points per game)")
    print(f"{'=' * 70}")
    top_points = merged_df.nlargest(10, 'projected_points')
    print(top_points[['player_name', 'position', 'team', 'salary', 'projected_points', 'projected_value']].to_string(index=False))

    # Show top value plays
    print(f"\n{'=' * 70}")
    print("TOP 10 VALUE PLAYS (by points per $1k salary)")
    print(f"{'=' * 70}")
    top_values = manager.get_top_values(draft_group_id, top_n=10)
    print(top_values[['player_name', 'position', 'team', 'salary', 'projected_points', 'projected_value']].to_string(index=False))

    # Show by position
    print(f"\n{'=' * 70}")
    print("TOP PLAYS BY POSITION")
    print(f"{'=' * 70}")

    positions = merged_df['position'].unique()
    for pos in sorted(positions):
        pos_df = merged_df[merged_df['position'] == pos]
        if len(pos_df) > 0:
            top = pos_df.nlargest(3, 'projected_points')
            print(f"\n{pos}:")
            for _, row in top.iterrows():
                print(f"  {row['player_name']:<20} {row['team']:>4}  ${row['salary']:>7,.0f}  {row['projected_points']:>5.1f} fppg  ({row['projected_value']:.2f} val)")

    # Show team analysis
    print(f"\n{'=' * 70}")
    print("TEAM ANALYSIS")
    print(f"{'=' * 70}")

    teams = merged_df['team'].unique()
    team_stats = {}
    for team in teams:
        team_df = merged_df[merged_df['team'] == team]
        team_stats[team] = {
            'players': len(team_df),
            'total_salary': team_df['salary'].sum(),
            'avg_projection': team_df['projected_points'].mean(),
            'top_projection': team_df['projected_points'].max()
        }

    for team, stats in sorted(team_stats.items(), key=lambda x: x[1]['total_salary'], reverse=True):
        print(f"\n{team}:")
        print(f"  Players: {stats['players']}")
        print(f"  Total Salary: ${stats['total_salary']:,.0f}")
        print(f"  Avg Projection: {stats['avg_projection']:.1f}")
        print(f"  Top Projection: {stats['top_projection']:.1f}")

    print(f"\n{'=' * 70}")
    print("ANALYSIS COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    contest, start_time = get_next_nba_showdown()

    if not contest:
        print("No upcoming NBA Showdown contests found.")
    else:
        print(f"Found next NBA Showdown starting at {start_time}")
        analyze_showdown(contest)