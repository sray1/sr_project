"""Tests for the accuracy module."""

import os
import sys
import sqlite3
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import db
import accuracy
import price_fetcher


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temporary database for each test."""
    temp_db_path = str(tmp_path / "test_accuracy.db")
    monkeypatch.setattr(db, "DB_PATH", temp_db_path)
    db.init_db()
    yield temp_db_path


class TestAccuracyRatings:
    """Test that accuracy ratings are computed correctly."""

    def test_hit_within_5_percent(self):
        sid = db.save_symbol("AAPL")
        tid = db.save_target_price(sid, "test", 100.0, date_posted="2026-01-01")
        db.save_accuracy_snapshot(tid, sid, 30, actual_price=104.0, target_price=100.0)
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        assert snapshots[0]["accuracy_rating"] == "hit"
        assert abs(snapshots[0]["pct_diff"] - 4.0) < 0.1

    def test_hit_exactly_5_percent(self):
        sid = db.save_symbol("MSFT")
        tid = db.save_target_price(sid, "test", 100.0, date_posted="2026-01-01")
        db.save_accuracy_snapshot(tid, sid, 30, actual_price=105.0, target_price=100.0)
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        assert snapshots[0]["accuracy_rating"] == "hit"

    def test_miss_low_over_5_percent(self):
        sid = db.save_symbol("GOOGL")
        tid = db.save_target_price(sid, "test", 100.0, date_posted="2026-01-01")
        db.save_accuracy_snapshot(tid, sid, 30, actual_price=110.0, target_price=100.0)
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        assert snapshots[0]["accuracy_rating"] == "miss_low"

    def test_miss_high_under_5_percent(self):
        sid = db.save_symbol("TSLA")
        tid = db.save_target_price(sid, "test", 100.0, date_posted="2026-01-01")
        db.save_accuracy_snapshot(tid, sid, 30, actual_price=90.0, target_price=100.0)
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        assert snapshots[0]["accuracy_rating"] == "miss_high"


class TestNeedsAccuracyCheck:
    """Test that targets are correctly identified as needing accuracy checks."""

    def test_old_target_needs_check(self):
        sid = db.save_symbol("OLDSTOCK")
        # Date far in the past → should need all checkpoints
        db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo", date_posted="2024-01-01")
        needing = db.get_symbols_needing_accuracy_check(checkpoint_days=30)
        assert any(n["symbol"] == "OLDSTOCK" for n in needing)

    def test_recent_target_does_not_need_30day(self):
        sid = db.save_symbol("NEWSTOCK")
        # Today's date → not yet 30 days old
        today = datetime.now().strftime('%Y-%m-%d')
        db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo", date_posted=today)
        needing = db.get_symbols_needing_accuracy_check(checkpoint_days=30)
        assert not any(n["symbol"] == "NEWSTOCK" for n in needing)

    def test_checked_target_not_returned(self):
        sid = db.save_symbol("CHECKED")
        tid = db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo", date_posted="2024-01-01")
        # Manually create a snapshot so it's already checked
        db.save_accuracy_snapshot(tid, sid, 30, actual_price=105.0, target_price=100.0)
        needing = db.get_symbols_needing_accuracy_check(checkpoint_days=30)
        assert not any(n["symbol"] == "CHECKED" for n in needing)


class TestCheckpointPriceProximity:
    """get_closest_price returns the latest price <= checkpoint_date, which can
    be a different checkpoint's price far away (actual_prices is sparse).
    compute_accuracy_for_target must reject a cached price that is far from the
    checkpoint and fetch the actual checkpoint-date price instead, and must use
    a freshly fetched price's 'close' (not only a saved row's 'close_price').
    """

    def test_stale_cached_price_is_rejected_and_fetched(self, monkeypatch):
        sid = db.save_symbol("STALE")
        # target issued 2024-01-01 -> 180-day checkpoint = 2024-06-29
        db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo",
                             date_posted="2024-01-01")
        # stale cached price 5 months before the 180-day checkpoint
        db.save_actual_price(sid, price_date="2024-02-01", close_price=50.0)
        calls = []

        def fake_fetch(symbol, d):
            calls.append((symbol, d))
            return {"price_date": "2024-06-28", "open": 1.0, "close": 110.0,
                    "high": 1.0, "low": 1.0, "volume": 1}

        monkeypatch.setattr(price_fetcher, "fetch_price_on_date", fake_fetch)
        accuracy.run_accuracy_checks(checkpoint_days=180)

        snaps = db.get_accuracy_snapshots(symbol_id=sid)
        assert snaps, "expected a 180-day snapshot"
        s = snaps[0]
        assert s["checkpoint_days"] == 180
        # used the fresh fetch (110.0), not the stale 50.0
        assert s["actual_price"] == 110.0
        assert calls, "expected fetch_price_on_date to be called for the stale case"

    def test_close_cached_price_is_used_no_fetch(self, monkeypatch):
        sid = db.save_symbol("CLOSE")
        db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo",
                             date_posted="2024-01-01")
        # cached price 3 days before the 180-day checkpoint (2024-06-29) -> within tolerance
        db.save_actual_price(sid, price_date="2024-06-26", close_price=107.0)
        calls = []
        monkeypatch.setattr(price_fetcher, "fetch_price_on_date",
                            lambda *a, **k: calls.append(1) or None)
        accuracy.run_accuracy_checks(checkpoint_days=180)

        s = db.get_accuracy_snapshots(symbol_id=sid)[0]
        assert s["checkpoint_days"] == 180
        assert s["actual_price"] == 107.0  # used the cached price
        assert calls == [], "should not fetch when a close cached price exists"