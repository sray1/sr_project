"""Tests for projection sources, name matching, and fallbacks (projections.py)."""

import pytest

from projections import (
    normalize_name, normalize_dst_name, load_csv_projections,
    salary_fallback_projection, get_player_projections, _match_key,
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