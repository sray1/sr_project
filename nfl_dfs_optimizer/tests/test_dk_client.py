"""Tests for the raw DK draftables fetch + status parsing (dk_client.py).

The raw draftables endpoint (not the draft_kings client) is used because
it carries DK's injury `status` field (OUT/IR/Q/D), live-verified
2026-09-12: Jordyn Tyson sat in the pool at $5,100 with DK's payload
already saying 'IR', while isDisabled was still False and DFF had no tag.
"""

from datetime import datetime, timezone
from unittest.mock import Mock

import dk_client


def make_raw(player_id, name, position='WR', team='KC', salary=3000,
             status='None', is_disabled=False, salary_mult=1,
             game='KC @ BAL', start='2026-09-13T17:00:00.0000000Z'):
    return {
        'playerId': player_id,
        'draftableId': player_id * 7,
        'displayName': name,
        'position': position,
        'salary': salary * salary_mult if salary else None,
        'teamAbbreviation': team,
        'isDisabled': is_disabled,
        'status': status,
        'competition': {'name': game, 'startTime': start},
    }


def mock_response(payload):
    response = Mock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class TestFetchDraftables:
    def _fetch(self, monkeypatch, entries):
        monkeypatch.setattr(
            'dk_client.requests.get',
            lambda url, **kw: mock_response({'draftables': entries}))

    def test_normalized_fields(self, monkeypatch):
        self._fetch(monkeypatch, [
            make_raw(1, 'Travis Kelce', 'TE', 'KC', 5200, status='Q')])
        players = dk_client.fetch_draftables(1)
        assert players == [{
            'player_id': 1, 'name': 'Travis Kelce', 'position': 'TE',
            'positions': ['TE'], 'salary': 5200, 'team': 'KC',
            'game': 'KC @ BAL',
            'game_start': datetime(2026, 9, 13, 17, 0,
                                   tzinfo=timezone.utc),
            'is_disabled': False, 'status': 'Q',
        }]

    def test_status_values_normalized(self, monkeypatch):
        self._fetch(monkeypatch, [
            make_raw(1, 'Out Guy', status='OUT'),
            make_raw(2, 'IR Guy', status='IR'),
            make_raw(3, 'Plain Guy', status='None'),
            make_raw(4, 'No Key Guy'),
        ])
        players = dk_client.fetch_draftables(1)
        by_name = {p['name']: p['status'] for p in players}
        assert by_name == {'Out Guy': 'OUT', 'IR Guy': 'IR',
                           'Plain Guy': '', 'No Key Guy': ''}

    def test_showdown_cpt_and_util_rows_kept_raw(self, monkeypatch):
        # Dedup is player_builder's job; the fetch must keep both rows
        self._fetch(monkeypatch, [
            make_raw(1, 'Patrick Mahomes', 'CPT', 'KC', 12000,
                     salary_mult=1),
            make_raw(1, 'Patrick Mahomes', 'FLEX', 'KC', 8000)])
        players = dk_client.fetch_draftables(1)
        assert len(players) == 2
        assert sorted(p['salary'] for p in players) == [8000, 12000]

    def test_fetch_failure_returns_empty(self, monkeypatch):
        import requests as requests_mod
        monkeypatch.setattr(
            'dk_client.requests.get',
            Mock(side_effect=requests_mod.ConnectionError('boom')))
        assert dk_client.fetch_draftables(1) == []

    def test_no_spoofed_browser_ua_sent(self, monkeypatch):
        # DK's Akamai filter (mid-Sept 2026) 403-blocks browser-like UAs on
        # the raw endpoint while requests' default UA passes — a regression
        # that re-adds a spoofed UA would silently break every fetch.
        sent_kwargs = {}

        def capture_get(url, **kwargs):
            sent_kwargs.update(kwargs)
            return mock_response({'draftables': []})

        monkeypatch.setattr('dk_client.requests.get', capture_get)
        dk_client.fetch_draftables(1)
        ua = sent_kwargs.get('headers', {}).get('User-Agent')
        assert ua is None

    def test_raw_failure_falls_back_to_client(self, monkeypatch):
        # Raw endpoint dead -> the draft_kings client keeps the pool alive
        # (at the cost of the injury status field)
        import requests as requests_mod
        monkeypatch.setattr(
            'dk_client.requests.get',
            Mock(side_effect=requests_mod.ConnectionError('403')))
        fallback = Mock()
        fallback.players = [
            Mock(player_id=1, name_details=Mock(display='Patrick Mahomes'),
                 position_name='QB', salary=8000.0,
                 team_details=Mock(abbreviation='KC'), is_disabled=False,
                 competition_details=Mock(
                     name='KC @ BAL',
                     starts_at=datetime(2026, 9, 13, 17, 0,
                                        tzinfo=timezone.utc)))]
        client = Mock()
        client.draftables.return_value = fallback
        monkeypatch.setattr('dk_client.get_draftkings_client',
                            lambda: client)
        players = dk_client.fetch_draftables(1)
        assert len(players) == 1
        p = players[0]
        assert p['name'] == 'Patrick Mahomes'
        assert p['salary'] == 8000
        assert p['status'] == ''  # the known cost of the fallback
        assert p['game_start'] == datetime(2026, 9, 13, 17, 0,
                                           tzinfo=timezone.utc)

    def test_raw_and_client_both_fail_returns_empty(self, monkeypatch):
        import requests as requests_mod
        monkeypatch.setattr(
            'dk_client.requests.get',
            Mock(side_effect=requests_mod.ConnectionError('403')))
        client = Mock()
        client.draftables.side_effect = RuntimeError('also dead')
        monkeypatch.setattr('dk_client.get_draftkings_client',
                            lambda: client)
        assert dk_client.fetch_draftables(1) == []


class TestDKStatusPoolFilter:
    """build_player_pool drops DK-status OUT/IR; Q/D stay in."""

    def make_draftable(self, pid, name, status):
        return {
            'player_id': pid, 'name': name, 'position': 'WR',
            'positions': ['WR'], 'salary': 3000, 'team': 'KC',
            'game': 'KC @ BAL', 'game_start': None,
            'is_disabled': False, 'status': status,
        }

    def test_out_and_ir_dropped_q_and_d_kept(self):
        from player_builder import build_player_pool
        draftables = [
            self.make_draftable(1, 'Out Guy', 'OUT'),
            self.make_draftable(2, 'IR Guy', 'IR'),
            self.make_draftable(3, 'Questionable Guy', 'Q'),
            self.make_draftable(4, 'Doubtful Guy', 'D'),
            self.make_draftable(5, 'Plain Guy', ''),
        ]
        projections = {p['player_id']: {'projection': 8.0, 'source': 'test'}
                        for p in draftables}
        pool = build_player_pool(draftables, projections,
                                 drop_backup_qbs=False)
        assert {p['name'] for p in pool} == {'Questionable Guy',
                                             'Doubtful Guy', 'Plain Guy'}

    def test_lowercase_status_also_caught(self):
        # Defensive: DK reports uppercase, but the filter must not depend
        # on it
        from player_builder import build_player_pool
        draftables = [self.make_draftable(1, 'Out Guy', 'out')]
        pool = build_player_pool(draftables, {1: {'projection': 8.0,
                                                  'source': 'test'}},
                                 drop_backup_qbs=False)
        assert pool == []

    def test_missing_status_key_is_fine(self):
        # Older synthetic draftables / tests don't carry the status key
        from player_builder import build_player_pool
        draftable = self.make_draftable(1, 'Plain Guy', '')
        del draftable['status']
        pool = build_player_pool([draftable],
                                 {1: {'projection': 8.0, 'source': 'test'}},
                                 drop_backup_qbs=False)
        assert [p['name'] for p in pool] == ['Plain Guy']