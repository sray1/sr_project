"""
Classic (Sunday main slate) lineup optimizer via pydfs_lineup_optimizer.

DK NFL Classic rules (handled natively by pydfs DraftKingsFootballSettings):
- Roster: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (WR/RB/TE), 1 DST
- Salary cap: $50,000

Stacking rules are CLI-configurable:
- qbwr (default): QB + at least one WR/TE from his own team
- qb2: QB + two pass-catchers from his own team
- team3: any 3 players from one team
- bringback: GameStack — pieces from both sides of the same game
"""

from pydfs_lineup_optimizer import get_optimizer, Site
from pydfs_lineup_optimizer.constants import Sport as PyDFSSport
from pydfs_lineup_optimizer.stacks import PositionsStack, TeamStack, GameStack

STACK_RULES = ('none', 'qbwr', 'qb2', 'team3', 'bringback')


def build_optimizer(players):
    """Create a pydfs DK Football optimizer loaded with the player pool.

    Args:
        players: List of pydfs Player objects from player_builder.build_pydfs_players

    Returns:
        Configured LineupOptimizer
    """
    optimizer = get_optimizer(Site.DRAFTKINGS, PyDFSSport.FOOTBALL)
    optimizer.load_players(players)
    return optimizer


def apply_stack_rule(optimizer, stack_rule='qbwr'):
    """Add a stacking rule to the optimizer.

    Args:
        optimizer: LineupOptimizer from build_optimizer
        stack_rule: One of STACK_RULES:
            none     - no stack constraints
            qbwr     - QB + 1 WR/TE teammate (default)
            qb2      - QB + 2 WR/TE teammates
            team3    - 3 players from one team
            bringback- 4 players from one game, at least 1 from each side

    Returns:
        The optimizer (for chaining)

    Raises:
        ValueError: On an unknown stack rule
    """
    if stack_rule not in STACK_RULES:
        raise ValueError(f"Unknown stack rule '{stack_rule}' (choose from {STACK_RULES})")

    if stack_rule == 'qbwr':
        optimizer.add_stack(PositionsStack(positions=['QB', ('WR', 'TE')]))
    elif stack_rule == 'qb2':
        optimizer.add_stack(PositionsStack(positions=['QB', ('WR', 'TE'), ('WR', 'TE')]))
    elif stack_rule == 'team3':
        optimizer.add_stack(TeamStack(size=3))
    elif stack_rule == 'bringback':
        # 4 players from the same game, at least 1 from each team
        optimizer.add_stack(GameStack(size=4, min_from_team=1))

    return optimizer


def generate_classic_lineups(players, n_lineups=1, stack_rule='qbwr'):
    """Generate the top-N classic lineups.

    Args:
        players: pydfs Player objects
        n_lineups: Number of lineups to generate (default 1: top only)
        stack_rule: Stacking rule (see apply_stack_rule)

    Returns:
        List of pydfs Lineup objects
    """
    optimizer = build_optimizer(players)
    apply_stack_rule(optimizer, stack_rule)

    return list(optimizer.optimize(n=n_lineups))


def lineup_to_dict(lineup):
    """Convert a pydfs Lineup into a plain dict for display/verification.

    Returns:
        Dict with players (list of dicts), total_projection, total_salary
    """
    players = []
    for player in lineup:
        players.append({
            'name': player.full_name,
            'lineup_position': player.lineup_position,
            'positions': player.positions,
            'team': player.team,
            'salary': player.salary,
            'projection': player.fppg,
            'opponent': '',
        })
        if player.game_info:
            home = player.game_info.home_team or ''
            away = player.game_info.away_team or ''
            if player.team == home:
                players[-1]['opponent'] = f"vs {away}" if away else ''
            elif player.team == away:
                players[-1]['opponent'] = f"@ {home}" if home else ''

    return {
        'players': players,
        'total_projection': lineup.fantasy_points_projection,
        'total_salary': lineup.salary_costs,
    }


def validate_classic_lineup(lineup_dict, salary_cap=50000):
    """Check a classic lineup against DK NFL rules.

    Returns:
        List of violation strings (empty = valid)
    """
    from contest_detector import ContestInfo

    violations = []
    players = lineup_dict['players']

    if len(players) != 9:
        violations.append(f"Expected 9 players, got {len(players)}")

    # Count how many players can fill each required slot (positional feasibility)
    required = ContestInfo.CLASSIC_POSITIONS
    filled = {pos: 0 for pos in required}
    remaining = list(players)
    for pos in ('QB', 'RB', 'WR', 'TE', 'DST'):
        filled[pos] = sum(1 for p in remaining
                          if pos in (p.get('positions') or p.get('lineup_position', '').split('/')))
    if filled['QB'] < required['QB']:
        violations.append(f"Need {required['QB']} QB, have {filled['QB']}")
    if filled['DST'] < required['DST']:
        violations.append(f"Need {required['DST']} DST, have {filled['DST']}")

    # FLEX fills with any RB/WR/TE; check combined feasibility
    flex_capable = sum(1 for p in players if any(pos in ('RB', 'WR', 'TE')
                                                for pos in (p.get('positions') or [])))
    if flex_capable < required['RB'] + required['WR'] + required['TE'] + required['FLEX']:
        violations.append(
            f"Need {required['RB'] + required['WR'] + required['TE'] + required['FLEX']} "
            f"RB/WR/TE total, have {flex_capable}")

    total_salary = sum(p['salary'] for p in players)
    if total_salary > salary_cap:
        violations.append(f"Salary ${total_salary:,.0f} exceeds cap ${salary_cap:,.0f}")

    return violations