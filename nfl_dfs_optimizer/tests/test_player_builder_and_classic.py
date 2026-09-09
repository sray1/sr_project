"""Tests for the player builder and classic optimizer (pydfs DK Football)."""

import pytest

from player_builder import build_player_pool, build_pydfs_players
from classic_optimizer import (
    generate_classic_lineups, lineup_to_dict, validate_classic_lineup,
)


def make_draftable(pid, name, position, team, salary, game="KC @ BAL", disabled=False):
    return {
        'player_id': pid,
        'name': name,
        'position': position,
        'positions': position.split('/'),
        'salary': salary,
        'team': team,
        'game': game,
        'game_start': None,
        'is_disabled': disabled,
    }


def make_projections(ids, base=10.0):
    return {pid: {'projection': base + pid, 'source': 'test'} for pid in ids}


class TestBuildPlayerPool:
    def test_dedup_keeps_lower_salary(self):
        # Showdown slates: same player as CPT (1.5x) and FLEX (base)
        draftables = [
            make_draftable(1, 'Patrick Mahomes', 'CPT', 'KC', 12000),
            make_draftable(1, 'Patrick Mahomes', 'FLEX', 'KC', 8000),
            make_draftable(2, 'Travis Kelce', 'FLEX', 'KC', 5200),
        ]
        pool = build_player_pool(draftables, make_projections([1, 2]))
        assert len(pool) == 2
        mahomes = next(p for p in pool if p['name'] == 'Patrick Mahomes')
        assert mahomes['salary'] == 8000

    def test_disabled_players_skipped(self):
        draftables = [
            make_draftable(1, 'Patrick Mahomes', 'QB', 'KC', 8000, disabled=True),
            make_draftable(2, 'Travis Kelce', 'TE', 'KC', 5200),
        ]
        pool = build_player_pool(draftables, make_projections([1, 2]))
        assert [p['name'] for p in pool] == ['Travis Kelce']

    def test_sub_min_salary_skipped(self):
        draftables = [
            make_draftable(1, 'Cheap Guy', 'WR', 'KC', 200),
            make_draftable(2, 'Travis Kelce', 'TE', 'KC', 5200),
        ]
        pool = build_player_pool(draftables, make_projections([1, 2]))
        assert [p['name'] for p in pool] == ['Travis Kelce']

    def test_projections_attached(self):
        draftables = [make_draftable(1, 'Travis Kelce', 'TE', 'KC', 5200)]
        pool = build_player_pool(draftables, {1: {'projection': 14.2,
                                                   'source': 'csv'}})
        assert pool[0]['projection'] == 14.2
        assert pool[0]['source'] == 'csv'

    def test_missing_projection_defaults(self):
        draftables = [make_draftable(1, 'Travis Kelce', 'TE', 'KC', 5200)]
        pool = build_player_pool(draftables, {})
        assert pool[0]['projection'] == 0.0
        assert pool[0]['source'] == 'fallback'


class TestBuildPyDFSPlayers:
    def test_name_split_and_positions(self):
        draftables = [make_draftable(1, 'Patrick Mahomes', 'QB', 'KC', 8000)]
        pool = build_player_pool(draftables, make_projections([1]))
        players = build_pydfs_players(pool)

        assert len(players) == 1
        player = players[0]
        assert player.first_name == 'Patrick'
        assert player.last_name == 'Mahomes'
        assert list(player.positions) == ['QB']
        assert player.team == 'KC'
        assert player.salary == 8000.0
        assert player.fppg == 11.0  # base 10 + id 1

    def test_game_info_parsed(self):
        draftables = [make_draftable(1, 'Patrick Mahomes', 'QB', 'KC', 8000,
                                     game="KC @ BAL")]
        pool = build_player_pool(draftables, make_projections([1]))
        players = build_pydfs_players(pool)

        assert players[0].game_info.home_team == 'BAL'
        assert players[0].game_info.away_team == 'KC'


def synthetic_pool():
    """A minimal but feasible classic pool (enough to fill 9 slots under $50k)."""
    draftables = []
    pid = 0

    # KC offense (for stacking)
    for name, pos, salary in [
        ('Patrick Mahomes', 'QB', 8000),
        ('Isiah Pacheco', 'RB', 6000),
        ('Rashee Rice', 'WR', 5500),
        ('Travis Kelce', 'TE', 5000),
        ('Xavier Worthy', 'WR', 4000),
    ]:
        pid += 1
        draftables.append(make_draftable(pid, name, pos, 'KC', salary))

    # BAL offense
    for name, pos, salary in [
        ('Lamar Jackson', 'QB', 7800),
        ('Derrick Henry', 'RB', 7000),
        ('Zay Flowers', 'WR', 5200),
        ('Mark Andrews', 'TE', 4800),
    ]:
        pid += 1
        draftables.append(make_draftable(pid, name, pos, 'BAL', salary))

    # Cheap fill-ins from other games
    for name, pos, salary in [
        ('Cheap QB', 'QB', 4000),
        ('Cheap RB1', 'RB', 3000),
        ('Cheap RB2', 'RB', 3200),
        ('Cheap WR1', 'WR', 3000),
        ('Cheap WR2', 'WR', 3100),
        ('Cheap TE', 'TE', 2800),
    ]:
        pid += 1
        draftables.append(make_draftable(pid, name, pos, 'BUF', salary,
                                          game="BUF @ NE"))

    # DSTs
    draftables.append(make_draftable(90, 'Patriots DST', 'DST', 'NE', 3000,
                                     game="BUF @ NE"))
    draftables.append(make_draftable(91, 'Bills DST', 'DST', 'BUF', 3200,
                                      game="BUF @ NE"))
    draftables.append(make_draftable(92, 'Chiefs DST', 'DST', 'KC', 3400))
    draftables.append(make_draftable(93, 'Ravens DST', 'DST', 'BAL', 3300))

    return draftables


class TestClassicOptimizer:
    def test_generates_valid_lineup(self):
        draftables = synthetic_pool()
        pool = build_player_pool(draftables, make_projections(
            [p['player_id'] for p in draftables]))
        pydfs_players = build_pydfs_players(pool)

        lineups = generate_classic_lineups(pydfs_players, n_lineups=1,
                                           stack_rule='none')
        assert len(lineups) == 1

        lineup_dict = lineup_to_dict(lineups[0])
        assert validate_classic_lineup(lineup_dict) == []

        # Roster composition: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX, 1 DST
        by_pos = {}
        for player in lineup_dict['players']:
            by_pos[player['lineup_position']] = by_pos.get(
                player['lineup_position'], 0) + 1
        assert by_pos == {'QB': 1, 'RB': 2, 'WR': 3, 'TE': 1,
                          'FLEX': 1, 'DST': 1}

    def test_qbwr_stack_enforced(self):
        draftables = synthetic_pool()
        pool = build_player_pool(draftables, make_projections(
            [p['player_id'] for p in draftables]))
        pydfs_players = build_pydfs_players(pool)

        lineups = generate_classic_lineups(pydfs_players, n_lineups=1,
                                           stack_rule='qbwr')
        assert len(lineups) == 1

        lineup_dict = lineup_to_dict(lineups[0])
        qb = next(p for p in lineup_dict['players']
                  if p['lineup_position'] == 'QB')
        stack_mates = [p for p in lineup_dict['players']
                      if p['team'] == qb['team']
                      and p['lineup_position'] in ('WR', 'TE', 'FLEX')
                      and any(pos in ('WR', 'TE') for pos in p['positions'])]
        assert len(stack_mates) >= 1, "QB must be stacked with a WR/TE teammate"

    def test_unknown_stack_rule_raises(self):
        from classic_optimizer import apply_stack_rule
        with pytest.raises(ValueError):
            apply_stack_rule(None, 'bogus')

    def test_validation_catches_bad_lineup(self):
        lineup_dict = {
            'players': [
                {'name': f'P{i}', 'lineup_position': 'WR', 'positions': ['WR'],
                 'team': 'KC', 'salary': 8000, 'projection': 10.0}
                for i in range(9)
            ],
            'total_projection': 90.0,
            'total_salary': 72000,
        }
        violations = validate_classic_lineup(lineup_dict)
        assert any('QB' in v for v in violations)
        assert any('DST' in v for v in violations)
        assert any('exceeds cap' in v for v in violations)

        # Not enough skill-position players to fill RB/WR/TE/FLEX slots
        thin_lineup = {
            'players': [
                {'name': 'QB1', 'lineup_position': 'QB', 'positions': ['QB'],
                 'team': 'KC', 'salary': 8000, 'projection': 20.0},
                {'name': 'DST1', 'lineup_position': 'DST', 'positions': ['DST'],
                 'team': 'NE', 'salary': 3000, 'projection': 8.0},
            ] + [
                {'name': f'K{i}', 'lineup_position': 'FLEX', 'positions': ['K'],
                 'team': 'BUF', 'salary': 3000, 'projection': 5.0}
                for i in range(7)
            ],
            'total_projection': 63.0,
            'total_salary': 35000,
        }
        violations = validate_classic_lineup(thin_lineup)
        assert any('RB/WR/TE' in v for v in violations)

class TestBackupPlayerFilters:
    """Backup-QB filter + manual exclusions in build_player_pool."""

    def make_qb_pool(self):
        """NE @ SEA-style slate with two backups behind each starter."""
        draftables = [
            make_draftable(1, 'Drake Maye', 'QB', 'NE', 10000),
            make_draftable(2, 'Tommy DeVito', 'QB', 'NE', 8000),
            make_draftable(3, 'Behren Morton', 'QB', 'NE', 6000),
            make_draftable(4, 'Sam Darnold', 'QB', 'SEA', 9400),
            make_draftable(5, 'Drew Lock', 'QB', 'SEA', 7400),
            make_draftable(6, 'Rhamondre Stevenson', 'RB', 'NE', 8400),
        ]
        return draftables, make_projections([1, 2, 3, 4, 5, 6])

    def test_backup_qbs_dropped_by_default(self):
        draftables, projections = self.make_qb_pool()
        pool = build_player_pool(draftables, projections)
        names = {p['name'] for p in pool}
        assert names == {'Drake Maye', 'Sam Darnold', 'Rhamondre Stevenson'}

    def test_filter_backup_qbs_direct(self):
        from player_builder import filter_backup_qbs
        draftables, projections = self.make_qb_pool()
        pool = build_player_pool(draftables, projections,
                                 drop_backup_qbs=False)
        kept, dropped = filter_backup_qbs(pool)
        assert {p['name'] for p in kept} == {'Drake Maye', 'Sam Darnold',
                                             'Rhamondre Stevenson'}
        assert {p['name'] for p in dropped} == {'Tommy DeVito',
                                               'Behren Morton', 'Drew Lock'}

    def test_keep_backup_qbs_opt_out(self):
        draftables, projections = self.make_qb_pool()
        pool = build_player_pool(draftables, projections, drop_backup_qbs=False)
        assert len(pool) == 6  # nothing dropped

    def test_tied_qb_salary_keeps_both(self):
        # Can't tell a true QB controversy apart — keep both
        draftables = [
            make_draftable(1, 'QB A', 'QB', 'NE', 9000),
            make_draftable(2, 'QB B', 'QB', 'NE', 9000),
        ]
        pool = build_player_pool(draftables, make_projections([1, 2]))
        assert len(pool) == 2

    def test_non_qb_positions_untouched(self):
        # Backup RBs/WRs are legitimately playable — only QBs are filtered
        draftables = [
            make_draftable(1, 'Starter RB', 'RB', 'NE', 8000),
            make_draftable(2, 'Backup RB', 'RB', 'NE', 3000),
        ]
        pool = build_player_pool(draftables, make_projections([1, 2]))
        assert len(pool) == 2

    def test_exclude_by_name_string(self):
        draftables, projections = self.make_qb_pool()
        pool = build_player_pool(draftables, projections,
                                 drop_backup_qbs=False,
                                 exclude='Rhamondre Stevenson, Tommy DeVito')
        assert {p['name'] for p in pool} == {'Drake Maye',
                                            'Behren Morton', 'Sam Darnold',
                                            'Drew Lock'}

    def test_exclude_dst_entry(self):
        from player_builder import exclude_named_players
        draftables = [
            make_draftable(1, 'Seahawks DST', 'DST', 'SEA', 2800),
            make_draftable(2, 'Kenneth Walker', 'RB', 'SEA', 8600),
        ]
        pool = build_player_pool(draftables, make_projections([1, 2]),
                                 drop_backup_qbs=False)
        kept, dropped = exclude_named_players(pool, ['Seahawks DST'])
        assert [p['name'] for p in kept] == ['Kenneth Walker']
        assert [p['name'] for p in dropped] == ['Seahawks DST']

    def test_exclude_noop_on_empty(self):
        draftables, projections = self.make_qb_pool()
        pool = build_player_pool(draftables, projections, exclude=None)
        assert 'Rhamondre Stevenson' in {p['name'] for p in pool}
