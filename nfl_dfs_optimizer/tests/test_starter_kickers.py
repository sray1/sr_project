"""Tests for starting-kicker resolution (starter_kickers.py).

ESPN roster data verified live 2026-10 (DAL @ GB week 6): Trey Smack is
GB's active PK while Lenny Krieg sat on the practice squad, both listed
by DK under K at $4,800.
"""

import pytest

import starter_kickers
from starter_kickers import (filter_nonstarter_kickers, get_starting_kickers,
                             get_team_ids)
from player_builder import build_player_pool


@pytest.fixture(autouse=True)
def clear_caches(monkeypatch):
    monkeypatch.setattr(starter_kickers, '_team_id_map', None)
    monkeypatch.setattr(starter_kickers, '_roster_cache', {})
    yield


TEAMS_RESPONSE = {
    'sports': [{'leagues': [{'teams': [
        {'team': {'abbreviation': 'GB', 'id': 9}},
        {'team': {'abbreviation': 'DAL', 'id': 6}},
    ]}]}],
}


def _roster_response(groups):
    """groups: {group_name: [(firstName, lastName, position)]}"""
    return {'athletes': [
        {'position': name, 'items': [
            {'firstName': fn, 'lastName': ln, 'position': pos}
            for fn, ln, pos in items]}
        for name, items in groups.items()]}


GB_ROSTER = _roster_response({
    'specialTeam': [
        ('Trey', 'Smack', 'PK'), ('Daniel', 'Whelan', 'P')],
    'practiceSquad': [('Lenny', 'Krieg', 'PK')],
})
DAL_ROSTER = _roster_response({
    'specialTeam': [
        ('Brandon', 'Aubrey', 'PK'), ('Bryan', 'Anger', 'P')],
})


def _mock_fetch(monkeypatch, by_url):
    def fake_fetch(url, params, timeout=20):
        for fragment, payload in by_url.items():
            # 'roster' guard: the roster URL contains the teams URL as a
            # prefix (.../nfl/teams/9/roster), so a bare 'teams' fragment
            # would match both fetches
            if fragment in url and ('roster' in fragment) == ('roster' in url):
                return payload
        return None
    monkeypatch.setattr(starter_kickers, '_fetch_json', fake_fetch)


def _k(name, team, salary=4800, player_id=None):
    return {'name': name, 'team': team, 'positions': ['K'],
            'salary': salary, 'player_id': player_id or name}


class TestGetStartingKickers:
    def test_practice_squad_kicker_excluded(self, monkeypatch):
        # Krieg is a PK too — but on the practice squad, not active
        _mock_fetch(monkeypatch, {'teams': TEAMS_RESPONSE,
                                  '/9/roster': GB_ROSTER})
        starters = get_starting_kickers(['GB'])
        assert starters == {'GB': {'trey smack'}}
        assert 'lenny krieg' not in starters['GB']

    def test_punter_not_a_kicker(self, monkeypatch):
        _mock_fetch(monkeypatch, {'teams': TEAMS_RESPONSE,
                                  '/6/roster': DAL_ROSTER})
        starters = get_starting_kickers(['DAL'])
        assert starters == {'DAL': {'brandon aubrey'}}
        assert 'bryan anger' not in starters['DAL']

    def test_unknown_team_maps_empty(self, monkeypatch):
        _mock_fetch(monkeypatch, {'teams': TEAMS_RESPONSE})
        assert get_starting_kickers(['XXX']) == {'XXX': set()}

    def test_failed_fetch_maps_empty(self, monkeypatch):
        # Teams fetch succeeds, roster fetch fails: team maps to empty set
        # (unresolved), never None
        def fake_fetch(url, params, timeout=20):
            if 'roster' not in url:
                return TEAMS_RESPONSE
            return None
        monkeypatch.setattr(starter_kickers, '_fetch_json', fake_fetch)
        assert get_team_ids() == {'GB': '9', 'DAL': '6'}
        assert get_starting_kickers(['GB']) == {'GB': set()}

    def test_rosters_cached_per_team(self, monkeypatch):
        calls = []

        def fake_fetch(url, params, timeout=20):
            if 'roster' in url:
                calls.append(url)
                return GB_ROSTER
            return TEAMS_RESPONSE
        monkeypatch.setattr(starter_kickers, '_fetch_json', fake_fetch)
        get_starting_kickers(['GB'])
        get_starting_kickers(['GB'])
        assert len(calls) == 1


class TestFilterNonstarterKickers:
    def test_practice_squad_kicker_dropped(self, monkeypatch):
        _mock_fetch(monkeypatch, {'teams': TEAMS_RESPONSE,
                                  '/9/roster': GB_ROSTER,
                                  '/6/roster': DAL_ROSTER})
        pool = [_k('Trey Smack', 'GB'), _k('Lenny Krieg', 'GB'),
                _k('Brandon Aubrey', 'DAL'), _k('Bryan Anger', 'DAL')]
        kept, dropped = filter_nonstarter_kickers(pool)
        kept_names = {p['name'] for p in kept}
        dropped_names = {p['name'] for p in dropped}
        assert kept_names == {'Trey Smack', 'Brandon Aubrey'}
        assert dropped_names == {'Lenny Krieg', 'Bryan Anger'}

    def test_name_drift_keeps_whole_team(self, monkeypatch):
        # DK spells the starter differently than ESPN: no pool kicker
        # matches the starter set -> keep everyone rather than guess
        _mock_fetch(monkeypatch, {'teams': TEAMS_RESPONSE,
                                  '/9/roster': GB_ROSTER})
        pool = [_k('Smack, Trey', 'GB'), _k('L. Krieg', 'GB')]
        kept, dropped = filter_nonstarter_kickers(pool)
        assert {p['name'] for p in kept} == {'Smack, Trey', 'L. Krieg'}
        assert dropped == []

    def test_fetch_failure_keeps_all(self, monkeypatch):
        monkeypatch.setattr(starter_kickers, '_fetch_json',
                            lambda url, params, timeout=20: None)
        pool = [_k('Trey Smack', 'GB'), _k('Lenny Krieg', 'GB')]
        kept, dropped = filter_nonstarter_kickers(pool)
        assert len(kept) == 2 and dropped == []

    def test_pool_without_kickers_is_untouched(self, monkeypatch):
        def boom(url, params, timeout=20):
            raise AssertionError('should not fetch anything')
        monkeypatch.setattr(starter_kickers, '_fetch_json', boom)
        pool = [{'name': 'X', 'team': 'GB', 'positions': ['WR'],
                  'salary': 3000, 'player_id': 1}]
        kept, dropped = filter_nonstarter_kickers(pool)
        assert kept == pool and dropped == []

    def test_multiple_active_pks_all_kept(self, monkeypatch):
        # Kicker injury week: two active PKs on the roster -> can't rank,
        # both stay
        roster = _roster_response({'specialTeam': [
            ('Trey', 'Smack', 'PK'), ('Lenny', 'Krieg', 'PK')]})
        _mock_fetch(monkeypatch, {'teams': TEAMS_RESPONSE,
                                  '/9/roster': roster})
        pool = [_k('Trey Smack', 'GB'), _k('Lenny Krieg', 'GB')]
        kept, dropped = filter_nonstarter_kickers(pool)
        assert len(kept) == 2 and dropped == []


class TestBuildPlayerPoolIntegration:
    def test_filter_wired_into_pool_build(self, monkeypatch):
        _mock_fetch(monkeypatch, {'teams': TEAMS_RESPONSE,
                                  '/9/roster': GB_ROSTER,
                                  '/6/roster': DAL_ROSTER})
        draftables = [
            _k('Trey Smack', 'GB', player_id=1),
            _k('Lenny Krieg', 'GB', player_id=2),
            _k('Brandon Aubrey', 'DAL', player_id=3),
            _k('Bryan Anger', 'DAL', player_id=4),
        ]
        pool = build_player_pool(
            draftables, {1: {'projection': 11.8, 'source': 'kicker_model'},
                         2: {'projection': 11.8, 'source': 'kicker_model'},
                         3: {'projection': 11.8, 'source': 'kicker_model'},
                         4: {'projection': 11.8, 'source': 'kicker_model'}},
            drop_nonstarter_kickers=True)
        assert {p['name'] for p in pool} == {'Trey Smack', 'Brandon Aubrey'}

    def test_filter_off_by_default(self, monkeypatch):
        # Default False: existing callers (tests, hindsight benchmarks)
        # keep every DK-listed kicker and never touch the network
        def boom(url, params, timeout=20):
            raise AssertionError('default must not fetch')
        monkeypatch.setattr(starter_kickers, '_fetch_json', boom)
        draftables = [_k('Lenny Krieg', 'GB', player_id=2)]
        pool = build_player_pool(draftables, {})
        assert {p['name'] for p in pool} == {'Lenny Krieg'}