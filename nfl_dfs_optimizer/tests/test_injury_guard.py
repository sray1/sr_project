"""Tests for the ESPN injury-report guard (injury_guard.py)."""

import json
import os

import pytest

import injury_guard
from injury_guard import (espn_injury_exclusions, espn_injury_notes,
                          fetch_injury_report)

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                       'espn_injuries.json')


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Every test starts with an unfetched cache and ends clean."""
    monkeypatch.setattr(injury_guard, '_injury_cache', None)
    yield
    injury_guard._injury_cache = None


def load_injury_fixture(monkeypatch):
    """Serve the saved ESPN injuries payload without the network."""
    payload = json.load(open(FIXTURE, encoding='utf-8'))

    class Resp:
        ok = True
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(injury_guard.requests, 'get',
                        lambda url, headers=None, timeout=None: Resp())


def player(name, team):
    return {'name': name, 'team': team, 'positions': ['WR']}


class TestFetchInjuryReport:
    def test_parses_teams_statuses_and_names(self, monkeypatch, capsys):
        load_injury_fixture(monkeypatch)
        report = fetch_injury_report()

        assert report['NE']['kayshon boutte'] == 'out'
        assert report['NE']['drake maye'] == 'questionable'
        assert report['NE']['antonio gibson'] == 'injured reserve'
        assert report['SEA']['zach charbonnet'] == 'doubtful'
        # Washington: ESPN id 28 abbreviates WSH; DK-style alias applied
        assert report['WAS']['austin ekeler'] == 'out'
        # 'Active' entries (returned from injury) are kept as data but
        # are neither a drop nor a note status
        assert report['NE']['rhamondre stevenson'] == 'active'
        assert 'ESPN injury report:' in capsys.readouterr().out

    def test_no_scrape_returns_empty(self):
        assert fetch_injury_report(allow_scrape=False) == {}

    def test_fetch_failure_returns_empty(self, monkeypatch, capsys):
        import requests
        monkeypatch.setattr(injury_guard.requests, 'get',
                            lambda url, headers=None, timeout=None:
                            (_ for _ in ()).throw(
                                requests.ConnectionError('down')))
        assert fetch_injury_report() == {}
        assert 'failed' in capsys.readouterr().out

    def test_cached_report_not_refetched(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        fetch_injury_report()
        monkeypatch.setattr(injury_guard.requests, 'get',
                            lambda *a, **kw: pytest.fail('re-fetched'))
        assert fetch_injury_report()['NE']['kayshon boutte'] == 'out'


class TestExclusions:
    def test_out_and_ir_excluded_with_team_qualification(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        slate = [
            player('Kayshon Boutte', 'NE'),      # Out -> dropped
            player('Antonio Gibson', 'NE'),      # IR -> dropped
            player('Sam Howell', 'SEA'),         # Out -> dropped
            player('Austin Ekeler', 'WSH'),      # Out, ESPN id 28 -> dropped
        ]
        assert espn_injury_exclusions(slate) == [
            ('Kayshon Boutte', 'NE'),
            ('Antonio Gibson', 'NE'),
            ('Sam Howell', 'SEA'),
            ('Austin Ekeler', 'WSH'),
        ]

    def test_questionable_doubtful_active_stay_in_pool(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        slate = [
            player('Drake Maye', 'NE'),           # Questionable
            player('Zach Charbonnet', 'SEA'),     # Doubtful
            player('Rhamondre Stevenson', 'NE'),  # Active (was hurt)
            player('Kenneth Walker', 'SEA'),      # healthy, not listed
        ]
        assert espn_injury_exclusions(slate) == []

    def test_same_name_other_team_not_excluded(self, monkeypatch):
        # The league-wide report lists Boutte under NE; a same-named
        # player on KC must survive (team-qualified matching)
        load_injury_fixture(monkeypatch)
        assert espn_injury_exclusions(
            [player('Kayshon Boutte', 'KC')]) == []

    def test_no_report_no_exclusions(self):
        assert espn_injury_exclusions(
            [player('Anyone', 'NE')]) == []


class TestNotes:
    def test_doubtful_questionable_reported(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        slate = [player('Kayshon Boutte', 'NE'),
                 player('Drake Maye', 'NE'),
                 player('Zach Charbonnet', 'SEA')]
        assert espn_injury_notes(slate) == [
            'Drake Maye (NE) — Questionable',
            'Zach Charbonnet (SEA) — Doubtful',
        ]

    def test_out_players_not_in_notes(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        assert espn_injury_notes([player('Kayshon Boutte', 'NE')]) == []
