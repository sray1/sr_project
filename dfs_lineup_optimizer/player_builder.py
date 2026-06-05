"""
Unified player builder for DFS lineup optimizers.

Consolidates the player creation, stat-line matching, and deduplication logic
that was duplicated between showdown_analyzer.py and comprehensive_analyzer.py.

Key features:
- Deduplicates CPT/UTIL entries by keeping the lower-salary (UTIL) entry
- Matches players to stat lines by ID first, then by position + salary proximity
- Optionally includes rotation metadata (role, minutes, mpg_actual)
- Supports custom stat line overrides for dynamic projections
"""

from draftkings_scoring import DKScoringCalculator, REALISTIC_STAT_LINES
from nba_rotations import (
    get_rotation_status, get_estimated_minutes, get_actual_mpg, get_minutes_weight
)
from pydfs_lineup_optimizer.player import Player


def create_pydfs_players_with_scoring(draftables, include_rotation_meta=True,
                                       stat_lines=None, min_salary=1000):
    """Create players with proper DK scoring, deduplication, and rotation metadata.

    Deduplicates players by name — keeps the lower-salary entry (best for Showdown utility).
    In Showdown contests, DK lists each player twice (CPT at 1.5x salary, UTIL at base salary);
    we keep the UTIL entry with its own fppg.

    Args:
        draftables: DraftKings draftables response object
        include_rotation_meta: If True, returns (players, player_meta) with rotation data
        stat_lines: Optional dict of {player_id: PlayerStats} to override REALISTIC_STAT_LINES
        min_salary: Minimum salary to include (default 1000, the DK minimum)

    Returns:
        If include_rotation_meta: (players_list, meta_list) where meta_list has dicts
            with 'role', 'minutes', 'mpg_actual' for each player
        If not include_rotation_meta: just the players_list
    """
    calculator = DKScoringCalculator()

    # Use provided stat lines or fall back to default
    active_stat_lines = stat_lines if stat_lines is not None else REALISTIC_STAT_LINES

    # Filter to stat lines with positive points
    stat_lines_by_name = {}
    for stat_id, stats in active_stat_lines.items():
        if stats.points > 0:
            stat_lines_by_name[stat_id] = stats

    player_stat_mapping = {}

    # First pass: collect all player data, then deduplicate by name
    player_entries = {}  # full_name -> best entry dict

    for player in draftables.players:
        # Skip injured / unavailable
        if player.is_disabled:
            continue

        # Skip players below minimum salary
        if player.salary < min_salary:
            continue

        positions = player.position_name.split('/')
        full_name = player.name_details.display
        team = player.team_details.abbreviation
        fppg = None

        # Try to find by player ID
        for stat_id, stats in stat_lines_by_name.items():
            if str(player.player_id) == stat_id:
                fppg = calculator.calculate_fantasy_points(stats)
                break

        # Find by position and salary if no ID match
        if fppg is None:
            best_match = None
            best_diff = float('inf')

            for stat_id, stats in stat_lines_by_name.items():
                if stat_id not in player_stat_mapping:
                    expected_fppg = calculator.calculate_fantasy_points(stats)
                    expected_salary = expected_fppg * 200

                    # Position matching
                    pos_match = False
                    if 'C' in positions and stats.rebounds >= 8:
                        pos_match = True
                    elif 'PG' in positions and stats.assists >= 5:
                        pos_match = True
                    elif 'SG' in positions and stats.points >= 12:
                        pos_match = True
                    elif 'SF' in positions or 'PF' in positions:
                        pos_match = True

                    if pos_match:
                        salary_diff = abs(expected_salary - player.salary)
                        if salary_diff < best_diff:
                            best_diff = salary_diff
                            best_match = stat_id

            if best_match:
                fppg = calculator.calculate_fantasy_points(stat_lines_by_name[best_match])
                player_stat_mapping[best_match] = True

        # Fallback: use rotation-aware or salary-based projections
        if fppg is None:
            from draftkings_scoring import generate_projections_from_rotation, generate_projections_from_salary
            # Try rotation-aware projection first (uses estimated minutes + role)
            projected_stats = generate_projections_from_rotation(
                full_name, team, player.salary, positions
            )
            if projected_stats.points > 0:
                fppg = calculator.calculate_fantasy_points(projected_stats)
            else:
                # Pure salary-based fallback
                projected_stats = generate_projections_from_salary(player.salary, positions)
                fppg = calculator.calculate_fantasy_points(projected_stats)

        # Deduplicate by player name: keep the lower salary (UTIL/base price, not CPT 1.5x)
        if full_name in player_entries:
            existing = player_entries[full_name]
            if player.salary < existing['salary']:
                player_entries[full_name] = {
                    'player_id': str(player.player_id),
                    'first_name': player.name_details.first,
                    'last_name': player.name_details.last,
                    'positions': positions,
                    'team': team,
                    'salary': player.salary,
                    'fppg': fppg,
                }
        else:
            player_entries[full_name] = {
                'player_id': str(player.player_id),
                'first_name': player.name_details.first,
                'last_name': player.name_details.last,
                'positions': positions,
                'team': team,
                'salary': player.salary,
                'fppg': fppg,
            }

    # Build final player list with optional rotation metadata
    pydfs_players = []
    player_meta = []

    for full_name, entry in player_entries.items():
        pydfs_player = Player(
            player_id=entry['player_id'],
            first_name=entry['first_name'],
            last_name=entry['last_name'],
            positions=entry['positions'],
            team=entry['team'],
            salary=entry['salary'],
            fppg=entry['fppg'],
            is_injured=False,
        )
        pydfs_players.append(pydfs_player)

        if include_rotation_meta:
            role = get_rotation_status(full_name, entry['team'])
            est_minutes = get_estimated_minutes(full_name, entry['team'], salary=entry['salary'])
            is_actual = get_actual_mpg(full_name, entry['team']) is not None
            player_meta.append({'role': role, 'minutes': est_minutes, 'mpg_actual': is_actual})

    if include_rotation_meta:
        return pydfs_players, player_meta
    return pydfs_players