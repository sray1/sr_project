"""Tests for scratch/MTO/AE detection and active-field filtering."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from race import Entry, filter_active  # noqa: E402
from sources import hrn  # noqa: E402
import consensus  # noqa: E402


# ── HRN parser ───────────────────────────────────────────────────────────

_HRN_FIXTURE = """
<html><body>
<table class="table-entries">
<tr>
  <td data-label="Program Number: 1"><img alt="1"/></td>
  <td data-label="Post Position">1</td>
  <td data-label="Horse / Sire"><h4><a class="horse-link">Alpha Cat</a>
    <span class="small">(87)</span></h4><p>Sire One</p></td>
  <td data-label="Trainer / Jockey"><p>Trainer A</p><p>Jockey A</p></td>
  <td class="table-entries-scratch-col" data-label="Scratched?"></td>
  <td data-label="Morning Line Odds"><p>5/2</p>
    <p class="table-entries-scratch-sm"><abbr title=""></abbr></p></td>
</tr>
<tr>
  <td data-label="Program Number: 2"><img alt="2"/></td>
  <td data-label="Post Position">2</td>
  <td data-label="Horse / Sire"><h4><a class="horse-link">Beta Dog</a></h4>
    <p>Sire Two</p></td>
  <td data-label="Trainer / Jockey"><p>Trainer B</p><p>Jockey B</p></td>
  <td class="table-entries-scratch-col" data-label="Scratched?">(Main Track only)</td>
  <td data-label="Morning Line Odds"><p>9/5 MTO</p><p><abbr title=""></abbr></p></td>
</tr>
<tr>
  <td data-label="Program Number: 3"><img alt="3"/></td>
  <td data-label="Post Position">3</td>
  <td data-label="Horse / Sire"><h4><a class="horse-link">Gamma Elk</a></h4>
    <p>Sire Three</p></td>
  <td data-label="Trainer / Jockey"><p>Trainer C</p><p>Jockey C</p></td>
  <td class="table-entries-scratch-col" data-label="Scratched?">Scratched</td>
  <td data-label="Morning Line Odds"><p>10/1</p><p><abbr title=""></abbr></p></td>
</tr>
<tr>
  <td data-label="Program Number: 4"><img alt="4"/></td>
  <td data-label="Post Position">4</td>
  <td data-label="Horse / Sire"><h4><a class="horse-link">Delta Fox</a></h4>
    <p>Sire Four</p></td>
  <td data-label="Trainer / Jockey"><p>Trainer D</p><p>Jockey D</p></td>
  <td class="table-entries-scratch-col" data-label="Scratched?">Also Eligible</td>
  <td data-label="Morning Line Odds"><p>15/1</p><p><abbr title=""></abbr></p></td>
</tr>
</table>
</body></html>
"""


def test_hrn_parse_entries_html_status_detection():
    entries = hrn._parse_entries_html(_HRN_FIXTURE, race_number=1)
    assert len(entries) == 4
    by_prog = {e["program_number"]: e for e in entries}

    assert by_prog["1"]["horse_name"] == "Alpha Cat"
    assert by_prog["1"]["status"] == "in"
    assert by_prog["1"]["morning_line_odds"] == 2.5
    assert by_prog["1"]["jockey"] == "Jockey A"
    assert by_prog["1"]["trainer"] == "Trainer A"
    assert by_prog["1"]["scratched"] is False

    assert by_prog["2"]["status"] == "mto"
    assert by_prog["2"]["morning_line_odds"] == 1.8

    assert by_prog["3"]["status"] == "scratched"
    assert by_prog["3"]["scratched"] is True

    assert by_prog["4"]["status"] == "ae"


def test_hrn_classify_status_variants():
    cls = hrn._classify_status
    assert cls("", "5/2", None) == "in"
    assert cls("SCR", "8/1", None) == "scratched"
    assert cls("Late Scratch", "8/1", None) == "scratched"
    assert cls("(Main Track only)", "9/5 MTO", None) == "mto"
    assert cls("", "5/2 MTO", None) == "mto"
    assert cls("Also Eligible", "15/1", None) == "ae"
    assert cls("", "15/1 AE", None) == "ae"


def test_hrn_race_number_out_of_range():
    # Only 1 table in the fixture; race 5 should return []
    assert hrn._parse_entries_html(_HRN_FIXTURE, race_number=5) == []


# ── filter_active ────────────────────────────────────────────────────────

def _entries():
    return [
        {"program_number": "1", "horse_name": "Alpha", "status": "in"},
        {"program_number": "2", "horse_name": "Beta", "status": "mto"},
        {"program_number": "3", "horse_name": "Gamma", "status": "scratched"},
        {"program_number": "4", "horse_name": "Delta", "status": "ae"},
        {"program_number": "5", "horse_name": "Epsilon", "status": "in"},
    ]


def test_filter_active_default_excludes_mto_ae_scratched():
    active, excluded = filter_active(_entries())
    active_progs = [e["program_number"] for e in active]
    assert active_progs == ["1", "5"]
    reasons = {e["program_number"]: r for e, r in excluded}
    assert reasons == {"2": "mto", "3": "scratched", "4": "ae"}


def test_filter_active_include_flags():
    active, _ = filter_active(_entries(), include_mto=True, include_ae=True)
    # Still excludes scratched
    assert sorted(e["program_number"] for e in active) == ["1", "2", "4", "5"]


def test_filter_active_extra_scratched_override():
    active, excluded = filter_active(_entries(), extra_scratched=["1"])
    assert "1" not in [e["program_number"] for e in active]
    reasons = {e["program_number"]: r for e, r in excluded}
    assert reasons["1"] == "scratched"


# ── consensus on active field excludes scratched ──────────────────────────

def test_consensus_only_sees_active_entries():
    # Entry objects: #1 in, #2 scratched, #3 in
    entries = [
        Entry("1", "Alpha", morning_line_odds=5.0, status="in"),
        Entry("2", "Beta", morning_line_odds=2.0, status="scratched"),
        Entry("3", "Gamma", morning_line_odds=3.0, status="in"),
    ]
    active, _ = filter_active(entries)
    picks = [
        {"source": "A", "horse_name": "Beta", "program_number": "2", "rank": 1},
        {"source": "A", "horse_name": "Alpha", "program_number": "1", "rank": 2},
    ]
    # If the caller passes only the active field, Beta's pick won't match
    result = consensus.aggregate(active, picks)
    by_prog = {r["program_number"]: r for r in result["rows"]}
    assert "2" not in by_prog  # scratched horse not in active rows
    assert by_prog["1"]["points"] == 3  # Alpha got the #2 vote (3 pts)
    # Beta's pick is unmatched against the active field
    assert len(result["unmatched_picks"]) == 1