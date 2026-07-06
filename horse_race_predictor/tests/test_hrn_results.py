"""Tests for the HRN direct results parser (hrn._parse_results_card_html)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sources"))
import hrn  # noqa: E402

# A miniature HRN entries-results page with two races' payouts tables, exercising:
#   - finish order = row order (winner first)
#   - program number from the <img alt>
#   - WPS payoff parsing ($X.XX / "-" -> None)
#   - speed-figure stripping from the horse-name cell
#   - a 4th-place finisher (superfecta leg) with all payoffs "-"
_SAMPLE = """<html><body>
<table class="table table-hrn table-speed"></table>
<table class="table table-hrn table-payouts">
  <thead><tr><th colspan="2">Runner (Speed)</th><th>Win</th><th>Place</th><th>Show</th></tr></thead>
  <tbody>
    <tr><td>War Warrior (92)</td><td><img alt="5" src="x.png"/></td><td>$5.20</td><td>$2.40</td><td>$2.40</td></tr>
    <tr><td>Lodato (91)</td><td><img alt="2" src="x.png"/></td><td>-</td><td>$6.20</td><td>$4.00</td></tr>
    <tr><td>Noahs Pride (92)</td><td><img alt="9" src="x.png"/></td><td>-</td><td>-</td><td>$3.80</td></tr>
    <tr><td>Messagefromtheking (88)</td><td><img alt="3" src="x.png"/></td><td>-</td><td>-</td><td>-</td></tr>
  </tbody>
</table>
<table class="table table-hrn table-exotic-payouts"></table>
<table class="table table-hrn table-payouts">
  <thead><tr><th colspan="2">Runner (Speed)</th><th>Win</th><th>Place</th><th>Show</th></tr></thead>
  <tbody>
    <tr><td>Longshot (IRE) (80)</td><td><img alt="1A" src="x.png"/></td><td>$82.20</td><td>$27.80</td><td>$6.40</td></tr>
    <tr><td>Full Nelson (90*)</td><td><img alt="1" src="x.png"/></td><td>-</td><td>$3.00</td><td>$2.10</td></tr>
  </tbody>
</table>
</body></html>"""


def test_parse_results_card_finish_order_and_payoffs():
    card = hrn._parse_results_card_html(_SAMPLE)
    assert set(card.keys()) == {1, 2}

    r1 = card[1]
    assert [r["finish_position"] for r in r1] == [1, 2, 3, 4]
    assert r1[0]["horse_name"] == "War Warrior"          # speed fig stripped
    assert r1[0]["program_number"] == "5"
    assert r1[0]["win_payoff"] == 5.20
    assert r1[0]["place_payoff"] == 2.40
    assert r1[0]["show_payoff"] == 2.40
    # 2nd: no win payoff, place/show present
    assert r1[1]["win_payoff"] is None
    assert r1[1]["place_payoff"] == 6.20
    # 4th (superfecta leg): all payoffs None
    assert r1[3]["finish_position"] == 4
    assert r1[3]["win_payoff"] is None and r1[3]["show_payoff"] is None


def test_parse_results_card_keeps_country_suffix_and_coupled_prog():
    card = hrn._parse_results_card_html(_SAMPLE)
    r2 = card[2][0]
    # "(IRE)" country suffix must survive; only the trailing (NN) speed fig is stripped.
    assert r2["horse_name"] == "Longshot (IRE)"
    assert r2["program_number"] == "1A"                  # coupled-entry letter kept
    assert r2["win_payoff"] == 82.20


def test_parse_money_handles_dash_and_commas():
    assert hrn._parse_money("-") is None
    assert hrn._parse_money("") is None
    assert hrn._parse_money(None) is None
    assert hrn._parse_money("$1,234.50") == 1234.50
    assert hrn._parse_money("3.40") == 3.40


def test_strip_speed_fig_leaves_non_digit_parens():
    assert hrn._strip_speed_fig("War Warrior (92)") == "War Warrior"
    assert hrn._strip_speed_fig("High Lateen (105*)") == "High Lateen"  # HRN asterisk fig
    assert hrn._strip_speed_fig("Longshot (IRE) (80)") == "Longshot (IRE)"
    assert hrn._strip_speed_fig("Plain") == "Plain"