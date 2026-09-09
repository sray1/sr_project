"""Tests for projection sources, name matching, and fallbacks (projections.py)."""

import os

import pytest

from projections import (
    normalize_name, normalize_dst_name, load_csv_projections,
    salary_fallback_projection, get_player_projections, get_source_projections,
    _match_key,
    _parse_dff_projections, _parse_bluecollar_projections, _strip_injury_tag,
)


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
      </tbody>
    </table>
    """

    def test_mini_table(self):
        projections = _parse_dff_projections(self.MINI_HTML)
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

    def test_no_table_returns_empty(self):
        assert _parse_dff_projections('<html><body><p>hi</p></body></html>') == {}

    def test_missing_detail_header_returns_empty(self):
        # Group-only header row (no POS/NAME) -> layout unrecognized
        html = ('<table><thead><tr><th>PLAYER</th></tr></thead>'
                '<tbody><tr><td>RB</td><td>Jahmyr Gibbs</td></tr></tbody></table>')
        assert _parse_dff_projections(html) == {}

    def test_frozen_fixture(self):
        """Full live page frozen 2026-09-08 (16-game week-1 slate)."""
        projections = _parse_dff_projections(load_fixture('dff_projections.html'))
        assert len(projections) > 800  # ~450 players x (name + name|team) keys
        assert projections['jahmyr gibbs'] == 23.8
        assert projections['patrick mahomes'] == 17.5
        assert projections['travis kelce'] == 11.2
        assert projections['jaguars'] == 7.3

    def test_scraper_registered_first(self):
        from projections import FETCHER_REGISTRY
        assert FETCHER_REGISTRY[0][0] == 'dailyfantasyfuel'
        assert 'bluecollar' in [name for name, _ in FETCHER_REGISTRY]


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
