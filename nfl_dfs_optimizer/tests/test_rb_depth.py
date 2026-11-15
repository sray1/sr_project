"""Tests for the backup-RB depth-chart filter (rb_depth.py)."""

import json
import os

import pytest

import rb_depth
from rb_depth import filter_backup_rbs, rb_depth_chart

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Every test starts with an empty chart cache and no network."""
    monkeypatch.setattr(rb_depth, '_depth_cache', {})
    monkeypatch.setattr(rb_depth, 'get_team_ids',
                        lambda: {'KC': '12', 'GB': '9'})
    yield
    rb_depth._depth_cache = {}


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return json.load(f)


def fake_fetch(monkeypatch, by_url):
    """Serve _fetch_json from a {url_substring: payload} dict; missing
    URLs return None (fetch failure)."""
    def _fetch(url, params=None):
        for key, payload in by_url.items():
            if key in url:
                return payload
        return None
    monkeypatch.setattr(rb_depth, '_fetch_json', _fetch)


def rb(name, team, salary, positions=None):
    return {'name': name, 'team': team, 'salary': salary,
            'positions': positions or ['RB']}


class TestRbDepthChart:
    def test_resolves_rank_order_via_roster(self, monkeypatch):
        fake_fetch(monkeypatch, {
            'depthcharts': load_fixture('espn_depthcharts_kc.json'),
            'roster': load_fixture('espn_roster_kc.json'),
        })
        assert rb_depth_chart('KC') == [
            'ken walker', 'emmett johnson', 'jaydn ott', 'nate carter']

    def test_chart_fetch_failure_returns_empty(self, monkeypatch):
        fake_fetch(monkeypatch, {'roster': load_fixture('espn_roster_kc.json')})
        assert rb_depth_chart('KC') == []

    def test_roster_fetch_failure_returns_empty(self, monkeypatch):
        fake_fetch(monkeypatch, {
            'depthcharts': load_fixture('espn_depthcharts_kc.json')})
        assert rb_depth_chart('KC') == []

    def test_unknown_team_returns_empty(self):
        assert rb_depth_chart('XX') == []

    def test_no_scrape_returns_empty(self):
        assert rb_depth_chart('KC', allow_scrape=False) == []

    def test_cached_chart_not_refetched(self, monkeypatch):
        rb_depth._depth_cache['KC'] = ['emmett johnson']
        fake_fetch(monkeypatch, {})  # any fetch would KeyError/assert
        assert rb_depth_chart('KC') == ['emmett johnson']


class TestFilterBackupRbs:
    def kc_chart(self, monkeypatch):
        monkeypatch.setattr(rb_depth, 'rb_depth_chart',
                            lambda team, allow_scrape=True: [
                                'ken walker', 'emmett johnson',
                                'jaydn ott', 'nate carter'])

    def test_ir_chart_leader_skipped_for_pool_ranks(self, monkeypatch):
        # KC week 11: chart still lists IR'd Ken Walker at #1 — the DK
        # status filter already dropped him, so the effective chart
        # ranks among POOL backs are Johnson (#2) and Ott (#3)
        self.kc_chart(monkeypatch)
        pool = [rb('Emmett Johnson', 'KC', 4700),
                rb('Jaydn Ott', 'KC', 4000),
                rb('Nate Carter', 'KC', 4000),
                rb('Ben VanSumeren', 'KC', 4000)]
        kept, dropped = filter_backup_rbs(pool)
        assert [p['name'] for p in kept] == ['Emmett Johnson', 'Jaydn Ott']
        assert [p['name'] for p in dropped] == ['Nate Carter',
                                                 'Ben VanSumeren']

    def test_chart_lag_corroborated_by_salary(self, monkeypatch):
        # GB week 11: chart lists the injured Jacobs #1 with beat-report
        # favorite Kaleb Johnson 4th — his No. 2 DK salary keeps him
        monkeypatch.setattr(rb_depth, 'rb_depth_chart',
                            lambda team, allow_scrape=True: [
                                'josh jacobs', 'marshawn lloyd',
                                'chris brooks', 'caleb johnson'])
        pool = [rb('MarShawn Lloyd', 'GB', 4800),
                rb('Kaleb Johnson', 'GB', 4600),
                rb('Chris Brooks', 'GB', 4000),
                rb('Pierre Strong Jr.', 'GB', 4300)]
        kept, dropped = filter_backup_rbs(pool)
        assert {p['name'] for p in kept} == {'MarShawn Lloyd',
                                             'Kaleb Johnson',
                                             'Chris Brooks'}
        assert [p['name'] for p in dropped] == ['Pierre Strong Jr.']

    def test_unresolved_chart_keeps_team(self, monkeypatch):
        # Safety valve: a failed chart fetch must never bench a starter
        monkeypatch.setattr(rb_depth, 'rb_depth_chart',
                            lambda team, allow_scrape=True: [])
        pool = [rb('DJ Giddens', 'IND', 4800),
                rb('Jerome Ford', 'ARI', 4000)]
        kept, dropped = filter_backup_rbs(pool)
        assert kept == pool and dropped == []

    def test_non_rbs_untouched(self, monkeypatch):
        self.kc_chart(monkeypatch)
        pool = [rb('Emmett Johnson', 'KC', 4700),
                {'name': 'Patrick Mahomes', 'team': 'KC', 'salary': 7000,
                 'positions': ['QB']},
                {'name': 'Travis Kelce', 'team': 'KC', 'salary': 6000,
                 'positions': ['TE']},
                rb('Nate Carter', 'KC', 4000),
                rb('Ben VanSumeren', 'KC', 4000)]
        kept, dropped = filter_backup_rbs(pool)
        assert {p['name'] for p in kept} == {'Patrick Mahomes',
                                             'Travis Kelce',
                                             'Emmett Johnson',
                                             'Nate Carter'}
        assert [p['name'] for p in dropped] == ['Ben VanSumeren']

    def test_no_rbs_noop(self, monkeypatch):
        self.kc_chart(monkeypatch)
        pool = [{'name': 'Patrick Mahomes', 'team': 'KC', 'salary': 7000,
                 'positions': ['QB']}]
        kept, dropped = filter_backup_rbs(pool)
        assert kept == pool and dropped == []
