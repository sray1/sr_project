"""Tests for expert_import.py (transcribed expert-lineup import CLI)."""

import pytest

from expert_import import parse_picks


class TestParsePicks:
    def test_flex_pick(self):
        assert parse_picks('Jahmyr Gibbs $12,000') == \
            [('Jahmyr Gibbs', 12000, False)]

    def test_cpt_pick(self):
        assert parse_picks('CPT Josh Allen $17100') == \
            [('Josh Allen', 17100, True)]

    def test_lowercase_cpt_prefix(self):
        assert parse_picks('cpt Josh Allen $17100')[0][2] is True

    def test_semicolon_separated(self):
        picks = parse_picks('CPT Josh Allen $17100; Jahmyr Gibbs $12,000; '
                            'DJ Moore $7,800')
        assert picks == [('Josh Allen', 17100, True),
                         ('Jahmyr Gibbs', 12000, False),
                         ('DJ Moore', 7800, False)]

    def test_blank_tokens_skipped(self):
        picks = parse_picks('Drake Maye $10,000;;')
        assert picks == [('Drake Maye', 10000, False)]

    def test_apostrophe_name(self):
        assert parse_picks("Ja'Marr Chase $7,800") == \
            [("Ja'Marr Chase", 7800, False)]

    def test_malformed_pick_raises(self):
        with pytest.raises(ValueError):
            parse_picks('Josh Allen 17100')  # no $ marker

    def test_salary_only_raises(self):
        with pytest.raises(ValueError):
            parse_picks('$17100')