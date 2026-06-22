"""Tests for the report generator (pure helpers + no-target edge case)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import report
import db as db_mod


class TestFmtSig3:
    def test_three_sig_digits(self):
        assert report._fmt_sig3(53.37) == "53.4"
        assert report._fmt_sig3(28.53) == "28.5"
        assert report._fmt_sig3(36.4) == "36.4"

    def test_small_values_keep_sig_figs(self):
        assert report._fmt_sig3(4.8) == "4.8"
        assert report._fmt_sig3(2.1) == "2.1"

    def test_zero_and_none(self):
        assert report._fmt_sig3(0) == "0"
        assert report._fmt_sig3(0.0) == "0"
        assert report._fmt_sig3(None) == "0"

    def test_negative_and_large(self):
        assert report._fmt_sig3(-10.05) == "-10.1"
        assert report._fmt_sig3(945) == "945"


class TestPriceRangeFor:
    HIST = [
        {"price_date": "2024-09-25", "low": 170.0, "high": 175.0, "close": 172.0},
        {"price_date": "2024-10-01", "low": 168.32, "high": 180.0, "close": 179.0},
        {"price_date": "2025-03-10", "low": 200.0, "high": 258.45, "close": 255.0},
        {"price_date": "2025-09-19", "low": 240.0, "high": 250.0, "close": 245.0},
        {"price_date": "2025-12-01", "low": 260.0, "high": 270.0, "close": 265.0},  # >360d
    ]

    def test_range_within_window(self):
        # Window = 2024-09-25 + 360d = 2025-09-19 (inclusive). Excludes 2025-12-01.
        r = report._price_range_for(self.HIST, "2024-09-25")
        assert r is not None
        assert r["low"] == 168.32
        assert r["high"] == 258.45
        assert r["start"] == "2024-09-25"
        assert r["end"] == "2025-09-19"
        assert r["n_points"] == 4

    def test_window_boundary_inclusive(self):
        # A point exactly at start+360 days is included; one past it is excluded.
        # Using a past start so the "clamp to today" branch is a no-op (deterministic).
        from datetime import datetime, timedelta
        start = "2020-01-01"
        end = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=360)).strftime("%Y-%m-%d")
        hist = [
            {"price_date": "2020-06-01", "low": 100.0, "high": 110.0, "close": 105.0},
            {"price_date": end, "low": 120.0, "high": 130.0, "close": 125.0},  # at boundary
            {"price_date": "2025-01-01", "low": 200.0, "high": 210.0, "close": 205.0},  # past window
        ]
        r = report._price_range_for(hist, start)
        assert r is not None
        assert r["n_points"] == 2  # boundary point kept, the far one dropped
        assert r["low"] == 100.0
        assert r["high"] == 130.0
        assert r["end"] == end

    def test_recent_prediction_clamps_future_window_end(self):
        # A start so recent that start+360d is in the future: the window end is
        # clamped to today, so any fixture point after today is excluded even if
        # it falls within the unclamped 360-day span.
        from datetime import datetime, timedelta
        start_dt = datetime.now() - timedelta(days=30)
        start = start_dt.strftime("%Y-%m-%d")
        future_after_today = (datetime.now() + timedelta(days=200)).strftime("%Y-%m-%d")
        hist = [
            {"price_date": start, "low": 50.0, "high": 52.0, "close": 51.0},
            {"price_date": future_after_today, "low": 99.0, "high": 101.0, "close": 100.0},
        ]
        r = report._price_range_for(hist, start)
        assert r is not None
        assert r["n_points"] == 1  # the future-after-today point is excluded
        assert r["end"] == start

    def test_no_data_in_window(self):
        r = report._price_range_for(self.HIST, "2026-01-01")
        assert r is None

    def test_missing_start_or_history(self):
        assert report._price_range_for(self.HIST, None) is None
        assert report._price_range_for([], "2024-09-25") is None
        assert report._price_range_for(self.HIST, "not-a-date") is None


class TestMethodologyFor:
    def test_consensus_org_gets_formula(self):
        r = report._methodology_for("Yahoo Finance Consensus")
        assert r["org_type"] == "Consensus"
        assert "AVG(target_price)" in r["methodology"]
        assert "mean" in r["methodology"].lower()

    def test_case_insensitive_consensus(self):
        assert report._methodology_for("Some consensus target")["org_type"] == "Consensus"

    def test_individual_firm_gets_general_description(self):
        r = report._methodology_for("Morgan Stanley")
        assert r["org_type"] == "Analyst firm"
        assert "DCF" in r["methodology"]
        assert "not publicly disclosed" in r["methodology"]
        assert "proprietary" in r["methodology"].lower()

    def test_empty_or_unknown_firm(self):
        assert report._methodology_for("")["org_type"] == "Analyst firm"
        assert report._methodology_for(None)["org_type"] == "Analyst firm"


class TestWholeWindowStats:
    """Whole-window measures (Met_any, days-to-hit, within-band %, bias) over
    the full [date_posted, +365d] window, clamped to today."""

    def test_met_any_and_aggregates(self):
        hist = [
            {"price_date": "2024-01-10", "low": 95.0, "high": 100.0, "close": 98.0},
            {"price_date": "2024-02-15", "low": 105.0, "high": 112.0, "close": 111.0},
            {"price_date": "2024-06-01", "low": 100.0, "high": 108.0, "close": 104.0},
            {"price_date": "2024-09-01", "low": 108.0, "high": 115.0, "close": 113.0},
        ]
        r = report._whole_window_stats(hist, "2024-01-01", 110.0)
        assert r is not None
        assert r["met_any"] is True
        assert r["first_hit_date"] == "2024-02-15"
        assert r["days_to_hit"] == 45
        assert r["within_band_pct"] == 50.0   # closes 111 & 113 within +/-5%
        assert r["mean_signed_pct"] == -3.2   # targets mostly sat above price
        assert r["n_days"] == 4
        assert r["window_end"] == "2024-09-01"

    def test_never_touched(self):
        hist = [
            {"price_date": "2024-01-10", "low": 80.0, "high": 90.0, "close": 85.0},
            {"price_date": "2024-06-01", "low": 75.0, "high": 95.0, "close": 90.0},
        ]
        r = report._whole_window_stats(hist, "2024-01-01", 110.0)
        assert r["met_any"] is False
        assert r["first_hit_date"] is None
        assert r["days_to_hit"] is None

    def test_no_data_in_window_returns_none(self):
        hist = [{"price_date": "2024-01-10", "low": 95.0, "high": 100.0, "close": 98.0}]
        # Window starts after all available data.
        r = report._whole_window_stats(hist, "2025-01-01", 110.0)
        assert r is None

    def test_missing_inputs_return_none(self):
        hist = [{"price_date": "2024-01-10", "low": 95.0, "high": 100.0, "close": 98.0}]
        assert report._whole_window_stats(hist, None, 110.0) is None
        assert report._whole_window_stats([], "2024-01-01", 110.0) is None
        assert report._whole_window_stats(hist, "2024-01-01", 0) is None
        assert report._whole_window_stats(hist, "2024-01-01", None) is None
        assert report._whole_window_stats(hist, "not-a-date", 110.0) is None

    def test_recent_prediction_clamps_window_to_today(self):
        from datetime import datetime, timedelta
        start_dt = datetime.now() - timedelta(days=30)
        start = start_dt.strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=200)).strftime("%Y-%m-%d")
        hist = [
            {"price_date": start, "low": 50.0, "high": 55.0, "close": 52.0},
            {"price_date": future, "low": 99.0, "high": 101.0, "close": 100.0},
        ]
        r = report._whole_window_stats(hist, start, 52.0)
        assert r is not None
        # The future point is past today, so the today-clamp drops it.
        assert r["n_days"] == 1


class TestNoTargetSymbol:
    """A tracked symbol with no analyst targets must not break the report and
    must surface as target_count == 0 with empty analyst lists."""

    def test_symbol_with_no_targets(self, tmp_path, monkeypatch):
        # Redirect the DB to a temp file so we don't touch the real one.
        tmp_db = tmp_path / "test_report.db"
        monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_db))
        db_mod.init_db()
        db_mod.save_symbol("NOTGT", company_name="No Targets Inc.", sector="Test")

        data = report._fetch_report_data()

        sym = next(s for s in data["symbols"] if s["symbol"] == "NOTGT")
        assert sym["target_count"] == 0
        assert sym["snapshot_count"] == 0
        assert sym["source_consensus"] == []
        assert sym["best_analysts"] == []
        assert sym["worst_analysts"] == []

        # Overall panels are empty (no snapshots anywhere).
        assert data["most_accurate_analysts"] == []
        assert data["least_accurate_analysts"] == []