"""Tests for the showdown pulp MILP optimizer."""

import pytest
from itertools import combinations

from showdown_optimizer import (
    generate_showdown_lineups, validate_lineup, MAX_PER_TEAM,
)


def make_player(pid, name, team, salary, projection, positions=('FLEX',)):
    return {
        'player_id': pid,
        'name': name,
        'team': team,
        'salary': salary,
        'projection': projection,
        'positions': list(positions),
    }


def small_pool():
    """A pool small enough for brute-force enumeration as an oracle."""
    return [
        make_player(1, 'QB Alpha', 'KC', 8000, 22.0),
        make_player(2, 'WR Beta', 'KC', 6000, 16.0),
        make_player(3, 'TE Gamma', 'KC', 4000, 10.0),
        make_player(4, 'RB Delta', 'KC', 5200, 14.0),
        make_player(5, 'QB Epsilon', 'BAL', 7400, 20.0),
        make_player(6, 'WR Zeta', 'BAL', 5600, 15.0),
        make_player(7, 'RB Eta', 'BAL', 4800, 12.5),
        make_player(8, 'WR Theta', 'BAL', 3400, 8.5),
        make_player(9, 'TE Iota', 'BAL', 2600, 6.0),
        make_player(10, 'WR Kappa', 'KC', 3000, 7.5),
    ]


def brute_force_optimal(pool, salary_cap=50000):
    """Exhaustive oracle: enumerate every CPT + C(remaining, 5) combo."""
    best = None
    for captain in pool:
        captain_salary = captain['salary'] * 1.5
        if captain_salary + 5 * 300 > salary_cap:
            continue
        remaining = [p for p in pool if p['player_id'] != captain['player_id']]
        for combo in combinations(remaining, 5):
            total_salary = captain_salary + sum(p['salary'] for p in combo)
            if total_salary > salary_cap:
                continue
            total_projection = (captain['projection'] * 1.5
                                + sum(p['projection'] for p in combo))
            if best is None or total_projection > best:
                best = total_projection
    return best


class TestShowdownMILP:
    def test_optimal_matches_brute_force(self):
        """The MILP must find the same total as exhaustive enumeration."""
        pool = small_pool()
        lineups = generate_showdown_lineups(pool, n_lineups=1)
        assert len(lineups) == 1

        optimal = brute_force_optimal(pool)
        assert optimal is not None
        assert lineups[0]['total_projection'] == pytest.approx(optimal)

    def test_captain_multiplier_applied(self):
        pool = small_pool()
        lineups = generate_showdown_lineups(pool, n_lineups=1)
        lineup = lineups[0]

        captain = lineup['captain']
        assert lineup['captain_cap_salary'] == pytest.approx(captain['salary'] * 1.5)
        assert lineup['captain_cap_projection'] == pytest.approx(
            captain['projection'] * 1.5)

    def test_salary_cap_respected(self):
        pool = small_pool()
        for lineup in generate_showdown_lineups(pool, n_lineups=5):
            assert lineup['total_salary'] <= 50000

    def test_lineup_structure(self):
        pool = small_pool()
        lineup = generate_showdown_lineups(pool, n_lineups=1)[0]

        assert len(lineup['flex']) == 5
        all_ids = {lineup['captain']['player_id']} | {
            p['player_id'] for p in lineup['flex']}
        assert len(all_ids) == 6  # no duplicates

    def test_team_limit_enforced(self):
        # Pool of one team's players where stacking 6 from KC would maximize
        # score if the team limit were not enforced
        pool = [make_player(i, f'Player {i}', 'KC', 3000 + i * 500, 10.0 + i)
                for i in range(1, 9)]
        # Opposing team fill-ins so a legal lineup exists
        pool += [make_player(20, 'Opponent A', 'BAL', 3000, 9.0),
                 make_player(21, 'Opponent B', 'BAL', 3000, 8.0)]
        lineup = generate_showdown_lineups(pool, n_lineups=1)[0]

        team_counts = {}
        for p in [lineup['captain']] + lineup['flex']:
            team_counts[p['team']] = team_counts.get(p['team'], 0) + 1
        assert max(team_counts.values()) <= MAX_PER_TEAM

    def test_multiple_lineups_are_distinct(self):
        pool = small_pool()
        lineups = generate_showdown_lineups(pool, n_lineups=4)

        assert len(lineups) == 4
        seen = set()
        for lineup in lineups:
            ids = frozenset({lineup['captain']['player_id']}
                            | {p['player_id'] for p in lineup['flex']})
            assert ids not in seen
            seen.add(ids)

        # Sorted descending by projection
        projections = [l['total_projection'] for l in lineups]
        assert projections == sorted(projections, reverse=True)

    def test_no_dst_captain(self):
        pool = small_pool() + [
            make_player(99, 'KC DST', 'KC', 3500, 9.0, positions=('DST', 'FLEX')),
        ]
        lineup = generate_showdown_lineups(pool, n_lineups=1,
                                            allow_dst_captain=False)[0]
        assert 'DST' not in (lineup['captain'].get('positions') or [])

        # With DST allowed, the MILP is free to pick it
        lineup_ok = generate_showdown_lineups(pool, n_lineups=1,
                                              allow_dst_captain=True)[0]
        assert validate_lineup(lineup_ok) == []

    def test_infeasible_pool(self):
        # 5 players only - can't fill 6 spots
        pool = small_pool()[:5]
        assert generate_showdown_lineups(pool, n_lineups=1) == []

    def test_validate_lineup_catches_violations(self):
        pool = small_pool()
        lineup = generate_showdown_lineups(pool, n_lineups=1)[0]
        assert validate_lineup(lineup) == []

        # Break the lineup: duplicate a flex player in place of the captain
        broken = dict(lineup)
        broken['captain'] = lineup['flex'][0]
        broken['total_salary'] = 99999
        violations = validate_lineup(broken)
        assert len(violations) >= 1  # duplicate or salary (or both) flagged