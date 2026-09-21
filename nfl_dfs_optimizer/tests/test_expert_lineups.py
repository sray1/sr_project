"""Tests for expert_lineups.py (Stokastic weekly cheat-sheet lineup).

The parser is frozen against tests/fixtures/stokastic_week1.html (live
serve of the week-1 2026 article). Its worked lineup: Burrow $6,900,
Ja'Marr Chase $7,800, Chase Brown $7,100, Gibbs $8,000, Hubbard $5,500,
Luther Burden III $5,500, Devaughn Vele $3,500, Mayer $2,900, Falcons DST
$2,600 = $49,800 / 138.5. The trap: item 1's prose mentions
"Tee Higgins ($6,300)" — explicitly NOT in the lineup.
"""

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

import expert_lineups
from expert_lineups import (build_expert_lineup, derive_week,
                            fetch_stokastic_lineup,
                            parse_stokastic_lineup, print_expert_lineup)

FIXTURE = Path(__file__).parent / 'fixtures' / 'stokastic_week1.html'


def make_draftable(pid, name, position, team, salary, game='KC @ BAL'):
    return {
        'player_id': pid, 'name': name, 'position': position,
        'positions': [position], 'salary': salary, 'team': team,
        'game': game, 'game_start': None, 'is_disabled': False,
        'status': '',
    }


def week1_draftables():
    """The 9 article picks + the Higgins trap + an unrelated player."""
    return [
        make_draftable(1, 'Joe Burrow', 'QB', 'CIN', 6900, 'CIN @ CLE'),
        make_draftable(2, "Ja'Marr Chase", 'WR', 'CIN', 7800, 'CIN @ CLE'),
        make_draftable(3, 'Chase Brown', 'RB', 'CIN', 7100, 'CIN @ CLE'),
        make_draftable(4, 'Jahmyr Gibbs', 'RB', 'DET', 8000, 'DET vs ATL'),
        make_draftable(5, 'Chuba Hubbard', 'RB', 'CAR', 5500, 'CAR @ CHI'),
        make_draftable(6, 'Luther Burden III', 'WR', 'CHI', 5500,
                       'CAR @ CHI'),
        make_draftable(7, 'Devaughn Vele', 'WR', 'NO', 3500, 'DET vs ATL'),
        make_draftable(8, 'Trey Mayer', 'TE', 'LV', 2900, 'LV vs MIA'),
        make_draftable(9, 'Falcons', 'DST', 'ATL', 2600, 'DET vs ATL'),
        # The trap: salary-mentioned in the prose, NOT in the lineup
        make_draftable(10, 'Tee Higgins', 'WR', 'CIN', 6300, 'CIN @ CLE'),
        # An unrelated player sharing no name with any pick
        make_draftable(11, 'Bijan Robinson', 'RB', 'ATL', 7700,
                       'DET vs ATL'),
    ]


def parse_fixture(draftables):
    return parse_stokastic_lineup(FIXTURE.read_text(encoding='utf-8'),
                                  draftables)


class TestParseFixture:
    def test_full_lineup_extracted_and_validated(self, capsys):
        lineup = parse_fixture(week1_draftables())
        assert lineup is not None
        assert len(lineup['players']) == 9
        assert lineup['total_salary'] == 49800
        assert lineup['total_projection'] == pytest.approx(138.5)
        assert "Stokastic: expert lineup validated" in \
            capsys.readouterr().out

    def test_higgins_trap_excluded(self):
        lineup = parse_fixture(week1_draftables())
        names = {p['name'] for p in lineup['players']}
        assert 'Tee Higgins' not in names
        assert "Ja'Marr Chase" in names
        assert 'Chase Brown' in names

    def test_last_name_mentions_resolve(self):
        # The article says "Burrow", "Gibbs", "Hubbard", "Vele", "Mayer",
        # "Falcons" — DK lists full names
        lineup = parse_fixture(week1_draftables())
        names = {p['name'] for p in lineup['players']}
        assert names == {'Joe Burrow', "Ja'Marr Chase", 'Chase Brown',
                         'Jahmyr Gibbs', 'Chuba Hubbard',
                         'Luther Burden III', 'Devaughn Vele', 'Trey Mayer',
                         'Falcons'}

    def test_salaries_match_the_article(self):
        lineup = parse_fixture(week1_draftables())
        by_name = {p['name']: p['salary'] for p in lineup['players']}
        assert by_name['Joe Burrow'] == 6900
        assert by_name['Jahmyr Gibbs'] == 8000
        assert by_name['Falcons'] == 2600

    def test_flex_slot_assigned(self):
        # 3 RBs (Brown, Gibbs, Hubbard) -> the third is the FLEX
        lineup = parse_fixture(week1_draftables())
        slots = [p['lineup_position'] for p in lineup['players']]
        assert slots.count('FLEX') == 1
        assert slots.count('QB') == 1 and slots.count('DST') == 1
        assert slots.count('RB') == 2 and slots.count('WR') == 3
        assert slots.count('TE') == 1

    def test_opponent_resolved_from_game(self):
        lineup = parse_fixture(week1_draftables())
        by_name = {p['name']: p for p in lineup['players']}
        assert by_name['Joe Burrow']['opponent'] == 'CLE'
        assert by_name['Falcons']['opponent'] == 'DET'

    def test_double_listed_players_deduped(self):
        # DK classic slates double-list players (FLEX slot rows); the
        # lowest-salary row must win or every pick looks "ambiguous"
        draftables = week1_draftables() + [
            make_draftable(4, 'Jahmyr Gibbs', 'RB', 'DET', 8000),
            dict(make_draftable(8, 'Trey Mayer', 'TE', 'LV', 2900),
                 positions=['TE', 'FLEX']),
        ]
        lineup = parse_fixture(draftables)
        assert lineup is not None
        assert len(lineup['players']) == 9

    def test_per_player_projection_is_none(self):
        # Stokastic publishes the lineup total, not per-player projections
        lineup = parse_fixture(week1_draftables())
        assert all(p['projection'] is None for p in lineup['players'])


class TestValidationFailures:
    """Every failure returns None with a loud note — nothing fabricated."""

    def test_salary_mismatch_rejected(self, capsys):
        draftables = week1_draftables()
        draftables[0]['salary'] = 7000  # Burrow's DK salary != article's
        assert parse_fixture(draftables) is None
        assert 'salary mismatch' in capsys.readouterr().out

    def test_player_missing_from_slate_rejected(self, capsys):
        draftables = [d for d in week1_draftables()
                      if d['name'] != 'Trey Mayer']
        assert parse_fixture(draftables) is None
        assert "'Mayer' not found on the slate" in capsys.readouterr().out

    def test_ambiguous_last_name_rejected(self, capsys):
        draftables = week1_draftables() + [
            make_draftable(12, 'Marvin Mayer', 'TE', 'KC', 2900)]
        assert parse_fixture(draftables) is None
        assert 'ambiguous' in capsys.readouterr().out

    def test_over_cap_rejected(self, capsys):
        draftables = week1_draftables()
        # Keep every stated salary intact but blow the cap: bump Burrow's
        # article salary by faking the draftable at a matching higher value
        for d in draftables:
            if d['name'] == 'Joe Burrow':
                d['salary'] = 9900
        html = FIXTURE.read_text(encoding='utf-8').replace('6,900', '9,900')
        lineup = parse_stokastic_lineup(html, draftables)
        assert lineup is None
        assert 'over the' in capsys.readouterr().out

    def test_stated_total_mismatch_rejected(self, capsys):
        # Article says $49,800; deflate one pick by $100 (stays under the
        # cap) so only the stated-total check can catch it
        draftables = week1_draftables()
        for d in draftables:
            if d['name'] == 'Falcons':
                d['salary'] = 2500
        html = FIXTURE.read_text(encoding='utf-8').replace('$2,600', '$2,500')
        lineup = parse_stokastic_lineup(html, draftables)
        assert lineup is None
        assert 'article total' in capsys.readouterr().out

    def test_illegal_roster_rejected(self, capsys):
        # Swap the DST out for a QB: two QBs, no DST -> not a classic roster
        draftables = week1_draftables()
        for d in draftables:
            if d['name'] == 'Falcons':
                d.update(name='Backup QB', position='QB', positions=['QB'])
        html = FIXTURE.read_text(encoding='utf-8').replace(
            'the Falcons ($2,600', 'Backup QB ($2,600')
        lineup = parse_stokastic_lineup(html, draftables)
        assert lineup is None
        assert 'legal classic roster' in capsys.readouterr().out

    def test_no_worked_example_section(self, capsys):
        assert parse_stokastic_lineup('<html>nothing here</html>',
                                      week1_draftables()) is None
        assert 'no' in capsys.readouterr().out.lower()


class TestUnits:
    def test_derive_week(self):
        assert derive_week(datetime(2026, 9, 13, 17, 0,
                                    tzinfo=timezone.utc)) == 1
        assert derive_week(datetime(2026, 9, 10, 0, 30,
                                    tzinfo=timezone.utc)) == 1  # TNF
        assert derive_week(datetime(2026, 9, 20, 17, 0,
                                    tzinfo=timezone.utc)) == 2
        assert derive_week(date(2026, 9, 7)) is None  # before week 1
        assert derive_week(None) is None

    def test_decimal_period_is_not_a_sentence_break(self):
        # "plus 2.1% leverage" between two picks must NOT end the run
        items = ["Burden ($5,500, plus 2.1% leverage), "
                 "Vele ($3,500) and Mayer ($2,900)."]
        picks = expert_lineups._picks_from_items(items)
        assert picks == [('Burden', 5500), ('Vele', 3500), ('Mayer', 2900)]

    def test_sentence_break_ends_the_run(self):
        items = ["Brown ($7,100). Higgins ($6,300) is out."]
        picks = expert_lineups._picks_from_items(items)
        assert picks == [('Brown', 7100)]  # Higgins is prose, not a pick

    def test_leading_and_the_stripped(self):
        items = ["Chase ($7,800) and Chase Brown ($7,100), plus the "
                 "Falcons ($2,600)."]
        picks = expert_lineups._picks_from_items(items)
        assert picks == [('Chase', 7800), ('Chase Brown', 7100),
                         ('Falcons', 2600)]

    def test_is_legal_classic(self):
        legal = week1_draftables()[:9]
        assert expert_lineups._is_legal_classic(legal)
        assert not expert_lineups._is_legal_classic(legal[1:])  # no QB


class TestBuildExpertLineup:
    """build_expert_lineup: hand-transcribed picks, same validation gates."""

    def showdown_draftables(self):
        # 4 KC + 2 IND, so a 6-pick lineup can stay under 5 per team
        return [
            make_draftable(1, 'Patrick Mahomes', 'QB', 'KC', 9600,
                           'IND @ KC'),
            make_draftable(2, 'Travis Kelce', 'TE', 'KC', 7000, 'IND @ KC'),
            make_draftable(3, 'Xavier Worthy', 'WR', 'KC', 5400, 'IND @ KC'),
            make_draftable(4, 'Harrison Butker', 'K', 'KC', 4600, 'IND @ KC'),
            make_draftable(5, 'Jonathan Taylor', 'RB', 'IND', 11000,
                           'IND @ KC'),
            make_draftable(6, 'Tyler Warren', 'TE', 'IND', 7200, 'IND @ KC'),
        ]

    def showdown_picks(self):
        # CPT stated at the 1.5x CPT-slot price the article prints
        return [('Patrick Mahomes', 14400, True),
                ('Jonathan Taylor', 11000, False),
                ('Travis Kelce', 7000, False),
                ('Tyler Warren', 7200, False),
                ('Xavier Worthy', 5400, False),
                ('Harrison Butker', 4600, False)]

    def test_showdown_lineup_built(self, capsys):
        lineup = build_expert_lineup(self.showdown_draftables(),
                                     self.showdown_picks(), 'showdown', 'si')
        assert lineup is not None
        assert lineup['captain']['name'] == 'Patrick Mahomes'
        assert lineup['captain']['salary'] == 9600  # base, not the 1.5x price
        assert len(lineup['flex']) == 5
        assert lineup['total_salary'] == 49600  # includes the 1.5x CPT price
        assert lineup['total_projection'] == 0.0  # no stated projection
        assert 'si: expert lineup validated' in capsys.readouterr().out

    def test_cpt_price_not_1_5x_whole_salary_rejected(self, capsys):
        picks = self.showdown_picks()
        picks[0] = ('Patrick Mahomes', 14002, True)  # not 1.5x anything
        assert build_expert_lineup(self.showdown_draftables(), picks,
                                   'showdown', 'si') is None
        assert 'not 1.5x a whole DK salary' in capsys.readouterr().out

    def test_salary_mismatch_rejected(self, capsys):
        picks = self.showdown_picks()
        picks[1] = ('Jonathan Taylor', 9900, False)
        assert build_expert_lineup(self.showdown_draftables(), picks,
                                   'showdown', 'si') is None
        assert 'salary mismatch' in capsys.readouterr().out

    def test_wrong_pick_count_rejected(self, capsys):
        assert build_expert_lineup(self.showdown_draftables(),
                                   self.showdown_picks()[:5],
                                   'showdown', 'si') is None
        assert 'expected 6' in capsys.readouterr().out

    def test_no_captain_rejected(self, capsys):
        picks = [(n, s, False) for n, s, _ in self.showdown_picks()]
        assert build_expert_lineup(self.showdown_draftables(), picks,
                                   'showdown', 'si') is None
        assert 'captains, expected 1' in capsys.readouterr().out

    def test_over_five_per_team_rejected(self, capsys):
        draftables = self.showdown_draftables() + [
            make_draftable(7, 'Rashee Rice', 'WR', 'KC', 4800, 'IND @ KC'),
            make_draftable(8, 'Kareem Hunt', 'RB', 'KC', 3600, 'IND @ KC'),
        ]
        picks = [('Patrick Mahomes', 14400, True),
                 ('Travis Kelce', 7000, False),
                 ('Xavier Worthy', 5400, False),
                 ('Harrison Butker', 4600, False),
                 ('Rashee Rice', 4800, False),
                 ('Kareem Hunt', 3600, False)]
        assert build_expert_lineup(draftables, picks, 'showdown', 'si') is None
        assert 'max 5' in capsys.readouterr().out

    def test_over_cap_rejected(self, capsys):
        draftables = self.showdown_draftables()
        for d in draftables:
            if d['name'] == 'Jonathan Taylor':
                d['salary'] = 15000
        picks = self.showdown_picks()
        picks[1] = ('Jonathan Taylor', 15000, False)
        assert build_expert_lineup(draftables, picks, 'showdown',
                                   'si') is None
        assert 'over the' in capsys.readouterr().out

    def test_classic_lineup_built(self):
        # The same 9 stokastic week-1 picks, hand-transcribed
        picks = [('Joe Burrow', 6900, False),
                 ("Ja'Marr Chase", 7800, False),
                 ('Chase Brown', 7100, False),
                 ('Jahmyr Gibbs', 8000, False),
                 ('Chuba Hubbard', 5500, False),
                 ('Luther Burden III', 5500, False),
                 ('Devaughn Vele', 3500, False),
                 ('Trey Mayer', 2900, False),
                 ('Falcons', 2600, False)]
        lineup = build_expert_lineup(week1_draftables(), picks, 'classic',
                                     'test')
        assert lineup is not None
        assert lineup['total_salary'] == 49800
        assert len(lineup['players']) == 9
        assert all(p['projection'] is None for p in lineup['players'])

    def test_classic_illegal_roster_rejected(self, capsys):
        picks = [('Joe Burrow', 6900, False),
                 ("Ja'Marr Chase", 7800, False),
                 ('Chase Brown', 7100, False),
                 ('Jahmyr Gibbs', 8000, False),
                 ('Chuba Hubbard', 5500, False),
                 ('Luther Burden III', 5500, False),
                 ('Devaughn Vele', 3500, False),
                 ('Trey Mayer', 2900, False),
                 ('Bijan Robinson', 7700, False)]  # no DST
        assert build_expert_lineup(week1_draftables(), picks, 'classic',
                                   'test') is None
        assert 'legal classic roster' in capsys.readouterr().out

    def test_stated_total_projection_kept(self):
        picks = self.showdown_picks()
        lineup = build_expert_lineup(self.showdown_draftables(), picks,
                                     'showdown', 'si',
                                     total_projection=138.5)
        assert lineup['total_projection'] == 138.5

    def test_print_showdown_expert_lineup(self, capsys):
        lineup = build_expert_lineup(self.showdown_draftables(),
                                     self.showdown_picks(), 'showdown', 'si')
        print_expert_lineup(lineup, source='si')
        out = capsys.readouterr().out
        assert 'CPT' in out and 'Patrick Mahomes' in out
        assert '14,400' in out  # captain printed at the 1.5x price


class TestFetch:
    def test_fetch_parses_fixture(self, monkeypatch):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.text = FIXTURE.read_text(encoding='utf-8')
        captured = {}
        def fake_get(url, **kw):
            captured['url'] = url
            return response
        monkeypatch.setattr('expert_lineups.requests.get', fake_get)
        lineup = fetch_stokastic_lineup(
            week1_draftables(),
            starts_at=datetime(2026, 9, 13, 17, tzinfo=timezone.utc))
        assert lineup is not None and len(lineup['players']) == 9
        # week derived from the Sunday start -> week-1 URL
        assert captured['url'].endswith('cheat-sheet-week-1')

    def test_fetch_failure_returns_none(self, monkeypatch, capsys):
        import requests as requests_mod
        monkeypatch.setattr(
            'expert_lineups.requests.get',
            Mock(side_effect=requests_mod.ConnectionError('boom')))
        assert fetch_stokastic_lineup(week1_draftables(), week=1) is None
        assert 'fetch failed' in capsys.readouterr().out

    def test_no_week_returns_none(self, capsys):
        assert fetch_stokastic_lineup(week1_draftables()) is None
        assert 'cannot determine NFL week' in capsys.readouterr().out

    def test_print_expert_lineup(self, capsys):
        lineup = parse_fixture(week1_draftables())
        print_expert_lineup(lineup)
        out = capsys.readouterr().out
        assert 'expert published lineup' in out
        assert 'Joe Burrow' in out and '$49,800' in out