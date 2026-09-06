"""
Detect contest type (Classic vs Showdown) for DraftKings NFL contests.

Adapted from dfs_lineup_optimizer/contest_detector.py with an added
MAIN_SLATE classifier used to auto-select the Sunday classic slate.
"""

from enum import Enum
from dataclasses import dataclass


class ContestType(Enum):
    """DraftKings NFL contest types."""
    CLASSIC = "classic"
    SHOWDOWN = "showdown"


@dataclass
class ContestInfo:
    """Contest information including type and rules."""
    contest_id: int
    contest_name: str
    contest_type: ContestType
    salary_cap: int
    roster_spots: int
    captain_multiplier: float = 1.0

    # Classic roster: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX, 1 DST
    CLASSIC_POSITIONS = {
        'QB': 1, 'RB': 2, 'WR': 3, 'TE': 1, 'FLEX': 1, 'DST': 1
    }


def detect_contest_type(contest_name: str) -> ContestType:
    """Detect if a contest is Classic or Showdown based on the name.

    Args:
        contest_name: The contest name from DraftKings

    Returns:
        ContestType enum indicating contest type
    """
    name_lower = contest_name.lower()

    # Check for showdown indicators
    showdown_keywords = ['showdown', 'single game', 'sgp', 'captain', 'mvp']

    for keyword in showdown_keywords:
        if keyword in name_lower:
            return ContestType.SHOWDOWN

    # Default to classic
    return ContestType.CLASSIC


def is_main_slate(contest_name: str) -> bool:
    """Check if a classic contest name refers to the main (Sunday) slate.

    DK names main-slate contests like "NFL $5M Fantasy Football Millionaire
    Maker ($20 entry)" or with "Main Slate" in the name. Tournaments and
    3-max/flags also run on the main slate; single-game and Thursday/Monday
    slates are excluded by the caller via start-time filtering.

    Args:
        contest_name: The contest name from DraftKings

    Returns:
        True if the name suggests the main slate
    """
    name_lower = contest_name.lower()

    # Explicit label
    if 'main slate' in name_lower:
        return True

    # Big GPPs run on the main slate
    tournament_markers = ['millionaire', 'tournament', 'gpp', 'grand jam']
    if any(marker in name_lower for marker in tournament_markers):
        return True

    return False


def get_contest_info(contest_id: int, contest_name: str) -> ContestInfo:
    """Get complete contest information including type and rules.

    Args:
        contest_id: DraftKings contest ID
        contest_name: Contest name from DraftKings

    Returns:
        ContestInfo with contest type and relevant rules
    """
    contest_type = detect_contest_type(contest_name)

    if contest_type == ContestType.SHOWDOWN:
        return ContestInfo(
            contest_id=contest_id,
            contest_name=contest_name,
            contest_type=contest_type,
            salary_cap=50000,
            roster_spots=6,  # 1 Captain + 5 FLEX
            captain_multiplier=1.5
        )
    else:
        return ContestInfo(
            contest_id=contest_id,
            contest_name=contest_name,
            contest_type=contest_type,
            salary_cap=50000,
            roster_spots=9,  # QB + 2RB + 3WR + TE + FLEX + DST
            captain_multiplier=1.0
        )


def display_contest_info(contest_info: ContestInfo):
    """Display contest information and rules."""
    print("=" * 70)
    print(f"CONTEST TYPE: {contest_info.contest_type.value.upper()}")
    print("=" * 70)
    print(f"Name: {contest_info.contest_name}")
    print(f"ID: {contest_info.contest_id}")

    if contest_info.contest_type == ContestType.SHOWDOWN:
        print(f"\nShowdown Rules:")
        print(f"  - Roster: 6 players (1 Captain + 5 FLEX)")
        print(f"  - Captain Multiplier: 1.5x on points AND salary")
        print(f"  - Salary Cap: ${contest_info.salary_cap:,}")
        print(f"  - Max 5 players from one team (captain counts)")
    else:
        print(f"\nClassic Rules:")
        print(f"  - Roster: 9 players (1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX, 1 DST)")
        print(f"  - Salary Cap: ${contest_info.salary_cap:,}")
        print(f"  - Standard DK NFL scoring rules apply")
        print(f"  - No captain multiplier")

    print("=" * 70)


if __name__ == "__main__":
    # Test contest detection
    test_contests = [
        (123, "NFL Showdown $100K Thursday Night Kickoff [1 Game]"),
        (456, "NFL $5M Fantasy Football Millionaire Maker ($20) [Main Slate]"),
        (789, "NFL Single Game $50K [MVP]"),
        (999, "NFL $500K Fantasy Football World [All Games]"),
    ]

    print("Contest Type Detection Examples:\n")
    for contest_id, contest_name in test_contests:
        info = get_contest_info(contest_id, contest_name)
        main = is_main_slate(contest_name)
        print(f"{contest_name:55} -> {info.contest_type.value:9} main_slate={main}")