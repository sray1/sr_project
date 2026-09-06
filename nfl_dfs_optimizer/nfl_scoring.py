"""
DraftKings NFL scoring rules & projection helpers.

Covers offensive player scoring (QB/RB/WR/TE) and DST scoring.
Used by the scoring display and by the fallback projection generator.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NFLScoringRules:
    """DraftKings NFL offensive scoring constants."""

    # Passing
    passing_yards_per_point: float = 25.0      # 1 pt per 25 pass yds
    passing_touchdown: float = 4.0
    interception_thrown: float = -1.0
    passing_yardage_bonus: float = 3.0         # 300+ pass yds game

    # Rushing
    rushing_yards_per_point: float = 10.0     # 1 pt per 10 rush yds
    rushing_touchdown: float = 6.0
    rushing_yardage_bonus: float = 3.0        # 100+ rush yds game

    # Receiving (full PPR)
    reception: float = 1.0
    receiving_yards_per_point: float = 10.0   # 1 pt per 10 rec yds
    receiving_touchdown: float = 6.0
    receiving_yardage_bonus: float = 3.0      # 100+ rec yds game

    # Misc
    fumble_lost: float = -1.0
    two_point_conversion: float = 2.0


@dataclass(frozen=True)
class DSTScoring:
    """DraftKings NFL DST (team defense) scoring constants."""

    sack: float = 1.0
    interception: float = 2.0
    fumble_recovery: float = 2.0
    blocked_kick: float = 2.0
    safety: float = 2.0
    touchdown: float = 6.0
    extra_point_return: float = 2.0
    two_point_conversion_return: float = 2.0

    # Points-allowed tiers: (low, high, points)
    POINTS_ALLOWED_TIERS = [
        (0, 0, 10.0),
        (1, 6, 7.0),
        (7, 13, 4.0),
        (14, 20, 1.0),
        (21, 27, 0.0),
        (28, 34, -1.0),
        (35, None, -4.0),
    ]

    @classmethod
    def points_allowed_bonus(cls, points_allowed: int) -> float:
        """Return DST fantasy points for points allowed in a game."""
        for low, high, pts in cls.POINTS_ALLOWED_TIERS:
            if points_allowed >= low and (high is None or points_allowed <= high):
                return pts
        raise ValueError(f"Invalid points_allowed: {points_allowed}")


def calculate_offensive_points(pass_yards=0, pass_tds=0, interceptions=0,
                               rush_yards=0, rush_tds=0,
                               receptions=0, rec_yards=0, rec_tds=0,
                               fumbles_lost=0, two_pt_conversions=0,
                               rules: NFLScoringRules = None) -> float:
    """Calculate DK fantasy points for an offensive stat line."""
    r = rules or NFLScoringRules()

    points = (
        pass_yards / r.passing_yards_per_point
        + pass_tds * r.passing_touchdown
        + interceptions * r.interception_thrown
        + rush_yards / r.rushing_yards_per_point
        + rush_tds * r.rushing_touchdown
        + receptions * r.reception
        + rec_yards / r.receiving_yards_per_point
        + rec_tds * r.receiving_touchdown
        + fumbles_lost * r.fumble_lost
        + two_pt_conversions * r.two_point_conversion
    )

    # Yardage bonuses
    if pass_yards >= 300:
        points += r.passing_yardage_bonus
    if rush_yards >= 100:
        points += r.rushing_yardage_bonus
    if rec_yards >= 100:
        points += r.receiving_yardage_bonus

    return round(points, 2)


def calculate_dst_points(sacks=0, interceptions=0, fumble_recoveries=0,
                         blocked_kicks=0, safeties=0, touchdowns=0,
                         points_allowed=0, scoring: DSTScoring = None) -> float:
    """Calculate DK fantasy points for a DST stat line."""
    s = scoring or DSTScoring()

    points = (
        sacks * s.sack
        + interceptions * s.interception
        + fumble_recoveries * s.fumble_recovery
        + blocked_kicks * s.blocked_kick
        + safeties * s.safety
        + touchdowns * s.touchdown
    )

    points += DSTScoring.points_allowed_bonus(points_allowed)

    return round(points, 2)


def display_scoring_rules(contest_type="classic"):
    """Display DraftKings NFL scoring rules.

    Args:
        contest_type: "showdown" or "classic"
    """
    print("=" * 70)
    print("DRAFTKINGS NFL SCORING RULES")
    print("=" * 70)

    print("\nOffensive Players (QB/RB/WR/TE):")
    print("  Passing Yards: +1.0 per 25 yards (0.04/yd)")
    print("  Passing Touchdowns: +4.0")
    print("  Interceptions Thrown: -1.0")
    print("  Rushing Yards: +1.0 per 10 yards (0.1/yd)")
    print("  Rushing Touchdowns: +6.0")
    print("  Receptions: +1.0 (PPR)")
    print("  Receiving Yards: +1.0 per 10 yards (0.1/yd)")
    print("  Receiving Touchdowns: +6.0")
    print("  Fumbles Lost: -1.0")
    print("  2-Point Conversions: +2.0")
    print("  300+ Pass Yards / 100+ Rush or Rec Yards: +3.0 bonus")

    print("\nDefense/Special Teams (DST):")
    print("  Sacks: +1.0")
    print("  Interceptions: +2.0")
    print("  Fumble Recoveries: +2.0")
    print("  Blocked Kicks: +2.0")
    print("  Safeties: +2.0")
    print("  DST Touchdowns: +6.0")
    print("  Points Allowed: 0=+10, 1-6=+7, 7-13=+4, 14-20=+1,")
    print("                  21-27=0, 28-34=-1, 35+=-4")

    if contest_type == "showdown":
        print("\nShowdown-Specific Rules:")
        print("  - Roster: 6 players (1 Captain + 5 FLEX)")
        print("  - Captain: 1.5x multiplier on BOTH points AND salary")
        print("  - Salary Cap: $50,000")
        print("  - Max 5 players from one team (captain counts)")
    else:
        print("\nClassic-Specific Rules:")
        print("  - Roster: 9 players (1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX, 1 DST)")
        print("  - Salary Cap: $50,000")

    print("=" * 70)


if __name__ == "__main__":
    display_scoring_rules("classic")
    print()
    display_scoring_rules("showdown")