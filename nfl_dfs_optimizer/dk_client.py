"""
DraftKings NFL contest & draftables fetching.

Best-effort fetch layer over the unofficial `draft_kings` client:
- list NFL contests (with type detection: Showdown vs Classic)
- auto-select a showdown contest or the Sunday main-slate classic contest
- fetch draftables for a draft group, normalized into plain player dicts
"""

from datetime import datetime, timezone, timedelta

from draft_kings import Sport
from contest_detector import ContestType, detect_contest_type, is_main_slate, get_contest_info, display_contest_info
from utils import get_draftkings_client

ET = timezone(timedelta(hours=-5))


def fetch_nfl_contests():
    """Fetch all upcoming NFL contests from DraftKings.

    Returns:
        List of ContestDetails objects with .contest_id, .draft_group_id,
        .name, .entries_details.total, .starts_at, .payout, .is_guaranteed
    """
    client = get_draftkings_client()
    response = client.contests(Sport.NFL)
    return response.contests or []


def list_nfl_contests(max_show=20, contest_filter=None):
    """Print upcoming NFL contests grouped by type.

    Args:
        max_show: Max contests to print per type
        contest_filter: Optional callable(contest) -> bool to prefilter

    Returns:
        (showdown_contests, classic_contests) lists, sorted by entries desc
    """
    contests = fetch_nfl_contests()
    if contest_filter:
        contests = [c for c in contests if contest_filter(c)]

    showdowns = []
    classics = []
    for contest in contests:
        if contest.name is None:
            continue
        if detect_contest_type(contest.name) == ContestType.SHOWDOWN:
            showdowns.append(contest)
        else:
            classics.append(contest)

    # Sort by total entries (popularity) descending
    showdowns.sort(key=lambda c: (c.entries_details.total or 0), reverse=True)
    classics.sort(key=lambda c: (c.entries_details.total or 0), reverse=True)

    print(f"Found {len(contests)} NFL contests "
          f"({len(showdowns)} showdown, {len(classics)} classic)\n")

    print("TOP SHOWDOWN CONTESTS (single game):")
    if not showdowns:
        print("  (none)")
    for i, contest in enumerate(showdowns[:max_show], 1):
        entries = contest.entries_details.total or 0
        print(f"  {i}. [{contest.contest_id}] {contest.name} "
              f"(draft group {contest.draft_group_id}, {entries:,} entries)")

    print("\nTOP CLASSIC CONTESTS (multi-game slates):")
    if not classics:
        print("  (none)")
    for i, contest in enumerate(classics[:max_show], 1):
        entries = contest.entries_details.total or 0
        main = "*" if is_main_slate(contest.name) else " "
        print(f"  {i}. [{contest.contest_id}] {main}{contest.name} "
              f"(draft group {contest.draft_group_id}, {entries:,} entries)")
    print("\n  (* = main-slate name detected)")

    return showdowns, classics


def select_showdown_contest(contests=None):
    """Pick the most popular showdown contest.

    Args:
        contests: Optional pre-fetched contest list

    Returns:
        ContestDetails for the chosen showdown contest, or None
    """
    if contests is None:
        contests = fetch_nfl_contests()

    showdowns = [c for c in contests if c.name and
                 detect_contest_type(c.name) == ContestType.SHOWDOWN]
    showdowns.sort(key=lambda c: (c.entries_details.total or 0), reverse=True)

    return showdowns[0] if showdowns else None


def select_main_slate_contest(contests=None):
    """Pick the Sunday main-slate classic contest.

    Strategy:
    1. Prefer contests with an explicit main-slate/GPP name
    2. Filter to contests starting Sunday (in ET), take the most-entries one

    Args:
        contests: Optional pre-fetched contest list

    Returns:
        ContestDetails for the chosen classic contest, or None
    """
    if contests is None:
        contests = fetch_nfl_contests()

    classics = [c for c in contests if c.name and
                detect_contest_type(c.name) == ContestType.CLASSIC]

    # Prefer explicitly named main-slate / big GPP contests
    named = [c for c in classics if is_main_slate(c.name)]
    pool = named or classics

    # Restrict to contests whose start time is a Sunday (ET)
    sunday_pool = []
    for contest in pool:
        starts = contest.starts_at
        if starts is None:
            continue
        # draft_kings returns tz-aware datetimes; convert to ET
        starts_et = starts.astimezone(ET) if starts.tzinfo else starts
        if starts_et.weekday() == 6:  # Sunday
            sunday_pool.append(contest)

    pool = sunday_pool or pool
    pool.sort(key=lambda c: (c.entries_details.total or 0), reverse=True)

    return pool[0] if pool else None


def fetch_draftables(draft_group_id):
    """Fetch and normalize draftable players for a draft group.

    DK showdown slates list each player twice (CPT at 1.5x salary, base
    UTIL/FLEX entry); classic slates list each player once per position slot.
    Normalization here keeps raw entries; dedup happens in player_builder.

    Args:
        draft_group_id: DK draft group ID

    Returns:
        List of player dicts:
            {player_id, name, position, positions, salary, team, game,
             game_start, is_disabled}
    """
    client = get_draftkings_client()
    response = client.draftables(draft_group_id)
    players = response.players or []

    normalized = []
    for player in players:
        positions = (player.position_name or '').split('/')

        game = None
        game_start = None
        if player.competition_details:
            game = player.competition_details.name
            game_start = player.competition_details.starts_at

        normalized.append({
            'player_id': player.player_id,
            'name': player.name_details.display if player.name_details else None,
            'position': player.position_name,
            'positions': positions,
            'salary': player.salary,
            'team': player.team_details.abbreviation if player.team_details else None,
            'game': game,
            'game_start': game_start,
            'is_disabled': bool(player.is_disabled),
        })

    return normalized


def show_contest_details(contest):
    """Print contest details and rules for a selected contest."""
    info = get_contest_info(contest.contest_id, contest.name)
    display_contest_info(info)
    print(f"Draft Group ID: {contest.draft_group_id}")
    entries = contest.entries_details.total or 0
    print(f"Entries: {entries:,}")
    if contest.payout:
        print(f"Payout: ${contest.payout:,.0f}")
    if contest.starts_at:
        print(f"Starts At: {contest.starts_at}")
    print("=" * 70)


if __name__ == "__main__":
    def _main():
        list_nfl_contests()

    from utils import run_and_save
    run_and_save(_main, prefix='nfl_contests_', output_dir='output')