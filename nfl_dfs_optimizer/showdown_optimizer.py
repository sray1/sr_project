"""
Showdown lineup optimizer: exact MILP via pulp.

DK NFL Showdown rules:
- Roster: 1 Captain + 5 FLEX
- Captain: 1.5x multiplier on BOTH points AND salary
- Salary cap: $50,000
- Max 5 players from one team (captain counts toward the team limit)

The MILP formulation guarantees the optimal lineup under all constraints
(and the top-N variants via added diversity constraints), unlike greedy
heuristics. Cross-checked against brute-force enumeration in tests.
"""

from pulp import (LpProblem, LpMaximize, LpVariable, lpSum,
                  LpStatusOptimal, PULP_CBC_CMD)

from utils import SALARY_CAP

CAPTAIN_MULTIPLIER = 1.5
MAX_PER_TEAM = 5  # DK NFL showdown rule (captain counts)


def _solve_lineup(pool, salary_cap, allow_dst_captain=True, banned_lineups=None,
                  min_overlap_difference=2):
    """Solve one iteration of the showdown MILP.

    Args:
        pool: List of player dicts with projection, salary, team, positions
        salary_cap: Total salary cap
        allow_dst_captain: Whether DST can be captain
        banned_lineups: List of previously chosen (captain_id, set(flex_ids)) —
            subsequent lineups must differ by at least min_overlap_difference
            players from each
        min_overlap_difference: How many players must differ from prior lineups

    Returns:
        (captain, flex_list) or None if infeasible
    """
    problem = LpProblem("showdown", LpMaximize)

    # Binary variables: captain role and flex role per player
    captain_vars = {p['player_id']: LpVariable(f"cpt_{p['player_id']}", cat='Binary')
                    for p in pool}
    flex_vars = {p['player_id']: LpVariable(f"flex_{p['player_id']}", cat='Binary')
                 for p in pool}

    # Objective: 1.5x captain projection + flex projections
    problem += lpSum(
        CAPTAIN_MULTIPLIER * p['projection'] * captain_vars[p['player_id']]
        + p['projection'] * flex_vars[p['player_id']]
        for p in pool
    )

    # Exactly 1 captain, exactly 5 flex
    problem += lpSum(captain_vars.values()) == 1
    problem += lpSum(flex_vars.values()) == 5

    # A player is either captain or flex, never both
    for p in pool:
        pid = p['player_id']
        problem += captain_vars[pid] + flex_vars[pid] <= 1

    # Salary cap (captain salary at 1.5x)
    problem += lpSum(
        CAPTAIN_MULTIPLIER * p['salary'] * captain_vars[p['player_id']]
        + p['salary'] * flex_vars[p['player_id']]
        for p in pool
    ) <= salary_cap

    # Max players from one team (captain counts)
    teams = {p['team'] for p in pool if p['team']}
    for team in teams:
        team_players = [p for p in pool if p['team'] == team]
        problem += lpSum(captain_vars[p['player_id']] + flex_vars[p['player_id']]
                         for p in team_players) <= MAX_PER_TEAM

    # Optional: DST cannot be captain
    if not allow_dst_captain:
        for p in pool:
            if 'DST' in (p.get('positions') or []):
                problem += captain_vars[p['player_id']] == 0

    # Diversity from previously chosen lineups: at least N different players
    if banned_lineups:
        for prev_captain_id, prev_flex_ids in banned_lineups:
            prev_all = set(prev_flex_ids) | {prev_captain_id}
            problem += lpSum(
                captain_vars[pid] + flex_vars[pid]
                for pid in prev_all
            ) <= (1 + 5) - min_overlap_difference  # overlap of 6 slots

    solver = PULP_CBC_CMD(msg=0)
    problem.solve(solver)

    if problem.status != LpStatusOptimal:
        return None

    captain = None
    flex = []
    for p in pool:
        pid = p['player_id']
        if captain_vars[pid].value() and captain_vars[pid].value() > 0.5:
            captain = p
        elif flex_vars[pid].value() and flex_vars[pid].value() > 0.5:
            flex.append(p)

    if captain is None or len(flex) != 5:
        return None

    return captain, flex


def generate_showdown_lineups(pool, n_lineups=5, salary_cap=SALARY_CAP,
                              allow_dst_captain=True):
    """Generate the top-N optimal showdown lineups.

    Args:
        pool: Player dicts from player_builder.build_player_pool
        n_lineups: Number of lineups to return
        salary_cap: Total salary cap (default $50,000)
        allow_dst_captain: Whether DST can be captain

    Returns:
        List of lineup dicts sorted by projected total, descending:
            {'captain', 'flex', 'total_projection', 'total_salary',
             'captain_cap_salary', 'captain_cap_projection'}
    """
    lineups = []
    banned = []

    for _ in range(n_lineups):
        solution = _solve_lineup(pool, salary_cap,
                                 allow_dst_captain=allow_dst_captain,
                                 banned_lineups=banned)
        if solution is None:
            break

        captain, flex = solution
        captain_cap_salary = captain['salary'] * CAPTAIN_MULTIPLIER
        captain_cap_projection = captain['projection'] * CAPTAIN_MULTIPLIER
        total_salary = captain_cap_salary + sum(p['salary'] for p in flex)
        total_projection = captain_cap_projection + sum(p['projection'] for p in flex)

        lineups.append({
            'captain': captain,
            'flex': flex,
            'captain_cap_salary': captain_cap_salary,
            'captain_cap_projection': captain_cap_projection,
            'total_salary': total_salary,
            'total_projection': total_projection,
        })

        banned.append((captain['player_id'], {p['player_id'] for p in flex}))

    return lineups


def validate_lineup(lineup, salary_cap=SALARY_CAP):
    """Check a lineup against all DK showdown rules.

    Returns:
        List of violation strings (empty = valid)
    """
    violations = []
    captain = lineup['captain']
    flex = lineup['flex']

    if len(flex) != 5:
        violations.append(f"Expected 5 FLEX, got {len(flex)}")

    all_players = [captain] + flex
    if len({p['player_id'] for p in all_players}) != len(all_players):
        violations.append("Duplicate player in lineup")

    total_salary = captain['salary'] * CAPTAIN_MULTIPLIER + sum(p['salary'] for p in flex)
    if total_salary > salary_cap:
        violations.append(f"Salary ${total_salary:,.0f} exceeds cap ${salary_cap:,.0f}")

    team_counts = {}
    for p in all_players:
        team_counts[p['team']] = team_counts.get(p['team'], 0) + 1
    for team, count in team_counts.items():
        if count > MAX_PER_TEAM:
            violations.append(f"{count} players from {team} (max {MAX_PER_TEAM})")

    return violations