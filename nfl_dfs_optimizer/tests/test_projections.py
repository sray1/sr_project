"""Tests for projection sources, name matching, and fallbacks (projections.py)."""

import os

import pytest

from projections import (
    normalize_name, normalize_dst_name, load_csv_projections,
    salary_fallback_projection, get_player_projections, get_source_projections,
    _match_key,
    _parse_dff_projections, _parse_bluecollar_projections, _strip_injury_tag,
    get_dff_out_names, get_dff_team_mismatches, scrape_dailyfantasyfuel,
)
import projections


@pytest.fixture(autouse=True)
def clean_dff_out_cache():
    """Isolate the DFF OUT-name and team caches between tests."""
    projections._dff_out_cache = None
    projections._dff_team_cache = None
    yield
    projections._dff_out_cache = None
    projections._dff_team_cache = None


class TestNameNormalization:
    def test_lowercase_and_strip(self):
        assert normalize_name("  Patrick Mahomes ") == "patrick mahomes"

    def test_suffix_stripped(self):
        assert normalize_name("Patrick Mahomes II") == "patrick mahomes"
        assert normalize_name("Odell Beckham Jr.") == "odell beckham"
        assert normalize_name("Odell Beckham Sr") == "odell beckham"

    def test_punctuation(self):
        assert normalize_name("D'Andre Swift") == "d andre swift"
        assert normalize_name("A.J. Brown") == "a j brown"

    def test_empty(self):
        assert normalize_name(None) == ''
        assert normalize_name('') == ''

    def test_dst_normalization(self):
        assert normalize_dst_name("Patriots DST") == "patriots"
        assert normalize_dst_name("New England Patriots DST") == "patriots"
        assert normalize_dst_name("Kansas City Chiefs Defense") == "chiefs"

    def test_match_key(self):
        assert _match_key("Patrick Mahomes") == "patrick mahomes"
        assert _match_key("Patrick Mahomes", "KC") == "patrick mahomes|kc"


class TestCSVProjections:
    def test_load_sample_csv(self):
        projections = load_csv_projections('sample_projections.csv')
        assert projections['patrick mahomes'] == 24.5
        assert projections['lamar jackson'] == 23.8

    def test_team_key_created(self):
        projections = load_csv_projections('sample_projections.csv')
        assert projections['patrick mahomes|kc'] == 24.5

    def test_alternate_column_names(self, tmp_path):
        csv_file = tmp_path / "proj.csv"
        csv_file.write_text("player,draftkings_projection\n"
                            "Josh Allen,22.75\n", encoding='utf-8')
        projections = load_csv_projections(str(csv_file))
        assert projections['josh allen'] == 22.75

    def test_missing_columns_raises(self, tmp_path):
        csv_file = tmp_path / "bad.csv"
        csv_file.write_text("foo,bar\n1,2\n", encoding='utf-8')
        with pytest.raises(ValueError):
            load_csv_projections(str(csv_file))

    def test_skips_bad_rows(self, tmp_path):
        csv_file = tmp_path / "messy.csv"
        csv_file.write_text("name,points\n"
                            "Josh Allen,20.5\n"
                            "Bad Row,NaN\n"
                            ",15.0\n", encoding='utf-8')
        projections = load_csv_projections(str(csv_file))
        assert projections == {'josh allen': 20.5}


class TestSalaryFallback:
    def test_qb_curve(self):
        # $6,000 QB: 6000 * 0.0022 + 4 = 17.2
        assert salary_fallback_projection(6000, 'QB') == 17.2

    def test_rb_curve(self):
        # $6,000 RB: 6000 * 0.0021 + 2 = 14.6
        assert salary_fallback_projection(6000, 'RB') == 14.6

    def test_dst_curve(self):
        # $3,000 DST: 3000 * 0.0028 + 2 = 10.4
        assert salary_fallback_projection(3000, 'DST') == 10.4

    def test_unknown_position(self):
        # Unknown positions get the generic curve
        assert salary_fallback_projection(5000, 'K') == 12.0

    def test_zero_salary(self):
        assert salary_fallback_projection(0, 'QB') == 0.0
        assert salary_fallback_projection(None, 'QB') == 0.0


class TestGetPlayerProjections:
    @staticmethod
    def make_pool():
        return [
            {'player_id': 1, 'name': 'Patrick Mahomes', 'position': 'QB',
             'positions': ['QB'], 'team': 'KC', 'salary': 8000},
            {'player_id': 2, 'name': 'Patriots DST', 'position': 'DST',
             'positions': ['DST'], 'team': 'NE', 'salary': 3000},
            {'player_id': 3, 'name': 'Unknown Player', 'position': 'WR',
             'positions': ['WR'], 'team': 'BUF', 'salary': 4000},
        ]

    def test_csv_takes_priority(self, tmp_path, monkeypatch):
        csv_file = tmp_path / "proj.csv"
        csv_file.write_text("name,points\nPatrick Mahomes,25.5\n", encoding='utf-8')

        # Scraper would return a different value; CSV must win
        monkeypatch.setattr('projections.run_scrape_fetchers',
                            lambda week=None: ('numberfire',
                                              {'patrick mahomes': 10.0}))

        result = get_player_projections(
            self.make_pool(), csv_path=str(csv_file), allow_scrape=True)

        assert result[1]['projection'] == 25.5
        assert result[1]['source'] == 'csv'

    def test_scrape_used_when_no_csv(self, monkeypatch):
        monkeypatch.setattr('projections.run_scrape_fetchers',
                            lambda week=None: ('numberfire',
                                              {'patrick mahomes': 21.0}))

        result = get_player_projections(self.make_pool(), allow_scrape=True)
        assert result[1]['projection'] == 21.0
        assert result[1]['source'] == 'numberfire'

    def test_fallback_when_no_source_matches(self, monkeypatch):
        monkeypatch.setattr('projections.run_scrape_fetchers',
                            lambda week=None: (None, {}))

        result = get_player_projections(self.make_pool(), allow_scrape=True)
        assert result[3]['source'] == 'fallback'
        assert result[3]['projection'] == 10.4  # 4000 * 0.0021 + 2

    def test_no_scrape_flag_skips_fetchers(self, monkeypatch):
        def boom(week=None):
            raise AssertionError("Scraper should not run with allow_scrape=False")

        monkeypatch.setattr('projections.run_scrape_fetchers', boom)
        result = get_player_projections(self.make_pool(), allow_scrape=False)
        assert all(v['source'] == 'fallback' for v in result.values())

    def test_dst_matches_team_token(self, monkeypatch):
        monkeypatch.setattr('projections.run_scrape_fetchers',
                            lambda week=None: ('numberfire', {'patriots': 8.5}))
        result = get_player_projections(self.make_pool(), allow_scrape=True)
        assert result[2]['projection'] == 8.5
        assert result[2]['source'] == 'numberfire'

    def test_every_player_gets_projection(self, monkeypatch):
        monkeypatch.setattr('projections.run_scrape_fetchers',
                            lambda week=None: (None, {}))
        result = get_player_projections(self.make_pool())
        assert set(result.keys()) == {1, 2, 3}
        assert all(v['projection'] >= 0 for v in result.values())

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name), encoding='utf-8') as f:
        return f.read()


class TestStripInjuryTag:
    def test_strips_questionable(self):
        assert _strip_injury_tag("Ja'Marr Chase Q") == "Ja'Marr Chase"

    def test_strips_out(self):
        assert _strip_injury_tag("Tyreek Hill O") == 'Tyreek Hill'

    def test_keeps_name_parts(self):
        # 'III' is a generational suffix, not an injury tag
        assert _strip_injury_tag('James Cook III') == 'James Cook III'

    def test_no_tag(self):
        assert _strip_injury_tag('Jahmyr Gibbs') == 'Jahmyr Gibbs'


class TestDFFParser:
    MINI_HTML = """
    <table>
      <thead>
        <tr><th>PLAYER</th><th>MATCHUP</th><th>PROJECTIONS</th></tr>
        <tr><th>POS</th><th>NAME</th><th>SALARY</th><th>TEAM</th><th>OPP</th>
            <th>DvP</th><th>DK FP PROJECTED</th><th>VALUE PROJECTED</th></tr>
      </thead>
      <tbody>
        <tr>
          <td class="hidden-sm hidden-md hidden-lg">J. Gibbs RB $8000 FPTS 23.8</td>
          <td>RB</td><td class="box">Jahmyr Gibbs</td><td>$ 8.0k</td><td>DET</td>
          <td>NO</td><td>16</td><td>23.8</td><td>2.98</td>
        </tr>
        <tr>
          <td class="hidden-sm hidden-md hidden-lg">J. Chase Q WR $7800 FPTS 21.8</td>
          <td>WR</td><td class="box">Ja'Marr Chase Q</td><td>$ 7.8k</td><td>CIN</td>
          <td>TB</td><td>16</td><td>21.8</td><td>2.79</td>
        </tr>
        <tr>
          <td class="hidden-sm hidden-md hidden-lg">Jaguars DST $3400 FPTS 7.3</td>
          <td>DST</td><td class="box">Jaguars</td><td>$ 3.4k</td><td>JAX</td>
          <td>CLE</td><td>30</td><td>7.3</td><td>2.16</td>
        </tr>
        <tr>
          <td class="hidden-sm hidden-md hidden-lg">No Proj QB $5000</td>
          <td>QB</td><td class="box">No Proj</td><td>$ 5.0k</td><td>KC</td>
          <td>DEN</td><td>20</td><td>--</td><td>2.50</td>
        </tr>
        <tr data-inj="O">
          <td class="hidden-sm hidden-md hidden-lg">Z. Charbonnet RB $8200</td>
          <td>RB</td><td class="box">Zach Charbonnet O</td><td>$ 8.2k</td>
          <td>SEA</td><td>NE</td><td>18</td><td>15.4</td><td>1.88</td>
        </tr>
      </tbody>
    </table>
    """

    def test_mini_table(self):
        projections, out_names, team_map = _parse_dff_projections(
            self.MINI_HTML)
        assert projections['jahmyr gibbs'] == 23.8
        assert projections['jahmyr gibbs|det'] == 23.8
        # Injury tag stripped, apostrophe normalized like DK names
        assert projections["ja marr chase"] == 21.8
        assert projections['ja marr chase|cin'] == 21.8
        # DST rows match on team token, no team-suffix key
        assert projections['jaguars'] == 7.3
        assert 'jaguars|jax' not in projections
        # '--' projections are skipped
        assert 'no proj' not in projections
        # data-inj="O" -> excluded from projections, reported as OUT
        # (name, team) tuple: team-qualified for pool exclusion matching
        assert 'zach charbonnet' not in projections
        assert out_names == [('Zach Charbonnet', 'SEA')]
        # Team map covers every non-DST row — projected, '--' and OUT alike
        # (traded players are detected even without a posted projection)
        assert team_map == {'jahmyr gibbs': {'DET'},
                            'ja marr chase': {'CIN'},
                            'no proj': {'KC'},
                            'zach charbonnet': {'SEA'}}

    def test_no_table_returns_empty(self):
        assert _parse_dff_projections(
            '<html><body><p>hi</p></body></html>') == ({}, [], {})

    def test_missing_detail_header_returns_empty(self):
        # Group-only header row (no POS/NAME) -> layout unrecognized
        html = ('<table><thead><tr><th>PLAYER</th></tr></thead>'
                '<tbody><tr><td>RB</td><td>Jahmyr Gibbs</td></tr></tbody></table>')
        assert _parse_dff_projections(html) == ({}, [], {})

    def test_frozen_fixture(self):
        """Full live page frozen 2026-09-08 (16-game week-1 slate)."""
        projections, out_names, team_map = _parse_dff_projections(
            load_fixture('dff_projections.html'))
        assert len(projections) > 800  # ~450 players x (name + name|team) keys
        assert projections['jahmyr gibbs'] == 23.8
        assert projections['patrick mahomes'] == 17.5
        assert projections['travis kelce'] == 11.2
        assert projections['jaguars'] == 7.3
        # 19 unique players listed OUT/IR on the board (2 listed twice by
        # DFF, deduped): excluded from projections, team-qualified
        assert len(out_names) == 19
        assert ('Zach Charbonnet', 'SEA') in out_names
        assert ('TreVeyon Henderson', 'NE') in out_names
        assert ('Michael Penix Jr.', 'ATL') in out_names
        assert 'zach charbonnet' not in projections
        assert 'treveyon henderson' not in projections
        # Questionable players stay in (they may play)
        assert 'tory horton' in projections
        # Team map: one entry per listed player, teams match DK abbreviations
        assert 300 < len(team_map) < 600
        assert team_map['jahmyr gibbs'] == {'DET'}
        assert team_map['zach charbonnet'] == {'SEA'}  # OUT rows included
        assert 'jaguars' not in team_map  # DST rows excluded

    def test_scraper_registered_first(self):
        from projections import FETCHER_REGISTRY
        assert FETCHER_REGISTRY[0][0] == 'dailyfantasyfuel'
        assert 'bluecollar' in [name for name, _ in FETCHER_REGISTRY]


class TestDFFOutList:
    def test_scrape_populates_cache(self, monkeypatch):
        monkeypatch.setattr(projections, '_fetch_html',
                            lambda url: load_fixture('dff_projections.html'))
        scrape_dailyfantasyfuel()
        out_names = get_dff_out_names()
        assert ('Zach Charbonnet', 'SEA') in out_names
        assert ('TreVeyon Henderson', 'NE') in out_names

    def test_cached_names_avoid_refetch(self, monkeypatch):
        monkeypatch.setattr(projections, '_dff_out_cache',
                            [('Zach Charbonnet', 'SEA')])
        def boom(week=None):
            raise AssertionError('must not re-scrape')
        monkeypatch.setattr(projections, 'scrape_dailyfantasyfuel', boom)
        assert get_dff_out_names() == [('Zach Charbonnet', 'SEA')]

    def test_no_cache_no_scrape_returns_empty(self, monkeypatch):
        def boom(week=None):
            raise AssertionError('must not scrape')
        monkeypatch.setattr(projections, 'scrape_dailyfantasyfuel', boom)
        assert get_dff_out_names(allow_scrape=False) == []

    def test_failed_scrape_yields_empty_not_none(self, monkeypatch):
        monkeypatch.setattr(projections, '_fetch_html', lambda url: None)
        assert scrape_dailyfantasyfuel() == {}
        # cache [] (not None) so get_dff_out_names never re-fetches
        assert get_dff_out_names() == []

    def test_merge_with_manual_exclusions(self):
        from player_builder import merge_exclusions
        merged = merge_exclusions('Tommy DeVito, Seahawks DST',
                                  [('Zach Charbonnet', 'SEA'),
                                   ('TreVeyon Henderson', 'NE')])
        assert merged == ['Tommy DeVito', 'Seahawks DST',
                          ('Zach Charbonnet', 'SEA'),
                          ('TreVeyon Henderson', 'NE')]


class TestDFFTeamMismatch:
    """Traded players: DK slate entries DFF lists under a different team."""

    @staticmethod
    def dk_player(name, team, positions=('WR',)):
        return {'name': name, 'team': team, 'positions': list(positions)}

    def _load_fixture_cache(self, monkeypatch):
        monkeypatch.setattr(projections, '_fetch_html',
                            lambda url: load_fixture('dff_projections.html'))

    def test_traded_player_flagged_with_dk_team(self, monkeypatch):
        # DK's slate still lists Charbonnet on NE; DFF's board carries SEA
        self._load_fixture_cache(monkeypatch)
        players = [self.dk_player('Zach Charbonnet', 'NE')]
        assert get_dff_team_mismatches(players) == [('Zach Charbonnet', 'NE')]

    def test_matching_team_not_flagged(self, monkeypatch):
        self._load_fixture_cache(monkeypatch)
        players = [self.dk_player('Zach Charbonnet', 'SEA'),
                   self.dk_player('Jahmyr Gibbs', 'DET')]
        assert get_dff_team_mismatches(players) == []

    def test_player_not_on_dff_board_not_flagged(self, monkeypatch):
        self._load_fixture_cache(monkeypatch)
        assert get_dff_team_mismatches(
            [self.dk_player('Never Heard Of Him', 'NE')]) == []

    def test_dst_entries_skipped(self, monkeypatch):
        self._load_fixture_cache(monkeypatch)
        players = [self.dk_player('Jaguars', 'NE', ('DST',))]
        assert get_dff_team_mismatches(players) == []

    def test_team_abbr_alias_not_a_mismatch(self, monkeypatch):
        # Convention drift between sites (WSH vs WAS) must never exclude
        monkeypatch.setattr(projections, '_dff_team_cache',
                            {'jacob smith': {'WSH'}})
        assert get_dff_team_mismatches(
            [self.dk_player('Jacob Smith', 'WAS')]) == []

    def test_same_name_on_two_dff_teams_not_flagged(self, monkeypatch):
        # Two same-named players league-wide; the DK entry matches one
        monkeypatch.setattr(projections, '_dff_team_cache',
                            {'jacob smith': {'NE', 'HOU'}})
        assert get_dff_team_mismatches(
            [self.dk_player('Jacob Smith', 'NE')]) == []

    def test_dk_player_without_team_skipped(self, monkeypatch):
        monkeypatch.setattr(projections, '_dff_team_cache',
                            {'jacob smith': {'HOU'}})
        assert get_dff_team_mismatches(
            [self.dk_player('Jacob Smith', '')]) == []

    def test_failed_scrape_returns_empty(self, monkeypatch):
        monkeypatch.setattr(projections, '_fetch_html', lambda url: None)
        assert get_dff_team_mismatches(
            [self.dk_player('Zach Charbonnet', 'NE')]) == []

    def test_no_cache_no_scrape_returns_empty(self, monkeypatch):
        def boom(week=None):
            raise AssertionError('must not scrape')
        monkeypatch.setattr(projections, 'scrape_dailyfantasyfuel', boom)
        assert get_dff_team_mismatches(
            [self.dk_player('Zach Charbonnet', 'NE')],
            allow_scrape=False) == []

    def test_out_entries_excluded_from_pool(self):
        from player_builder import build_player_pool
        draftables = [
            {'player_id': 1, 'name': 'Zach Charbonnet', 'position': 'RB',
             'positions': ['RB'], 'salary': 8200, 'team': 'SEA',
             'game': 'NE @ SEA', 'game_start': None, 'is_disabled': False},
            {'player_id': 2, 'name': 'Kenneth Walker', 'position': 'RB',
             'positions': ['RB'], 'salary': 8600, 'team': 'SEA',
             'game': 'NE @ SEA', 'game_start': None, 'is_disabled': False},
        ]
        fallbacks = {p['player_id']: {'projection': 15.0,
                                       'source': 'fallback'}
                     for p in draftables}
        # Pool-level exclusion: salary fallback must not resurrect an
        # OUT player's projection into a lineup
        pool = build_player_pool(draftables, fallbacks,
                                 exclude=[('Zach Charbonnet', 'SEA')])
        assert [p['name'] for p in pool] == ['Kenneth Walker']

    def test_name_collision_not_excluded(self):
        """League-wide OUT entries must not hit same-named/tokend players.

        Regression (live 2026-09-08): 'Sincere Brown' (LAC, OUT) and
        'Michael Penix Jr.' (ATL, OUT) wrongly excluded 'A.J. Brown' (NE)
        and 'Velus Jones Jr.' / 'Montorie Foster Jr.' (SEA) from the
        NE @ SEA pool via last-name token matching.
        """
        from player_builder import build_player_pool
        draftables = [
            {'player_id': 1, 'name': 'A.J. Brown', 'position': 'WR',
             'positions': ['WR'], 'salary': 9600, 'team': 'NE',
             'game': 'NE @ SEA', 'game_start': None, 'is_disabled': False},
            {'player_id': 2, 'name': 'Velus Jones Jr.', 'position': 'RB',
             'positions': ['RB'], 'salary': 3000, 'team': 'SEA',
             'game': 'NE @ SEA', 'game_start': None, 'is_disabled': False},
            {'player_id': 3, 'name': 'Sincere Brown', 'position': 'WR',
             'positions': ['WR'], 'salary': 1200, 'team': 'LAC',
             'game': 'LAC @ KC', 'game_start': None, 'is_disabled': False},
            {'player_id': 4, 'name': 'Zach Charbonnet', 'position': 'RB',
             'positions': ['RB'], 'salary': 8200, 'team': 'SEA',
             'game': 'NE @ SEA', 'game_start': None, 'is_disabled': False},
        ]
        fallbacks = {p['player_id']: {'projection': 15.0,
                                       'source': 'fallback'}
                     for p in draftables}
        pool = build_player_pool(draftables, fallbacks, exclude=[
            ('Sincere Brown', 'LAC'),          # OUT, other team
            ('Michael Penix Jr.', 'ATL'),      # OUT, 'Jr.' token
            ('Zach Charbonnet', 'SEA'),        # OUT, on this slate
        ])
        names = [p['name'] for p in pool]
        # Wrongly matched before the fix — must stay in the pool
        assert 'A.J. Brown' in names
        assert 'Velus Jones Jr.' in names
        # Legitimately excluded
        assert 'Sincere Brown' not in names
        assert 'Zach Charbonnet' not in names

    def test_same_name_different_team_not_excluded(self):
        """OUT 'A.J. Brown' on PHI must not exclude NE's A.J. Brown."""
        from player_builder import exclude_named_players
        pool = [
            {'name': 'A.J. Brown', 'team': 'NE'},
            {'name': 'A.J. Brown', 'team': 'PHI'},
        ]
        kept, dropped = exclude_named_players(pool, [('A.J. Brown', 'PHI')])
        assert [p['team'] for p in kept] == ['NE']
        assert [p['team'] for p in dropped] == ['PHI']

    def test_manual_bare_name_still_matches_all_teams(self):
        """--exclude 'A.J. Brown' (no team) drops every same-named player."""
        from player_builder import exclude_named_players
        pool = [
            {'name': 'A.J. Brown', 'team': 'NE'},
            {'name': 'A.J. Brown', 'team': 'PHI'},
        ]
        kept, dropped = exclude_named_players(pool, ['A.J. Brown'])
        assert kept == []
        assert len(dropped) == 2


class TestBlueCollarStub:
    def test_frozen_shell_yields_nothing(self):
        """Anonymous page shell (frozen 2026-09-08) has no player data."""
        assert _parse_bluecollar_projections(
            load_fixture('bluecollar_optimizer.html')) == {}

    def test_embedded_json_would_parse(self):
        html = ('<html><body><script>'
                'var data = {"name": "Patrick Mahomes", "projection": "18.2"};'
                '</script></body></html>')
        projections = _parse_bluecollar_projections(html)
        assert projections['patrick mahomes'] == 18.2


class TestBlueCollarAPI:
    """Developer-API path (premium key in BLUECOLLAR_API_KEY, 2026-09)."""

    API_JSON = {
        'slates': [{
            'slate': 'Main', 'slate_type': 'classic', 'date': '09_14_26',
            'info': [
                {'name': 'Patrick Mahomes', 'team': 'KC', 'position': 'QB',
                 'opponent': 'LV', 'projection': '21.4', 'salary': '8200',
                 'value': '2.6'},
                {'name': 'Chiefs', 'team': 'KC', 'position': 'DST',
                 'opponent': 'LV', 'projection': '9.1', 'salary': '3100',
                 'value': '2.9'},
                {'name': 'Bad Row', 'team': 'KC', 'position': 'WR',
                 'opponent': 'LV', 'projection': '--', 'salary': '3000',
                 'value': '0.0'},
            ],
        }],
    }

    def test_parse_api_json(self):
        from projections import _parse_bluecollar_api
        projections = _parse_bluecollar_api(self.API_JSON)
        assert projections['patrick mahomes'] == 21.4
        assert projections['patrick mahomes|kc'] == 21.4
        # DST rows match on team token, no team-suffix key
        assert projections['chiefs'] == 9.1
        assert 'chiefs|kc' not in projections
        # Unparseable projection strings are skipped
        assert 'bad row' not in projections

    def test_no_key_skips_api(self, monkeypatch):
        import projections
        monkeypatch.delenv('BLUECOLLAR_API_KEY', raising=False)
        monkeypatch.delenv('BCDFS_API_KEY', raising=False)
        def boom():
            raise AssertionError('must not call the API without a key')
        monkeypatch.setattr(projections.requests, 'get', boom)
        projections_list, status = projections.fetch_bluecollar_api()
        assert projections_list is None and status is None

    def test_api_error_returns_empty(self, monkeypatch, capsys):
        import projections
        monkeypatch.setenv('BLUECOLLAR_API_KEY', 'bcdfs_live_test')

        class Resp:
            status_code = 401
            text = '{"error": "API key required"}'
            def json(self):
                return {'error': 'API key required'}
        monkeypatch.setattr(projections.requests, 'get',
                            lambda url, headers=None, timeout=None: Resp())
        projections_list, status = projections.fetch_bluecollar_api()
        assert projections_list == {} and status == 401
        assert 'HTTP 401' in capsys.readouterr().out

    def test_scrape_uses_api_when_keyed(self, monkeypatch):
        import projections
        monkeypatch.setenv('BCDFS_API_KEY', 'bcdfs_live_test')
        monkeypatch.setattr(projections, 'fetch_bluecollar_api',
                            lambda timeout=15: (
                                projections._parse_bluecollar_api(
                                    self.API_JSON), 200))
        result = projections.scrape_bluecollar()
        assert result['patrick mahomes'] == 21.4
        assert result['chiefs'] == 9.1


class TestGetSourceProjections:
    @staticmethod
    def make_pool():
        return [
            {'player_id': 1, 'name': 'Patrick Mahomes', 'position': 'QB',
             'positions': ['QB'], 'team': 'KC', 'salary': 8000},
            {'player_id': 2, 'name': 'Patriots DST', 'position': 'DST',
             'positions': ['DST'], 'team': 'NE', 'salary': 3000},
            {'player_id': 3, 'name': 'Unknown Player', 'position': 'WR',
             'positions': ['WR'], 'team': 'BUF', 'salary': 4000},
        ]

    def test_each_source_resolved_independently(self, monkeypatch):
        monkeypatch.setattr('projections.run_all_scrape_fetchers',
                            lambda week=None: {
                                'dailyfantasyfuel': {'patrick mahomes': 17.5},
                                'numberfire': {'patrick mahomes': 16.0},
                            })

        sources = get_source_projections(self.make_pool(), allow_scrape=True)
        assert sources['dailyfantasyfuel'][1]['projection'] == 17.5
        assert sources['numberfire'][1]['projection'] == 16.0
        # Unmatched players fall back to salary-implied within each source
        assert sources['dailyfantasyfuel'][3]['source'] == 'fallback'
        assert sources['numberfire'][3]['projection'] == 10.4

    def test_fallback_source_always_present(self, monkeypatch):
        monkeypatch.setattr('projections.run_all_scrape_fetchers',
                            lambda week=None: {})
        sources = get_source_projections(self.make_pool(), allow_scrape=True)
        assert set(sources.keys()) == {'fallback'}
        assert sources['fallback'][1]['projection'] == 21.6  # 8000*.0022+4

    def test_csv_included_as_source(self, tmp_path, monkeypatch):
        csv_file = tmp_path / "proj.csv"
        csv_file.write_text("name,points\nPatrick Mahomes,25.5\n", encoding='utf-8')
        monkeypatch.setattr('projections.run_all_scrape_fetchers',
                            lambda week=None: {})

        sources = get_source_projections(self.make_pool(), csv_path=str(csv_file),
                                         allow_scrape=True)
        assert sources['csv'][1]['projection'] == 25.5
        assert sources['csv'][2]['source'] == 'fallback'

    def test_no_scrape_skips_fetchers(self, monkeypatch):
        def boom(week=None):
            raise AssertionError("Fetchers must not run with allow_scrape=False")

        monkeypatch.setattr('projections.run_all_scrape_fetchers', boom)
        sources = get_source_projections(self.make_pool(), allow_scrape=False)
        assert set(sources.keys()) == {'fallback'}

    def test_every_source_covers_every_player(self, monkeypatch):
        monkeypatch.setattr('projections.run_all_scrape_fetchers',
                            lambda week=None: {'s1': {'patrick mahomes': 18.0}})
        sources = get_source_projections(self.make_pool(), allow_scrape=True)
        for source, projections in sources.items():
            assert set(projections.keys()) == {1, 2, 3}
            assert all(v['projection'] >= 0 for v in projections.values())
