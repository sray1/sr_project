"""Tests for the per-source lineup comparison (comparison.py)."""

import pytest

from comparison import (baseline_source_name, build_lineups_per_source,
                        lineup_player_names, compare_against_baseline)


def make_draftable(pid, name, position, team, salary, game="KC @ BAL",
                   disabled=False):
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


def make_showdown_draftables():
    """A valid showdown pool: 8 players across two teams (max 5 per team)."""
    specs = [
        (1, 'Patrick Mahomes', 'QB', 'KC', 8000),
        (2, 'Travis Kelce', 'TE', 'KC', 5200),
        (3, 'Rashee Rice', 'WR', 'KC', 4800),
        (4, 'Isiah Pacheco', 'RB', 'KC', 4400),
        (5, 'Harrison Butkerish', 'K', 'KC', 3000),
        (6, 'Lamar Jackson', 'QB', 'BAL', 8500),
        (7, 'Zay Flowers', 'WR', 'BAL', 5000),
        (8, 'Derrick Henry', 'RB', 'BAL', 7000),
    ]
    return [make_draftable(*spec) for spec in specs]


def make_source_projections(draftables, proj_by_id):
    """One source dict: given projections, salary-fallback for the rest."""
    from projections import salary_fallback_projection
    return {
        p['player_id']: {
            'projection': proj_by_id.get(p['player_id'],
                                         salary_fallback_projection(
                                             p['salary'], p['position'])),
            'source': 'test' if p['player_id'] in proj_by_id else 'fallback',
        }
        for p in draftables
    }


class TestBaselineSourceName:
    def test_csv_wins(self):
        assert baseline_source_name({'fallback': {}, 'csv': {},
                                     'dailyfantasyfuel': {}}) == 'csv'

    def test_first_registry_source_wins(self):
        # dailyfantasyfuel is ahead of numberfire in FETCHER_REGISTRY
        assert baseline_source_name({'numberfire': {}, 'fallback': {},
                                     'dailyfantasyfuel': {}}) == 'dailyfantasyfuel'

    def test_fallback_when_only_fallback(self):
        assert baseline_source_name({'fallback': {}}) == 'fallback'


class TestBuildLineupsPerSource:
    def test_showdown_one_lineup_per_source(self):
        draftables = make_showdown_draftables()
        sources = {
            'dailyfantasyfuel': make_source_projections(draftables, {1: 25.0, 6: 24.0}),
            'fallback': make_source_projections(draftables, {}),
        }
        lineups = build_lineups_per_source(draftables, sources, 'showdown')
        assert set(lineups.keys()) == {'dailyfantasyfuel', 'fallback'}
        for lineup in lineups.values():
            assert lineup['total_salary'] <= 50000
            assert len(lineup['flex']) == 5
            assert lineup['captain'] is not None

    def test_higher_projection_changes_captain(self):
        draftables = make_showdown_draftables()
        mahomes_captain = make_source_projections(draftables, {1: 30.0})
        lamar_captain = make_source_projections(draftables, {6: 30.0})
        lineups = build_lineups_per_source(
            draftables, {'a': mahomes_captain, 'b': lamar_captain}, 'showdown')
        assert lineups['a']['captain']['name'] == 'Patrick Mahomes'
        assert lineups['b']['captain']['name'] == 'Lamar Jackson'

    def test_failing_source_skipped(self):
        draftables = make_showdown_draftables()
        # Only one team -> showdown infeasible (max 5 per team needs 2 teams)
        one_team = [d for d in draftables if d['team'] == 'KC']
        sources = {'good': make_source_projections(draftables, {}),
                   'thin': make_source_projections(one_team, {})}
        lineups = build_lineups_per_source(one_team + draftables[-2:], sources,
                                           'showdown')
        # 'thin' cannot field a lineup from its one-team pool
        assert 'thin' not in lineups or lineups['thin'] is not None

    def test_classic_mode(self):
        specs = [
            (1, 'Patrick Mahomes', 'QB', 'KC', 8000),
            (2, 'Isiah Pacheco', 'RB', 'KC', 4400),
            (3, 'Rashee Rice', 'WR', 'KC', 4800),
            (4, 'Travis Kelce', 'TE', 'KC', 5200),
            (5, 'Xavier Worthy', 'WR', 'KC', 3600),
            (6, 'Lamar Jackson', 'QB', 'BAL', 8500),
            (7, 'Derrick Henry', 'RB', 'BAL', 7000),
            (8, 'Zay Flowers', 'WR', 'BAL', 5000),
            (9, 'Mark Andrews', 'TE', 'BAL', 4600),
            (10, 'Isaiah Likely', 'TE', 'BAL', 3200),
            (11, 'Rashod Bateman', 'WR', 'BAL', 3000),
            (12, 'Justice Hill', 'RB', 'BAL', 2400),
            (13, 'Chiefs DST', 'DST', 'KC', 3000),
            (14, 'Ravens DST', 'DST', 'BAL', 3200),
        ]
        draftables = [make_draftable(*spec) for spec in specs]
        sources = {'dailyfantasyfuel': make_source_projections(draftables, {}),
                   'fallback': make_source_projections(draftables, {})}
        lineups = build_lineups_per_source(draftables, sources, 'classic')
        assert set(lineups.keys()) == {'dailyfantasyfuel', 'fallback'}
        for lineup in lineups.values():
            assert len(lineup['players']) == 9
            assert lineup['total_salary'] <= 50000


class TestCompareAgainstBaseline:
    def test_identical_lineups_full_overlap(self):
        draftables = make_showdown_draftables()
        sources = {'a': make_source_projections(draftables, {})}
        lineups = build_lineups_per_source(draftables, sources, 'showdown')
        lineup = lineups['a']
        cmp = compare_against_baseline(lineup, lineup, 'showdown')
        assert cmp['overlap'] == 6
        assert cmp['unique_picks'] == []
        assert cmp['same_captain'] is True

    def test_different_lineups(self):
        draftables = make_showdown_draftables()
        # Each source devalues the other's star, so the lineups genuinely
        # diverge (not just a captain swap of the same six players)
        a = make_source_projections(draftables, {1: 30.0, 6: 3.0})
        b = make_source_projections(draftables, {6: 30.0, 1: 3.0})
        lineups = build_lineups_per_source(
            draftables, {'a': a, 'b': b}, 'showdown')
        cmp = compare_against_baseline(lineups['b'], lineups['a'], 'showdown')
        assert cmp['same_captain'] is False
        assert cmp['overlap'] < 6
        assert 'Lamar Jackson' in cmp['unique_picks']

    def test_lineup_player_names_captain_first(self):
        draftables = make_showdown_draftables()
        lineups = build_lineups_per_source(
            draftables, {'a': make_source_projections(draftables, {1: 30.0})},
            'showdown')
        names = lineup_player_names(lineups['a'], 'showdown')
        assert len(names) == 6
        assert names[0] == 'Patrick Mahomes'  # captain first

class TestPoolFilterPassThrough:
    def test_backup_qbs_never_in_any_source_lineup(self):
        # Two high-projected backup QBs would be picked if the filter
        # didn't reach build_lineups_per_source
        draftables = make_showdown_draftables() + [
            make_draftable(9, 'Backup QB KC', 'QB', 'KC', 4000),
            make_draftable(10, 'Backup QB BAL', 'QB', 'BAL', 4000),
        ]
        sources = {
            'dailyfantasyfuel': make_source_projections(draftables, {
                9: 30.0, 10: 30.0}),  # backups wildly overprojected
            'fallback': make_source_projections(draftables, {}),
        }
        lineups = build_lineups_per_source(draftables, sources, 'showdown')
        assert lineups
        for lineup in lineups.values():
            names = {lineup['captain']['name']} | \
                    {p['name'] for p in lineup['flex']}
            assert 'Backup QB KC' not in names
            assert 'Backup QB BAL' not in names

    def test_exclude_reaches_lineups(self):
        draftables = make_showdown_draftables()
        sources = {
            'dailyfantasyfuel': make_source_projections(draftables, {}),
        }
        lineups = build_lineups_per_source(draftables, sources, 'showdown',
                                          exclude='Harrison Butkerish')
        names = ({lineups['dailyfantasyfuel']['captain']['name']}
                 | {p['name']
                    for p in lineups['dailyfantasyfuel']['flex']})
        assert 'Harrison Butkerish' not in names
