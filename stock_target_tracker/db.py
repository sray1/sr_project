"""
SQLite database for tracking stock analyst target prices and accuracy over time.

Stores symbols, analyst target prices (from multiple sources), actual stock
prices, and accuracy snapshots at multiple checkpoints (30/90/180/365 days) so
analyst prediction accuracy can be measured across sources and time horizons.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone

# Database file path (same directory as this module)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_tracker.db")


def get_connection():
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create database tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            company_name TEXT,
            sector TEXT,
            is_active INTEGER DEFAULT 1,
            added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_symbols_symbol
        ON symbols(symbol)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS target_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            analyst_name TEXT,
            analyst_firm TEXT,
            target_price REAL NOT NULL,
            rating TEXT,
            date_posted TEXT,
            fetched_at TEXT NOT NULL,
            raw_data_json TEXT,
            FOREIGN KEY (symbol_id) REFERENCES symbols(id),
            UNIQUE(symbol_id, source, analyst_firm, date_posted)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS actual_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol_id INTEGER NOT NULL,
            price_date TEXT NOT NULL,
            open_price REAL,
            close_price REAL,
            high_price REAL,
            low_price REAL,
            volume INTEGER,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY (symbol_id) REFERENCES symbols(id),
            UNIQUE(symbol_id, price_date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accuracy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_price_id INTEGER NOT NULL,
            symbol_id INTEGER NOT NULL,
            checkpoint_days INTEGER NOT NULL,
            actual_price REAL,
            target_price REAL NOT NULL,
            price_diff REAL,
            pct_diff REAL,
            accuracy_rating TEXT,
            snapshot_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (target_price_id) REFERENCES target_prices(id),
            FOREIGN KEY (symbol_id) REFERENCES symbols(id),
            UNIQUE(target_price_id, checkpoint_days)
        )
    """)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


# ── Symbol CRUD ──────────────────────────────────────────────────────────

def save_symbol(symbol, company_name=None, sector=None):
    """Save a stock symbol. Returns the symbol_id.

    If the symbol already exists, updates company_name and sector if provided.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute("SELECT id FROM symbols WHERE symbol = ?", (symbol,))
    row = cursor.fetchone()

    if row:
        symbol_id = row["id"]
        if company_name or sector:
            cursor.execute(
                "UPDATE symbols SET company_name = COALESCE(?, company_name), "
                "sector = COALESCE(?, sector), updated_at = ? WHERE id = ?",
                (company_name, sector, now, symbol_id)
            )
            conn.commit()
        conn.close()
        return symbol_id

    cursor.execute(
        "INSERT INTO symbols (symbol, company_name, sector, added_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (symbol, company_name, sector, now, now)
    )
    conn.commit()
    symbol_id = cursor.lastrowid
    conn.close()
    return symbol_id


def get_symbols(active_only=True):
    """Get all tracked symbols.

    Returns list of dicts with symbol info.
    """
    conn = get_connection()
    cursor = conn.cursor()

    if active_only:
        cursor.execute("SELECT * FROM symbols WHERE is_active = 1 ORDER BY symbol")
    else:
        cursor.execute("SELECT * FROM symbols ORDER BY symbol")

    symbols = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return symbols


def get_symbol_id(symbol):
    """Get symbol_id for a given ticker symbol string. Returns None if not found."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM symbols WHERE symbol = ?", (symbol,))
    row = cursor.fetchone()
    conn.close()
    return row["id"] if row else None


# ── Target Price CRUD ────────────────────────────────────────────────────

def save_target_price(symbol_id, source, target_price, rating=None,
                      analyst_name=None, analyst_firm=None, date_posted=None,
                      raw_data=None):
    """Save an analyst target price. Upserts on (symbol_id, source, analyst_firm, date_posted).

    Returns the target_price id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    raw_json = json.dumps(raw_data) if raw_data else None

    # Use COALESCE for analyst_firm so NULL firms still work with UNIQUE constraint
    # SQLite treats NULL != NULL in UNIQUE indexes, so we use empty string for NULLs
    firm_key = analyst_firm if analyst_firm else ""

    # Undated targets (date_posted IS NULL — e.g. Yahoo Finance consensus
    # mean/high/low, which have no issue date) would otherwise never match
    # (col = NULL is always false), so every re-fetch inserted a fresh duplicate
    # row. For those, dedupe in place on (symbol, source, firm, rating) keeping
    # the single existing row and updating its price — one current target per
    # (symbol, source, firm, rating), refreshed each fetch instead of growing.
    if date_posted is None:
        rating_key = rating if rating else ""
        cursor.execute(
            "SELECT id FROM target_prices "
            "WHERE symbol_id = ? AND source = ? AND "
            "COALESCE(analyst_firm, '') = ? AND COALESCE(rating, '') = ? "
            "AND date_posted IS NULL "
            "ORDER BY fetched_at DESC LIMIT 1",
            (symbol_id, source, firm_key, rating_key)
        )
    else:
        cursor.execute(
            "SELECT id FROM target_prices "
            "WHERE symbol_id = ? AND source = ? AND "
            "COALESCE(analyst_firm, '') = ? AND date_posted = ?",
            (symbol_id, source, firm_key, date_posted)
        )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE target_prices SET target_price = ?, rating = COALESCE(?, rating), "
            "analyst_name = COALESCE(?, analyst_name), raw_data_json = COALESCE(?, raw_data_json), "
            "fetched_at = ? WHERE id = ?",
            (target_price, rating, analyst_name, raw_json, now, row["id"])
        )
        conn.commit()
        result = row["id"]
    else:
        cursor.execute(
            "INSERT INTO target_prices "
            "(symbol_id, source, analyst_name, analyst_firm, target_price, rating, "
            "date_posted, fetched_at, raw_data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol_id, source, analyst_name, analyst_firm, target_price, rating,
             date_posted, now, raw_json)
        )
        conn.commit()
        result = cursor.lastrowid

    conn.close()
    return result


def get_target_prices(symbol_id=None, source=None, limit=100):
    """Get target prices, optionally filtered by symbol_id or source.

    Returns list of dicts.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT tp.*, s.symbol, s.company_name
        FROM target_prices tp
        JOIN symbols s ON tp.symbol_id = s.id
        WHERE 1=1
    """
    params = []

    if symbol_id:
        query += " AND tp.symbol_id = ?"
        params.append(symbol_id)
    if source:
        query += " AND tp.source = ?"
        params.append(source)

    query += " ORDER BY tp.date_posted DESC, tp.fetched_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]

    # Parse raw_data_json
    for r in results:
        if r.get("raw_data_json"):
            r["raw_data"] = json.loads(r["raw_data_json"])
        else:
            r["raw_data"] = None
        del r["raw_data_json"]

    conn.close()
    return results


# ── Actual Price CRUD ────────────────────────────────────────────────────

def save_actual_price(symbol_id, price_date, open_price=None, close_price=None,
                      high_price=None, low_price=None, volume=None):
    """Save an actual stock price. Upserts on (symbol_id, price_date).

    Returns the price id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        "SELECT id FROM actual_prices WHERE symbol_id = ? AND price_date = ?",
        (symbol_id, price_date)
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE actual_prices SET open_price = COALESCE(?, open_price), "
            "close_price = COALESCE(?, close_price), high_price = COALESCE(?, high_price), "
            "low_price = COALESCE(?, low_price), volume = COALESCE(?, volume), "
            "fetched_at = ? WHERE id = ?",
            (open_price, close_price, high_price, low_price, volume, now, row["id"])
        )
        conn.commit()
        result = row["id"]
    else:
        cursor.execute(
            "INSERT INTO actual_prices "
            "(symbol_id, price_date, open_price, close_price, high_price, low_price, "
            "volume, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol_id, price_date, open_price, close_price, high_price, low_price,
             volume, now)
        )
        conn.commit()
        result = cursor.lastrowid

    conn.close()
    return result


def get_actual_prices(symbol_id, start_date=None, end_date=None):
    """Get actual stock prices for a symbol, optionally within a date range.

    Returns list of dicts ordered by price_date.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM actual_prices WHERE symbol_id = ?"
    params = [symbol_id]

    if start_date:
        query += " AND price_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND price_date <= ?"
        params.append(end_date)

    query += " ORDER BY price_date"
    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_closest_price(symbol_id, target_date):
    """Get the closing price closest to (on or before) a target date.

    Handles weekends/holidays by finding the nearest prior trading day.

    Returns dict with price info, or None if no data available.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Try exact date first, then look for nearest prior trading day
    cursor.execute(
        "SELECT * FROM actual_prices WHERE symbol_id = ? AND price_date <= ? "
        "ORDER BY price_date DESC LIMIT 1",
        (symbol_id, target_date)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Accuracy Snapshot CRUD ───────────────────────────────────────────────

def save_accuracy_snapshot(target_price_id, symbol_id, checkpoint_days,
                           actual_price, target_price, price_diff=None,
                           pct_diff=None, accuracy_rating=None):
    """Save an accuracy snapshot. Upserts on (target_price_id, checkpoint_days).

    Returns the snapshot id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    if price_diff is None and actual_price is not None:
        price_diff = actual_price - target_price
    if pct_diff is None and actual_price is not None and target_price > 0:
        pct_diff = (actual_price - target_price) / target_price * 100

    if accuracy_rating is None and actual_price is not None:
        if abs(pct_diff) <= 5:
            accuracy_rating = "hit"
        elif pct_diff > 5:
            accuracy_rating = "miss_low"
        else:
            accuracy_rating = "miss_high"

    cursor.execute(
        "SELECT id FROM accuracy_snapshots "
        "WHERE target_price_id = ? AND checkpoint_days = ?",
        (target_price_id, checkpoint_days)
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE accuracy_snapshots SET actual_price = ?, target_price = ?, "
            "price_diff = ?, pct_diff = ?, accuracy_rating = ?, snapshot_date = ?, "
            "created_at = ? WHERE id = ?",
            (actual_price, target_price, price_diff, pct_diff, accuracy_rating,
             today, now, row["id"])
        )
        conn.commit()
        result = row["id"]
    else:
        cursor.execute(
            "INSERT INTO accuracy_snapshots "
            "(target_price_id, symbol_id, checkpoint_days, actual_price, target_price, "
            "price_diff, pct_diff, accuracy_rating, snapshot_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (target_price_id, symbol_id, checkpoint_days, actual_price, target_price,
             price_diff, pct_diff, accuracy_rating, today, now)
        )
        conn.commit()
        result = cursor.lastrowid

    conn.close()
    return result


def get_accuracy_snapshots(symbol_id=None, checkpoint_days=None):
    """Get accuracy snapshots, optionally filtered by symbol or checkpoint.

    Returns list of dicts.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT a.*, tp.source, tp.analyst_firm, tp.rating as analyst_rating,
               tp.date_posted, s.symbol, s.company_name
        FROM accuracy_snapshots a
        JOIN target_prices tp ON a.target_price_id = tp.id
        JOIN symbols s ON a.symbol_id = s.id
        WHERE 1=1
    """
    params = []

    if symbol_id:
        query += " AND a.symbol_id = ?"
        params.append(symbol_id)
    if checkpoint_days:
        query += " AND a.checkpoint_days = ?"
        params.append(checkpoint_days)

    query += " ORDER BY s.symbol, tp.date_posted, a.checkpoint_days"
    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_symbols_needing_accuracy_check(checkpoint_days=None):
    """Find target prices that are old enough for an accuracy checkpoint
    but don't have a snapshot yet.

    Returns list of dicts with target_price info + symbol.
    """
    conn = get_connection()
    cursor = conn.cursor()

    checkpoints = [checkpoint_days] if checkpoint_days else [30, 90, 180, 365]

    results = []
    for cp in checkpoints:
        cursor.execute(
            """
            SELECT tp.id as target_price_id, tp.symbol_id, tp.target_price,
                   tp.source, tp.analyst_firm, tp.date_posted,
                   s.symbol, s.company_name,
                   DATE(tp.date_posted, '+' || ? || ' days') as checkpoint_date
            FROM target_prices tp
            JOIN symbols s ON tp.symbol_id = s.id
            WHERE tp.date_posted IS NOT NULL
              AND DATE(tp.date_posted, '+' || ? || ' days') <= DATE('now')
              AND NOT EXISTS (
                  SELECT 1 FROM accuracy_snapshots a
                  WHERE a.target_price_id = tp.id AND a.checkpoint_days = ?
              )
            ORDER BY tp.date_posted
            """,
            (cp, cp, cp)
        )
        for row in cursor.fetchall():
            entry = dict(row)
            entry["checkpoint_days"] = cp
            results.append(entry)

    conn.close()
    return results


# ── Display Functions ────────────────────────────────────────────────────

def display_summary(by_source=False, by_checkpoint=False):
    """Display aggregate accuracy summary across all tracked targets."""
    conn = get_connection()
    cursor = conn.cursor()

    print("\n" + "=" * 80)
    print("ANALYST TARGET PRICE ACCURACY SUMMARY")
    print("=" * 80)

    # Overall stats
    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN accuracy_rating = 'hit' THEN 1 ELSE 0 END) as hits,
               SUM(CASE WHEN accuracy_rating = 'miss_low' THEN 1 ELSE 0 END) as miss_low,
               SUM(CASE WHEN accuracy_rating = 'miss_high' THEN 1 ELSE 0 END) as miss_high,
               SUM(CASE WHEN accuracy_rating = 'no_data' THEN 1 ELSE 0 END) as no_data,
               ROUND(AVG(pct_diff), 2) as avg_pct_diff,
               ROUND(AVG(ABS(pct_diff)), 2) as avg_abs_pct_diff
        FROM accuracy_snapshots
    """)
    overall = dict(cursor.fetchone())

    if overall["total"] and overall["total"] > 0:
        hit_rate = overall["hits"] / overall["total"] * 100
        print(f"\n  Overall ({overall['total']} checkpoints evaluated):")
        print(f"    Hit rate (within 5%):  {hit_rate:.1f}% ({overall['hits']}/{overall['total']})")
        print(f"    Miss (target too low): {overall['miss_low']}")
        print(f"    Miss (target too high): {overall['miss_high']}")
        print(f"    No data:               {overall['no_data']}")
        print(f"    Average deviation:     {overall['avg_pct_diff']:+.1f}%")
        print(f"    Average |deviation|:    {overall['avg_abs_pct_diff']:.1f}%")
    else:
        print("\n  No accuracy data yet. Run 'accuracy' command after targets have aged past checkpoints.")

    # By checkpoint
    if by_checkpoint or not by_source:
        cursor.execute("""
            SELECT checkpoint_days,
                   COUNT(*) as total,
                   SUM(CASE WHEN accuracy_rating = 'hit' THEN 1 ELSE 0 END) as hits,
                   ROUND(AVG(pct_diff), 2) as avg_pct_diff,
                   ROUND(AVG(ABS(pct_diff)), 2) as avg_abs_pct_diff
            FROM accuracy_snapshots
            GROUP BY checkpoint_days
            ORDER BY checkpoint_days
        """)
        checkpoints = [dict(row) for row in cursor.fetchall()]

        if checkpoints:
            print(f"\n  By Checkpoint:")
            print(f"    {'Checkpoint':<12} {'Hit Rate':>10} {'Avg Dev':>10} {'Avg |Dev|':>10} {'Total':>8}")
            print(f"    {'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*8}")
            for cp in checkpoints:
                label = f"{cp['checkpoint_days']}-day"
                hit_rate = cp["hits"] / cp["total"] * 100 if cp["total"] else 0
                print(f"    {label:<12} {hit_rate:>9.1f}% {cp['avg_pct_diff']:>+9.1f}% "
                      f"{cp['avg_abs_pct_diff']:>9.1f}% {cp['total']:>8}")

    # By source
    if by_source or not by_checkpoint:
        cursor.execute("""
            SELECT tp.source,
                   COUNT(*) as total,
                   SUM(CASE WHEN a.accuracy_rating = 'hit' THEN 1 ELSE 0 END) as hits,
                   ROUND(AVG(a.pct_diff), 2) as avg_pct_diff,
                   ROUND(AVG(ABS(a.pct_diff)), 2) as avg_abs_pct_diff
            FROM accuracy_snapshots a
            JOIN target_prices tp ON a.target_price_id = tp.id
            GROUP BY tp.source
            ORDER BY tp.source
        """)
        sources = [dict(row) for row in cursor.fetchall()]

        if sources:
            print(f"\n  By Source:")
            print(f"    {'Source':<15} {'Hit Rate':>10} {'Avg Dev':>10} {'Avg |Dev|':>10} {'Total':>8}")
            print(f"    {'-'*15} {'-'*10} {'-'*10} {'-'*10} {'-'*8}")
            for src in sources:
                hit_rate = src["hits"] / src["total"] * 100 if src["total"] else 0
                print(f"    {src['source']:<15} {hit_rate:>9.1f}% {src['avg_pct_diff']:>+9.1f}% "
                      f"{src['avg_abs_pct_diff']:>9.1f}% {src['total']:>8}")

    conn.close()


def display_symbol_detail(symbol):
    """Display full detail for a symbol: targets, prices, and accuracy history."""
    conn = get_connection()
    cursor = conn.cursor()

    # Symbol info
    cursor.execute("SELECT * FROM symbols WHERE symbol = ?", (symbol,))
    sym = cursor.fetchone()
    if not sym:
        print(f"\nSymbol '{symbol}' not found in database. Run 'fetch' first.")
        conn.close()
        return

    sym = dict(sym)

    # Latest actual price
    cursor.execute(
        "SELECT close_price, price_date FROM actual_prices "
        "WHERE symbol_id = ? ORDER BY price_date DESC LIMIT 1",
        (sym["id"],)
    )
    latest_price = cursor.fetchone()

    print(f"\n{'=' * 80}")
    company = sym.get("company_name") or ""
    print(f"TARGET DETAIL: {symbol} ({company})")
    if latest_price:
        print(f"Latest price: ${latest_price['close_price']:.2f} ({latest_price['price_date']})")
    print("=" * 80)

    # Analyst targets (latest)
    cursor.execute(
        "SELECT source, analyst_firm, rating, target_price, date_posted "
        "FROM target_prices WHERE symbol_id = ? "
        "ORDER BY date_posted DESC LIMIT 20",
        (sym["id"],)
    )
    targets = [dict(row) for row in cursor.fetchall()]

    if targets:
        print(f"\n  Analyst Targets (latest):")
        print(f"    {'Source':<15} {'Firm':<20} {'Rating':<12} {'Target':>8} {'Date':<12}")
        print(f"    {'-'*15} {'-'*20} {'-'*12} {'-'*8} {'-'*12}")
        for t in targets:
            firm = t["analyst_firm"] or "—"
            rating = t["rating"] or "—"
            print(f"    {t['source']:<15} {firm:<20} {rating:<12} "
                  f"${t['target_price']:>7.2f} {t['date_posted'] or 'N/A':<12}")

    # Accuracy history
    cursor.execute("""
        SELECT a.checkpoint_days, a.target_price, a.actual_price,
               a.price_diff, a.pct_diff, a.accuracy_rating,
               tp.source, tp.analyst_firm, tp.date_posted
        FROM accuracy_snapshots a
        JOIN target_prices tp ON a.target_price_id = tp.id
        WHERE a.symbol_id = ?
        ORDER BY tp.date_posted, a.checkpoint_days
    """, (sym["id"],))
    snapshots = [dict(row) for row in cursor.fetchall()]

    if snapshots:
        print(f"\n  Accuracy History:")
        print(f"    {'Checkpoint':<12} {'Source':<12} {'Firm':<15} {'Target':>8} "
              f"{'Actual':>8} {'Diff':>8} {'%Diff':>8} {'Rating':<10}")
        print(f"    {'-'*12} {'-'*12} {'-'*15} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
        for s in snapshots:
            cp_label = f"{s['checkpoint_days']}-day"
            actual = f"${s['actual_price']:.2f}" if s["actual_price"] is not None else "N/A"
            diff = f"${s['price_diff']:+.2f}" if s["price_diff"] is not None else "N/A"
            pct = f"{s['pct_diff']:+.1f}%" if s["pct_diff"] is not None else "N/A"
            firm = s["analyst_firm"] or "—"
            rating = s["accuracy_rating"] or "—"
            print(f"    {cp_label:<12} {s['source']:<12} {firm:<15} "
                  f"${s['target_price']:>7.2f} {actual:>8} {diff:>8} {pct:>8} {rating:<10}")
    else:
        print(f"\n  No accuracy data yet for {symbol}.")

    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized. Run tracker.py to populate data.")