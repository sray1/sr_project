"""
DraftKings NFL contest & draftables fetching.

Best-effort fetch layer over the unofficial `draft_kings` client:
- list NFL contests (with type detection: Showdown vs Classic)
- auto-select a showdown contest or the Sunday main-slate classic contest
- fetch draftables for a draft group, normalized into plain player dicts
"""

from datetime import datetime, timezone, timedelta

import requests

from draft_kings import Sport
from contest_detector import ContestType, detect_contest_type, is_main_slate, get_contest_info, display_contest_info
from utils import get_draftkings_client

ET = timezone(timedelta(hours=-5))

DK_DRAFTABLES_URL = ("https://api.draftkings.com/draftgroups/v1/"
                     "draftgroups/{draft_group_id}/draftables")


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


def _client_fallback_draftables(draft_group_id):
    """Fetch draftables via the `draft_kings` client library.

    Fallback only — the raw endpoint carries DK's injury `status` field
    (OUT/IR/Q/D), which the client library drops. Used when the raw
    endpoint fails outright, so the pool is at worst degraded (no
    early OUT/IR flags), never empty.
    """
    try:
        response = get_draftkings_client().draftables(
            draft_group_id=draft_group_id)
    except Exception as e:
        print(f"  Client-library draftables fetch failed: {e}")
        return []

    normalized = []
    for player in (response.players or []):
        competition = player.competition_details
        normalized.append({
            'player_id': player.player_id,
            'name': player.name_details.display,
            'position': player.position_name,
            'positions': (player.position_name or '').split('/'),
            'salary': int(player.salary) if player.salary else None,
            'team': (player.team_details.abbreviation
                     if player.team_details else None),
            'game': competition.name if competition else None,
            'game_start': competition.starts_at if competition else None,
            'is_disabled': bool(player.is_disabled),
            'status': '',
        })
    return normalized


def fetch_draftables(draft_group_id):
    """Fetch and normalize draftable players for a draft group.

    Fetched from the raw draftables endpoint, not the `draft_kings`
    client: the client drops DK's own injury `status` field (OUT/IR/Q/D),
    which flags IR and OUT players days before `isDisabled` flips
    (~90 min pre-lock) — Jordyn Tyson sat in the pool at $5,100 with DK's
    payload already saying 'IR'.

    DK showdown slates list each player twice (CPT at 1.5x salary, base
    UTIL/FLEX entry); classic slates list each player once per position slot.
    Normalization here keeps raw entries; dedup happens in player_builder.

    Args:
        draft_group_id: DK draft group ID

    Returns:
        List of player dicts:
            {player_id, name, position, positions, salary, team, game,
             game_start, is_disabled, status}
            status: 'OUT'/'IR'/'Q'/'D' or '' (DK reports 'None')
    """
    url = DK_DRAFTABLES_URL.format(draft_group_id=draft_group_id)
    # DK added Akamai UA filtering on this endpoint (mid-Sept 2026): a
    # spoofed browser UA ('Mozilla/5.0') is 403-blocked endpoint-wide while
    # requests' honest default UA ('python-requests/x') passes — send no
    # User-Agent at all.
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        entries = response.json().get('draftables') or []
    except (requests.RequestException, ValueError) as e:
        print(f"  Raw draftables fetch failed: {e}")
        print("  Falling back to the draft_kings client "
              "(injury status unavailable)")
        return _client_fallback_draftables(draft_group_id)

    normalized = []
    for entry in entries:
        competition = (entry.get('competition')
                       or (entry.get('competitions') or [{}])[0])

        game_start = None
        raw_start = competition.get('startTime')
        if raw_start:
            try:
                game_start = datetime.fromisoformat(
                    raw_start.replace('Z', '+00:00'))
            except ValueError:
                pass

        status = (entry.get('status') or '').strip().upper()
        if status == 'NONE':
            status = ''

        normalized.append({
            'player_id': entry.get('playerId'),
            'name': entry.get('displayName'),
            'position': entry.get('position'),
            'positions': (entry.get('position') or '').split('/'),
            'salary': entry.get('salary'),
            'team': entry.get('teamAbbreviation'),
            'game': competition.get('name'),
            'game_start': game_start,
            'is_disabled': bool(entry.get('isDisabled')),
            'status': status,
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