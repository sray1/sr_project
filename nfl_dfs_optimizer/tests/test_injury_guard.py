"""Tests for the ESPN injury-report guard (injury_guard.py)."""

import json
import os
from datetime import datetime, timezone

import pytest

import injury_guard
from injury_guard import (espn_injury_exclusions, espn_injury_notes,
                          fetch_injury_report)

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                       'espn_injuries.json')

# The fixture's world: every fresh designation is dated Sat Nov 9,
# the stale one (Polk) Sat Nov 1; NE and SEA both played Sun Nov 8.
LAST_GAME = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)
FROZEN_NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Every test starts with an unfetched cache, no schedule data (all
    designations count as fresh), and ends clean."""
    monkeypatch.setattr(injury_guard, '_injury_cache', None)
    monkeypatch.setattr(injury_guard, '_injury_dates', None)
    monkeypatch.setattr(injury_guard, '_schedule_cache', {})
    monkeypatch.setattr(injury_guard, 'team_game_dates',
                        lambda team, allow_scrape=True: [])
    monkeypatch.setattr(injury_guard, '_utcnow', lambda: FROZEN_NOW)
    yield
    injury_guard._injury_cache = None
    injury_guard._injury_dates = None
    injury_guard._schedule_cache = {}


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
    def test_parses_teams_statuses_names_and_dates(self, monkeypatch, capsys):
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
        # Designation timestamps parsed in parallel for staleness checks
        assert injury_guard._injury_dates['NE']['kayshon boutte'] == \
            datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
        # normalize_name turns "Ja'Lynn" into "ja lynn" (apostrophe ->
        # space), the same normalization the slate lookup applies
        assert injury_guard._injury_dates['NE']["ja lynn polk"] == \
            datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)
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


class TestStaleDesignations:
    """Out/IR tags that predate the team's last game (the TNF
    short-week trap: WAS@NYG week 10, Skattebo's stale Sunday 'Out')."""

    def real_schedule(self, monkeypatch, games):
        # Single-team slates: every team_game_dates call gets the same
        # list (tests only ever probe one team's players)
        monkeypatch.setattr(injury_guard, 'team_game_dates',
                            lambda team, allow_scrape=True: games)

    def test_stale_out_kept_in_pool_and_noted(self, monkeypatch, capsys):
        load_injury_fixture(monkeypatch)
        self.real_schedule(monkeypatch, [LAST_GAME])
        slate = [player('Kayshon Boutte', 'NE'),   # dated after last game
                 player("Ja'Lynn Polk", 'NE')]     # dated before it
        assert espn_injury_exclusions(slate) == [
            ('Kayshon Boutte', 'NE'),
        ]
        assert 'predate' in capsys.readouterr().out
        assert espn_injury_notes(slate) == [
            "Ja'Lynn Polk (NE) — Out (stale tag — verify)",
        ]

    def test_fresh_out_still_excluded_with_schedule_present(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        self.real_schedule(monkeypatch, [LAST_GAME])
        slate = [player('Kayshon Boutte', 'NE')]   # Sat tag, Sun game
        assert espn_injury_exclusions(slate) == [('Kayshon Boutte', 'NE')]
        assert espn_injury_notes(slate) == []

    def test_missing_timestamp_never_stale(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        # Schedule fetch would prove Boutte stale, but his timestamp is
        # missing — no evidence, so the drop stands (old behavior)
        fetch_injury_report()
        injury_guard._injury_dates['NE']['kayshon boutte'] = None
        monkeypatch.setattr(injury_guard, 'team_game_dates',
                            lambda *a, **kw: pytest.fail('schedule fetched'))
        assert espn_injury_exclusions(
            [player('Kayshon Boutte', 'NE')]) == [('Kayshon Boutte', 'NE')]

    def test_schedule_failure_treated_fresh(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        # autouse fresh_cache already stubs team_game_dates -> [] (fetch
        # failed): every designation counts as fresh and drops stand
        assert espn_injury_exclusions([player("Ja'Lynn Polk", 'NE')]) == [
            ("Ja'Lynn Polk", 'NE'),
        ]

    def test_game_after_designation_not_stale(self, monkeypatch):
        load_injury_fixture(monkeypatch)
        # Boutte's tag is dated AFTER the Nov 8 game; the Nov 15 game is
        # after 'now' (Nov 12), so no game falls between the tag and
        # the fetch — an upcoming game proves nothing, the drop stands
        upcoming = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
        self.real_schedule(monkeypatch, [LAST_GAME, upcoming])
        assert espn_injury_exclusions(
            [player('Kayshon Boutte', 'NE')]) == [('Kayshon Boutte', 'NE')]
