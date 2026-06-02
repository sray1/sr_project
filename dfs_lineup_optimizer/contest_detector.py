"""
Detect contest type (Classic vs Showdown) and apply appropriate scoring rules.
"""

from enum import Enum
from dataclasses import dataclass


class ContestType(Enum):
    """DraftKings NBA contest types."""
    CLASSIC = "classic"
    SHOWDOWN = "showdown"


@dataclass
class ContestInfo:
    """Contest information including type and scoring rules."""
    contest_id: int
    contest_name: str
    contest_type: ContestType
    salary_cap: int
    roster_spots: int
    captain_multiplier: float = 1.0


def detect_contest_type(contest_name: str) -> ContestType:
    """
    Detect if a contest is Classic or Showdown based on the name.

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


def get_contest_info(contest_id: int, contest_name: str) -> ContestInfo:
    """
    Get complete contest information including type and rules.

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
            roster_spots=6,  # 1 Captain + 5 UTIL
            captain_multiplier=1.5
        )
    else:
        return ContestInfo(
            contest_id=contest_id,
            contest_name=contest_name,
            contest_type=contest_type,
            salary_cap=50000,
            roster_spots=8,  # 8 standard spots for NBA Classic
            captain_multiplier=1.0
        )


def apply_captain_multiplier(base_fppg: float, is_captain: bool = False) -> float:
    """
    Apply captain multiplier to fantasy points for showdown contests.

    Args:
        base_fppg: Base fantasy points projection
        is_captain: Whether the player is in the captain spot

    Returns:
        Adjusted fantasy points (multiplied if captain)
    """
    if is_captain:
        return base_fppg * 1.5
    return base_fppg


def apply_salary_multiplier(base_salary: float, is_captain: bool = False) -> float:
    """
    Apply salary multiplier for captain spot in showdown contests.

    Args:
        base_salary: Base player salary
        is_captain: Whether the player is in the captain spot

    Returns:
        Adjusted salary (multiplied by 1.5 if captain)
    """
    if is_captain:
        return base_salary * 1.5
    return base_salary


def get_roster_requirements(contest_type: ContestType) -> dict:
    """
    Get roster requirements for the contest type.

    Args:
        contest_type: Classic or Showdown

    Returns:
        Dictionary with roster requirements
    """
    if contest_type == ContestType.SHOWDOWN:
        return {
            'total_spots': 6,
            'captain_spots': 1,
            'util_spots': 5,
            'positions': ['CPT', 'UTIL', 'UTIL', 'UTIL', 'UTIL', 'UTIL'],
            'salary_cap': 50000
        }
    else:
        return {
            'total_spots': 8,
            'positions': ['PG', 'PG', 'SG', 'SG', 'SF', 'SF', 'PF', 'PF', 'C'],
            'salary_cap': 50000,
            'flex_spots': True
        }


def calculate_lineup_score(players: list, contest_type: ContestType, captain_index: int = 0) -> float:
    """
    Calculate total lineup score accounting for captain multiplier.

    Args:
        players: List of player objects with fppg attribute
        contest_type: Classic or Showdown
        captain_index: Index of captain player (only used for showdown)

    Returns:
        Total fantasy points for the lineup
    """
    if contest_type == ContestType.SHOWDOWN:
        total = 0
        for i, player in enumerate(players):
            if i == captain_index:
                total += player.fppg * 1.5
            else:
                total += player.fppg
        return total
    else:
        return sum(player.fppg for player in players)


def display_contest_info(contest_info: ContestInfo):
    """Display contest information and rules."""
    print("=" * 70)
    print(f"CONTEST TYPE: {contest_info.contest_type.value.upper()}")
    print("=" * 70)
    print(f"Name: {contest_info.contest_name}")
    print(f"ID: {contest_info.contest_id}")

    if contest_info.contest_type == ContestType.SHOWDOWN:
        print(f"\nShowdown Rules:")
        print(f"  - Roster: 6 players (1 Captain + 5 UTIL)")
        print(f"  - Captain Multiplier: 1.5x on points AND salary")
        print(f"  - Salary Cap: ${contest_info.salary_cap:,}")
        print(f"  - Captain spot earns 1.5x fantasy points")
        print(f"  - Captain salary also multiplied by 1.5x")
    else:
        print(f"\nClassic Rules:")
        print(f"  - Roster: 8 players (PG/SG/SF/PF/C positions)")
        print(f"  - Salary Cap: ${contest_info.salary_cap:,}")
        print(f"  - Standard DK scoring rules apply")
        print(f"  - No captain multiplier")

    print("=" * 70)


# Examples
if __name__ == "__main__":
    # Test contest detection
    test_contests = [
        (123, "NBA Showdown $1M Finals Tip-Off Special"),
        (456, "NBA Classic $200K Tournament"),
        (789, "NBA Single Game $50K"),
        (999, "NBA GPP $100K"),
    ]

    print("Contest Type Detection Examples:\n")
    for contest_id, contest_name in test_contests:
        info = get_contest_info(contest_id, contest_name)
        print(f"{contest_name:40} -> {info.contest_type.value}")

    # Display full info for a showdown contest
    print("\n" + "=" * 70)
    showdown_info = get_contest_info(190875116, "NBA Showdown $1M Finals Tip-Off Special")
    display_contest_info(showdown_info)

    # Test captain multiplier calculations
    print("\nCaptain Multiplier Examples:")
    print(f"  50 fppg player as UTIL: {apply_captain_multiplier(50, False)} fppg")
    print(f"  50 fppg player as Captain: {apply_captain_multiplier(50, True)} fppg")
    print(f"  $10,000 salary as UTIL: ${apply_salary_multiplier(10000, False):,.0f}")
    print(f"  $10,000 salary as Captain: ${apply_salary_multiplier(10000, True):,.0f}")