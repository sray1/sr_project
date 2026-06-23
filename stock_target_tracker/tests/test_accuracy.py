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


class TestEverHit:
    """Whole-window ever-hit (TPMetANY) flag: did the price touch the target
    at any point in the 365-day window?"""

    def test_touch_via_intraday_high(self):
        # Day's low..high straddles the target -> touched.
        hist = [{"price_date": "2026-01-05", "low": 95, "high": 105, "close": 100}]
        res = accuracy.compute_ever_hit(hist, "2026-01-01", 100.0)
        assert res["ever_hit"] is True
        assert res["first_hit_date"] == "2026-01-05"
        assert res["days_to_hit"] == 4

    def test_touch_from_above_bearish_target(self):
        # Price starts above the target and crosses down through it; the day
        # straddling 100 (low=99, high=101) is the touch. Direction-agnostic.
        hist = [
            {"price_date": "2026-01-03", "low": 108, "high": 112, "close": 110},
            {"price_date": "2026-01-04", "low": 99, "high": 101, "close": 100},
        ]
        res = accuracy.compute_ever_hit(hist, "2026-01-01", 100.0)
        assert res["ever_hit"] is True
        assert res["first_hit_date"] == "2026-01-04"

    def test_no_touch_returns_false(self):
        # Every day's range sits entirely above the target -> never touched,
        # but there IS usable in-window data, so this is a False verdict (not None).
        hist = [{"price_date": "2026-01-05", "low": 120, "high": 130, "close": 125}]
        res = accuracy.compute_ever_hit(hist, "2026-01-01", 100.0)
        assert res["ever_hit"] is False
        assert res["first_hit_date"] is None
        assert res["days_to_hit"] is None

    def test_no_in_window_data_returns_none(self):
        # History exists but nothing falls inside the window -> can't verdict.
        hist = [{"price_date": "2027-06-05", "low": 95, "high": 105, "close": 100}]
        res = accuracy.compute_ever_hit(hist, "2026-01-01", 100.0)
        assert res is None

    def test_window_clamped_to_today(self):
        # date_posted + 365 is in the future; window end should be today, and a
        # touch after today must not exist. A touch within the clamped window hits.
        today = datetime.now().strftime('%Y-%m-%d')
        hist = [{"price_date": today, "low": 95, "high": 105, "close": 100}]
        res = accuracy.compute_ever_hit(hist, "2026-01-01", 100.0)
        assert res["ever_hit"] is True
        assert res["first_hit_date"] == today

    def test_update_persists_hit_and_is_sticky(self, monkeypatch):
        sid = db.save_symbol("HITSTOCK")
        tid = db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo",
                                   date_posted="2026-01-01")
        hist = [{"price_date": "2026-01-10", "low": 95, "high": 105, "close": 100}]
        monkeypatch.setattr(price_fetcher, "fetch_price_history",
                            lambda *a, **k: hist)

        stats = accuracy.update_ever_hit_flags()
        assert stats["evaluated"] == 1
        assert stats["hit"] == 1

        needing = db.get_targets_needing_ever_hit()
        assert not any(t["target_price_id"] == tid for t in needing), \
            "a hit is sticky and must not be re-evaluated"

    def test_miss_is_reevaluated_while_window_open(self, monkeypatch):
        sid = db.save_symbol("MISSSTOCK")
        tid = db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo",
                                   date_posted="2026-01-01")  # window open until 2027-01-01
        # First pass: no touch -> ever_hit = 0.
        monkeypatch.setattr(price_fetcher, "fetch_price_history",
                            lambda *a, **k: [{"price_date": "2026-01-10",
                                              "low": 120, "high": 130, "close": 125}])
        accuracy.update_ever_hit_flags()
        due = db.get_targets_needing_ever_hit()
        assert any(t["target_price_id"] == tid for t in due), \
            "a 0 with an open window must stay eligible for re-evaluation"

        # Second pass: price now touches -> flips to 1.
        monkeypatch.setattr(price_fetcher, "fetch_price_history",
                            lambda *a, **k: [{"price_date": "2026-02-10",
                                              "low": 95, "high": 105, "close": 100}])
        stats = accuracy.update_ever_hit_flags()
        assert stats["hit"] == 1
        # Now sticky — no longer eligible.
        due = db.get_targets_needing_ever_hit()
        assert not any(t["target_price_id"] == tid for t in due)

    def test_no_history_skips_without_stamping_false(self, monkeypatch):
        sid = db.save_symbol("NODATA")
        tid = db.save_target_price(sid, "test", 100.0, analyst_firm="TestCo",
                                   date_posted="2026-01-01")
        monkeypatch.setattr(price_fetcher, "fetch_price_history",
                            lambda *a, **k: [])
        stats = accuracy.update_ever_hit_flags()
        assert stats["evaluated"] == 0
        assert stats["errors"] == 1
        # Still eligible (not stamped 0) so a later run with data can verdict.
        due = db.get_targets_needing_ever_hit()
        assert any(t["target_price_id"] == tid for t in due)

    def test_undated_target_is_never_eligible(self):
        sid = db.save_symbol("UNDATED")
        db.save_target_price(sid, "yahoo_finance", 100.0, analyst_firm="Consensus",
                             date_posted=None)
        due = db.get_targets_needing_ever_hit()
        assert not any(t["symbol"] == "UNDATED" for t in due)


class TestEverHitMigration:
    """The ever_hit columns must be added to target_prices on existing DBs."""

    def test_columns_added_on_existing_db(self, tmp_path, monkeypatch):
        # Build a DB with the OLD schema (no ever_hit columns) by creating the
        # table manually, then run init_db and confirm the migration adds them.
        path = str(tmp_path / "old_schema.db")
        monkeypatch.setattr(db, "DB_PATH", path)
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE target_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol_id INTEGER,
            source TEXT, analyst_name TEXT, analyst_firm TEXT,
            target_price REAL, rating TEXT, date_posted TEXT,
            fetched_at TEXT, raw_data_json TEXT)""")
        conn.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, symbol TEXT)")
        conn.commit()
        conn.close()

        db.init_db()  # should add the missing columns

        conn = sqlite3.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(target_prices)")}
        conn.close()
        assert "ever_hit" in cols
        assert "first_hit_date" in cols
        assert "days_to_hit" in cols
        assert "ever_hit_eval_at" in cols