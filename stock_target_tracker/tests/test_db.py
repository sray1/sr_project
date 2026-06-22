"""Tests for the database module."""

import os
import sys
import sqlite3
import tempfile
import pytest

# Add module directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temporary database for each test."""
    temp_db_path = str(tmp_path / "test_stock_tracker.db")
    monkeypatch.setattr(db, "DB_PATH", temp_db_path)
    db.init_db()
    yield temp_db_path


class TestInitDb:
    def test_creates_tables(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        assert "symbols" in tables
        assert "target_prices" in tables
        assert "actual_prices" in tables
        assert "accuracy_snapshots" in tables

    def test_idempotent(self, temp_db):
        # Calling init_db twice should not raise
        db.init_db()
        db.init_db()


class TestSymbols:
    def test_save_and_get_symbol(self):
        sid = db.save_symbol("AAPL", company_name="Apple Inc.", sector="Technology")
        assert sid is not None
        symbols = db.get_symbols()
        assert len(symbols) >= 1
        assert any(s["symbol"] == "AAPL" for s in symbols)

    def test_save_duplicate_symbol_returns_same_id(self):
        sid1 = db.save_symbol("MSFT")
        sid2 = db.save_symbol("MSFT")
        assert sid1 == sid2

    def test_save_updates_company_name(self):
        sid1 = db.save_symbol("GOOGL")
        sid2 = db.save_symbol("GOOGL", company_name="Alphabet Inc.")
        assert sid1 == sid2
        symbols = db.get_symbols()
        googl = [s for s in symbols if s["symbol"] == "GOOGL"][0]
        assert googl["company_name"] == "Alphabet Inc."

    def test_get_symbol_id(self):
        sid = db.save_symbol("TSLA")
        found = db.get_symbol_id("TSLA")
        assert found == sid

    def test_get_symbol_id_not_found(self):
        assert db.get_symbol_id("NONEXISTENT") is None


class TestTargetPrices:
    def test_save_and_get_target_price(self):
        sid = db.save_symbol("AAPL")
        tid = db.save_target_price(
            symbol_id=sid, source="yahoo_finance", target_price=185.0,
            rating="buy", analyst_firm="Morgan Stanley", date_posted="2026-05-15"
        )
        assert tid is not None
        targets = db.get_target_prices(symbol_id=sid)
        assert len(targets) >= 1
        assert targets[0]["target_price"] == 185.0

    def test_upsert_target_price(self):
        sid = db.save_symbol("AAPL")
        tid1 = db.save_target_price(
            symbol_id=sid, source="fmp", target_price=180.0,
            analyst_firm="Goldman", date_posted="2026-05-01"
        )
        tid2 = db.save_target_price(
            symbol_id=sid, source="fmp", target_price=190.0,
            analyst_firm="Goldman", date_posted="2026-05-01"
        )
        assert tid1 == tid2  # Same record, updated
        targets = db.get_target_prices(symbol_id=sid, source="fmp")
        assert targets[0]["target_price"] == 190.0


class TestActualPrices:
    def test_save_and_get_actual_price(self):
        sid = db.save_symbol("AAPL")
        pid = db.save_actual_price(
            symbol_id=sid, price_date="2026-06-14",
            open_price=188.0, close_price=189.5, high_price=190.0,
            low_price=187.5, volume=50000000
        )
        assert pid is not None
        prices = db.get_actual_prices(sid)
        assert len(prices) >= 1

    def test_upsert_actual_price(self):
        sid = db.save_symbol("MSFT")
        pid1 = db.save_actual_price(sid, "2026-06-14", close_price=420.0)
        pid2 = db.save_actual_price(sid, "2026-06-14", close_price=425.0)
        assert pid1 == pid2
        prices = db.get_actual_prices(sid)
        assert prices[0]["close_price"] == 425.0

    def test_get_closest_price(self):
        sid = db.save_symbol("NVDA")
        db.save_actual_price(sid, "2026-06-12", close_price=130.0)
        db.save_actual_price(sid, "2026-06-13", close_price=132.0)
        result = db.get_closest_price(sid, "2026-06-13")
        assert result["close_price"] == 132.0
        # Asking for a future date should return the latest available
        result2 = db.get_closest_price(sid, "2026-06-15")
        assert result2["close_price"] == 132.0


class TestAccuracySnapshots:
    def test_save_and_get_snapshot(self):
        sid = db.save_symbol("AAPL")
        tid = db.save_target_price(sid, "yahoo_finance", 185.0, date_posted="2026-05-01")
        snap_id = db.save_accuracy_snapshot(
            target_price_id=tid, symbol_id=sid, checkpoint_days=30,
            actual_price=190.0, target_price=185.0
        )
        assert snap_id is not None
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        assert len(snapshots) >= 1

    def test_accuracy_rating_hit(self):
        sid = db.save_symbol("AAPL")
        tid = db.save_target_price(sid, "fmp", 185.0, date_posted="2026-05-01")
        snap_id = db.save_accuracy_snapshot(
            target_price_id=tid, symbol_id=sid, checkpoint_days=30,
            actual_price=187.0, target_price=185.0
        )
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        # Within 5% → hit
        assert snapshots[0]["accuracy_rating"] == "hit"

    def test_accuracy_rating_miss_low(self):
        sid = db.save_symbol("AAPL")
        tid = db.save_target_price(sid, "fmp", 185.0, date_posted="2026-05-01")
        db.save_accuracy_snapshot(
            target_price_id=tid, symbol_id=sid, checkpoint_days=30,
            actual_price=200.0, target_price=185.0
        )
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        assert snapshots[0]["accuracy_rating"] == "miss_low"

    def test_accuracy_rating_miss_high(self):
        sid = db.save_symbol("AAPL")
        tid = db.save_target_price(sid, "fmp", 185.0, date_posted="2026-05-01")
        db.save_accuracy_snapshot(
            target_price_id=tid, symbol_id=sid, checkpoint_days=30,
            actual_price=170.0, target_price=185.0
        )
        snapshots = db.get_accuracy_snapshots(symbol_id=sid)
        assert snapshots[0]["accuracy_rating"] == "miss_high"

    def test_get_symbols_needing_accuracy_check(self):
        # Insert a target old enough for a 30-day check
        sid = db.save_symbol("TEST1")
        db.save_target_price(
            sid, "yahoo_finance", 100.0,
            analyst_firm="Test Firm", date_posted="2025-01-01"
        )
        needing = db.get_symbols_needing_accuracy_check(checkpoint_days=30)
        assert len(needing) >= 1
        assert any(n["symbol"] == "TEST1" for n in needing)