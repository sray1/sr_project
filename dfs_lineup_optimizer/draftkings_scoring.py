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

    def estimate_stats_from_fppg(self, fppg: float, player_type: str = "balanced") -> PlayerStats:
        """
        Estimate player statistics from fantasy points per game.
        This is a rough approximation for when only total FPPG is available.

        Args:
            fppg: Fantasy points per game
            player_type: 'balanced', 'scorer', 'passer', 'defender', 'big'
        """
        stat_distributions = {
            'scorer': {'points': 0.4, 'rebounds': 0.15, 'assists': 0.15,
                       'steals': 0.05, 'blocks': 0.03, 'turnovers': -0.1},
            'passer': {'points': 0.2, 'rebounds': 0.15, 'assists': 0.4,
                       'steals': 0.08, 'blocks': 0.02, 'turnovers': -0.15},
            'defender': {'points': 0.15, 'rebounds': 0.25, 'assists': 0.15,
                        'steals': 0.15, 'blocks': 0.1, 'turnovers': -0.08},
            'big': {'points': 0.2, 'rebounds': 0.4, 'assists': 0.1,
                   'steals': 0.05, 'blocks': 0.15, 'turnovers': -0.1},
            'balanced': {'points': 0.25, 'rebounds': 0.25, 'assists': 0.2,
                        'steals': 0.08, 'blocks': 0.07, 'turnovers': -0.1}
        }

        dist = stat_distributions.get(player_type, stat_distributions['balanced'])

        # Estimate base stat values
        stats = PlayerStats(
            points=fppg * dist['points'] / self.rules.points,
            rebounds=fppg * dist['rebounds'] / self.rules.rebounds,
            assists=fppg * dist['assists'] / self.rules.assists,
            steals=fppg * dist['steals'] / self.rules.steals,
            blocks=fppg * dist['blocks'] / self.rules.blocks,
            turnovers=max(0, abs(fppg * dist['turnovers']) / abs(self.rules.turnovers)),
        )

        # Estimate 3-pointers (roughly 30% of points from 3s for shooters)
        stats.three_pointers = max(0, stats.points * 0.3 / 3)

        return stats


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


def calculate_all_dk_projections() -> Dict[str, float]:
    """Calculate DK fantasy points from realistic stat lines."""
    calculator = DKScoringCalculator()
    projections = {}

    for player_id, stats in REALISTIC_STAT_LINES.items():
        projections[player_id] = calculator.calculate_fantasy_points(stats)

    return projections


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

    # Show all calculated projections
    print("\nAll DK Fantasy Projections from Realistic Stats:")
    projections = calculate_all_dk_projections()
    for player_id, fppg in projections.items():
        stats = REALISTIC_STAT_LINES[player_id]
        print(f"  {stats.points:>2}pts/{stats.rebounds:>2}reb/{stats.assists:>2}ast -> {fppg:5.1f} fppg")