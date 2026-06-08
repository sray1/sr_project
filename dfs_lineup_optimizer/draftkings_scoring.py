"""
DraftKings NBA scoring rules and projection calculator.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class DKScoringRules:
    """DraftKings NBA scoring configuration."""
    points: float = 1.0
    rebounds: float = 1.25
    assists: float = 1.5
    steals: float = 2.0
    blocks: float = 2.0
    turnovers: float = -0.5
    three_pointers: float = 0.5
    double_double_bonus: float = 1.5
    triple_double_bonus: float = 3.0


@dataclass
class PlayerStats:
    """NBA player statistics for fantasy scoring."""
    points: float = 0.0
    rebounds: float = 0.0
    assists: float = 0.0
    steals: float = 0.0
    blocks: float = 0.0
    turnovers: float = 0.0
    three_pointers: float = 0.0


class DKScoringCalculator:
    """Calculate fantasy points based on DraftKings NBA scoring rules."""

    def __init__(self):
        self.rules = DKScoringRules()

    def calculate_fantasy_points(self, stats: PlayerStats) -> float:
        """Calculate total fantasy points from player statistics."""
        base_points = (
            stats.points * self.rules.points +
            stats.rebounds * self.rules.rebounds +
            stats.assists * self.rules.assists +
            stats.steals * self.rules.steals +
            stats.blocks * self.rules.blocks +
            stats.turnovers * self.rules.turnovers +
            stats.three_pointers * self.rules.three_pointers
        )

        # Add bonus points for double-doubles and triple-doubles
        bonus = self._calculate_milestone_bonuses(stats)

        return base_points + bonus

    def _calculate_milestone_bonuses(self, stats: PlayerStats) -> float:
        """Calculate bonus points for double-doubles and triple-doubles."""
        # Count categories with double digits
        double_digit_categories = 0

        if stats.points >= 10:
            double_digit_categories += 1
        if stats.rebounds >= 10:
            double_digit_categories += 1
        if stats.assists >= 10:
            double_digit_categories += 1
        if stats.steals >= 10:
            double_digit_categories += 1
        if stats.blocks >= 10:
            double_digit_categories += 1

        # Triple-double bonus overrides double-double
        if double_digit_categories >= 3:
            return self.rules.triple_double_bonus
        elif double_digit_categories >= 2:
            return self.rules.double_double_bonus

        return 0.0

    def calculate_from_stat_line(self, points: int, rebounds: int, assists: int,
                                  steals: int, blocks: int, turnovers: int,
                                  three_pointers: int) -> float:
        """Calculate fantasy points from a stat line."""
        stats = PlayerStats(
            points=points,
            rebounds=rebounds,
            assists=assists,
            steals=steals,
            blocks=blocks,
            turnovers=turnovers,
            three_pointers=three_pointers
        )
        return self.calculate_fantasy_points(stats)


# Example realistic stat lines from Daily Fantasy Fuel data
REALISTIC_STAT_LINES = {
    # San Antonio Spurs
    '1373356': PlayerStats(points=28, rebounds=12, assists=4, steals=1, blocks=2,
                           turnovers=3, three_pointers=2),  # Victor Wembanyama
    '1461118': PlayerStats(points=18, rebounds=5, assists=6, steals=2, blocks=0,
                           turnovers=2, three_pointers=2),  # Stephon Castle
    'player_fox': PlayerStats(points=22, rebounds=4, assists=8, steals=1, blocks=0,
                               turnovers=4, three_pointers=2),  # De'Aaron Fox
    'player_vassell': PlayerStats(points=16, rebounds=6, assists=3, steals=1, blocks=1,
                                   turnovers=2, three_pointers=3),  # Devin Vassell
    'player_champagnie': PlayerStats(points=14, rebounds=8, assists=2, steals=1, blocks=0,
                                     turnovers=1, three_pointers=2),  # Julian Champagnie
    'player_harper': PlayerStats(points=12, rebounds=4, assists=5, steals=1, blocks=0,
                                 turnovers=2, three_pointers=2),  # Dylan Harper
    'player_johnson': PlayerStats(points=10, rebounds=5, assists=2, steals=1, blocks=0,
                                   turnovers=1, three_pointers=1),  # Keldon Johnson
    'player_kornet': PlayerStats(points=6, rebounds=5, assists=1, steals=0, blocks=1,
                                 turnovers=1, three_pointers=0),  # Luke Kornet
    'player_carter': PlayerStats(points=5, rebounds=3, assists=1, steals=1, blocks=0,
                                 turnovers=1, three_pointers=1),  # Carter Bryant
    'player_mclaughlin': PlayerStats(points=4, rebounds=2, assists=5, steals=1, blocks=0,
                                     turnovers=1, three_pointers=0),  # Jordan McLaughlin

    # New York Knicks
    '887914': PlayerStats(points=26, rebounds=5, assists=9, steals=1, blocks=0,
                          turnovers=4, three_pointers=3),  # Jalen Brunson
    '837030': PlayerStats(points=24, rebounds=11, assists=3, steals=1, blocks=1,
                          turnovers=3, three_pointers=2),  # Karl-Anthony Towns
    'player_anunoby': PlayerStats(points=18, rebounds=7, assists=2, steals=2, blocks=1,
                                   turnovers=2, three_pointers=2),  # OG Anunoby
    'player_hart': PlayerStats(points=16, rebounds=9, assists=5, steals=2, blocks=0,
                               turnovers=2, three_pointers=1),  # Josh Hart
    'player_bridges': PlayerStats(points=16, rebounds=6, assists=3, steals=1, blocks=0,
                                  turnovers=2, three_pointers=3),  # Mikal Bridges
    'player_mcbride': PlayerStats(points=8, rebounds=3, assists=4, steals=1, blocks=0,
                                  turnovers=2, three_pointers=1),  # Miles McBride
    'player_shamet': PlayerStats(points=10, rebounds=2, assists=2, steals=0, blocks=0,
                                  turnovers=1, three_pointers=2),  # Landry Shamet
    'player_robinson': PlayerStats(points=6, rebounds=8, assists=1, steals=1, blocks=2,
                                   turnovers=1, three_pointers=0),  # Mitchell Robinson
    'player_alvarado': PlayerStats(points=6, rebounds=2, assists=5, steals=2, blocks=0,
                                   turnovers=2, three_pointers=0),  # Jose Alvarado
    'player_clarkson': PlayerStats(points=8, rebounds=2, assists=2, steals=0, blocks=0,
                                   turnovers=1, three_pointers=2),  # Jordan Clarkson
}

# Module-level override for custom stat lines (set at runtime)
_custom_stat_lines: Dict[str, PlayerStats] = {}


def set_stat_lines(new_stat_lines: Dict[str, PlayerStats]):
    """Set custom stat lines to override DEFAULT_STAT_LINES at runtime.

    Args:
        new_stat_lines: Dict mapping player_id/name -> PlayerStats
    """
    global _custom_stat_lines
    _custom_stat_lines = new_stat_lines


def get_active_stat_lines() -> Dict[str, PlayerStats]:
    """Get the active stat lines, preferring custom overrides over defaults.

    Returns:
        Dict mapping player_id/name -> PlayerStats
    """
    if _custom_stat_lines:
        return _custom_stat_lines
    return REALISTIC_STAT_LINES


def generate_projections_from_salary(salary: float, positions: list = None) -> PlayerStats:
    """Generate a projected stat line from salary using position-aware scaling.

    Creates a realistic stat distribution based on player salary (which
    correlates with usage and minutes) and position.

    Args:
        salary: DK salary (e.g., 10000)
        positions: List of positions (e.g., ['PG', 'SG'])

    Returns:
        PlayerStats with projected stats
    """
    # Base fppg from salary (roughly salary / 1000 * 2.5 for starters)
    # Higher salary = higher usage and more minutes
    base_fppg = salary / 1000 * 2.5

    # Position-based distribution adjustments
    if positions is None:
        positions = []

    pos_str = '/'.join(positions)

    # Default balanced distribution
    dist = {'points': 0.4, 'rebounds': 0.15, 'assists': 0.15,
            'steals': 0.05, 'blocks': 0.03, 'turnovers': -0.10}

    # Adjust by primary position
    if 'PG' in positions:
        dist = {'points': 0.30, 'rebounds': 0.10, 'assists': 0.25,
                'steals': 0.06, 'blocks': 0.01, 'turnovers': -0.12}
    elif 'SG' in positions:
        dist = {'points': 0.40, 'rebounds': 0.10, 'assists': 0.12,
                'steals': 0.06, 'blocks': 0.01, 'turnovers': -0.10}
    elif 'SF' in positions:
        dist = {'points': 0.30, 'rebounds': 0.20, 'assists': 0.10,
                'steals': 0.06, 'blocks': 0.03, 'turnovers': -0.08}
    elif 'PF' in positions:
        dist = {'points': 0.25, 'rebounds': 0.25, 'assists': 0.08,
                'steals': 0.04, 'blocks': 0.06, 'turnovers': -0.08}
    elif 'C' in positions:
        dist = {'points': 0.20, 'rebounds': 0.30, 'assists': 0.05,
                'steals': 0.03, 'blocks': 0.10, 'turnovers': -0.06}

    # Star player bonus (salary >= 8000 means higher usage)
    if salary >= 10000:
        dist['points'] += 0.05
        dist['assists'] += 0.02
    elif salary >= 8000:
        dist['points'] += 0.03

    # Build stat line from fppg and distribution
    rules = DKScoringRules()
    projected_points = base_fppg * dist['points'] / rules.points
    projected_rebounds = max(0, base_fppg * dist['rebounds'] / rules.rebounds)
    projected_assists = max(0, base_fppg * dist['assists'] / rules.assists)
    projected_steals = max(0, base_fppg * dist['steals'] / rules.steals)
    projected_blocks = max(0, base_fppg * dist['blocks'] / rules.blocks)
    projected_turnovers = max(0, base_fppg * abs(dist['turnovers']) / abs(rules.turnovers))
    projected_3pt = max(0, projected_points * 0.25 / 3)  # ~25% of points from 3s

    return PlayerStats(
        points=round(projected_points, 1),
        rebounds=round(projected_rebounds, 1),
        assists=round(projected_assists, 1),
        steals=round(projected_steals, 1),
        blocks=round(projected_blocks, 1),
        turnovers=round(projected_turnovers, 1),
        three_pointers=round(projected_3pt, 1),
    )


def generate_projections_from_rotation(player_name: str, team_abbr: str,
                                        salary: float, positions: list = None) -> PlayerStats:
    """Generate projected stats using rotation data for better accuracy.

    Combines rotation role (starter/rotation/bench) and estimated minutes
    with salary to produce more realistic projections.

    Args:
        player_name: Full player name (e.g., 'Jalen Brunson')
        team_abbr: Team abbreviation (e.g., 'NYK')
        salary: DK salary
        positions: List of positions (e.g., ['PG'])

    Returns:
        PlayerStats with projected stats
    """
    from nba_rotations import get_rotation_status, get_estimated_minutes

    role = get_rotation_status(player_name, team_abbr)
    minutes = get_estimated_minutes(player_name, team_abbr, salary=salary)

    # Base fppg scales with minutes played
    # Average starter: ~33 min → ~30 fppg, rotation: ~21 min → ~18 fppg, bench: ~8 min → ~6 fppg
    if role == 'starter':
        base_fppg = minutes * 0.90  # starters produce ~0.9 fppg per minute
    elif role == 'rotation':
        base_fppg = minutes * 0.85  # rotation players slightly less efficient
    else:
        base_fppg = minutes * 0.75  # bench players much less efficient

    # Adjust with salary signal (higher salary = better per-minute production)
    if salary >= 10000:
        base_fppg *= 1.15
    elif salary >= 8000:
        base_fppg *= 1.05
    elif salary < 4000:
        base_fppg *= 0.90

    # Position-based distribution
    if positions is None:
        positions = []

    if 'PG' in positions:
        dist = {'points': 0.30, 'rebounds': 0.10, 'assists': 0.25,
                'steals': 0.06, 'blocks': 0.01, 'turnovers': -0.12}
    elif 'SG' in positions:
        dist = {'points': 0.40, 'rebounds': 0.10, 'assists': 0.12,
                'steals': 0.06, 'blocks': 0.01, 'turnovers': -0.10}
    elif 'SF' in positions:
        dist = {'points': 0.30, 'rebounds': 0.20, 'assists': 0.10,
                'steals': 0.06, 'blocks': 0.03, 'turnovers': -0.08}
    elif 'PF' in positions:
        dist = {'points': 0.25, 'rebounds': 0.25, 'assists': 0.08,
                'steals': 0.04, 'blocks': 0.06, 'turnovers': -0.08}
    elif 'C' in positions:
        dist = {'points': 0.20, 'rebounds': 0.30, 'assists': 0.05,
                'steals': 0.03, 'blocks': 0.10, 'turnovers': -0.06}
    else:
        dist = {'points': 0.25, 'rebounds': 0.25, 'assists': 0.20,
                'steals': 0.08, 'blocks': 0.07, 'turnovers': -0.10}

    rules = DKScoringRules()
    stats = PlayerStats(
        points=round(base_fppg * dist['points'] / rules.points, 1),
        rebounds=round(base_fppg * dist['rebounds'] / rules.rebounds, 1),
        assists=round(base_fppg * dist['assists'] / rules.assists, 1),
        steals=round(base_fppg * dist['steals'] / rules.steals, 1),
        blocks=round(base_fppg * dist['blocks'] / rules.blocks, 1),
        turnovers=round(base_fppg * abs(dist['turnovers']) / abs(rules.turnovers), 1),
        three_pointers=round(max(0, base_fppg * dist['points'] / rules.points * 0.25 / 3), 1),
    )

    return stats


def display_scoring_breakdown(player_id: str = '1373356'):
    """Display scoring breakdown for a sample player."""
    calculator = DKScoringCalculator()
    stats = REALISTIC_STAT_LINES.get(player_id, REALISTIC_STAT_LINES['1373356'])

    print("=" * 70)
    print("DRAFTKINGS NBA SCORING BREAKDOWN")
    print("=" * 70)
    print(f"\nPlayer Statistics:")
    print(f"  Points: {stats.points}")
    print(f"  Rebounds: {stats.rebounds}")
    print(f"  Assists: {stats.assists}")
    print(f"  Steals: {stats.steals}")
    print(f"  Blocks: {stats.blocks}")
    print(f"  Turnovers: {stats.turnovers}")
    print(f"  3-Pointers: {stats.three_pointers}")

    print(f"\nScoring Breakdown:")
    print(f"  Points: {stats.points} × {calculator.rules.points} = {stats.points * calculator.rules.points:.1f}")
    print(f"  Rebounds: {stats.rebounds} × {calculator.rules.rebounds} = {stats.rebounds * calculator.rules.rebounds:.1f}")
    print(f"  Assists: {stats.assists} × {calculator.rules.assists} = {stats.assists * calculator.rules.assists:.1f}")
    print(f"  Steals: {stats.steals} × {calculator.rules.steals} = {stats.steals * calculator.rules.steals:.1f}")
    print(f"  Blocks: {stats.blocks} × {calculator.rules.blocks} = {stats.blocks * calculator.rules.blocks:.1f}")
    print(f"  Turnovers: {stats.turnovers} × {calculator.rules.turnovers} = {stats.turnovers * calculator.rules.turnovers:.1f}")
    print(f"  3-Pointers: {stats.three_pointers} × {calculator.rules.three_pointers} = {stats.three_pointers * calculator.rules.three_pointers:.1f}")

    # Check for milestones
    double_digit_cats = sum([
        stats.points >= 10,
        stats.rebounds >= 10,
        stats.assists >= 10,
        stats.steals >= 10,
        stats.blocks >= 10
    ])

    bonus = 0
    if double_digit_cats >= 3:
        bonus = calculator.rules.triple_double_bonus
        print(f"  Triple-Double Bonus: +{bonus}")
    elif double_digit_cats >= 2:
        bonus = calculator.rules.double_double_bonus
        print(f"  Double-Double Bonus: +{bonus}")

    total = calculator.calculate_fantasy_points(stats)
    print(f"\nTotal Fantasy Points: {total:.1f}")
    print("=" * 70)


if __name__ == "__main__":
    display_scoring_breakdown()

    # Show all calculated projections from realistic stat lines
    print("\nAll DK Fantasy Projections from Realistic Stats:")
    calculator = DKScoringCalculator()
    for player_id, stats in REALISTIC_STAT_LINES.items():
        fppg = calculator.calculate_fantasy_points(stats)
        print(f"  {player_id}: {stats.points:>2}pts/{stats.rebounds:>2}reb/{stats.assists:>2}ast -> {fppg:5.1f} fppg")