"""
SQLite database for Horse Race Predictor.

Stores races, entries (horses), per-source expert picks, official results, and
accuracy snapshots so that source/consensus prediction accuracy can be measured
across races once official results are reconciled. Mirrors the access pattern of
stock_target_tracker/db.py.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from utils import DB_PATH  # honors HRP_DB_PATH env override


def get_connection():
    """Get a connection to the SQLite database.

    WAL + synchronous=NORMAL make per-statement commits much cheaper (~5x
    measured) - this app is a single writer doing many small upserts, which is
    exactly the pattern the default delete-journal fsync-per-commit hurts.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def connect(conn=None):
    """Yield a DB connection, closing it only if we opened it.

    Lets hot loops reuse one connection across many small reads/writes
    instead of paying connect/close per call:
        with db.connect() as c:
            for rid in ids:
                entries = db.get_entries(rid, conn=c)
    """
    if conn is not None:
        yield conn
    else:
        c = get_connection()
        try:
            yield c
        finally:
            c.close()


def init_db():
    """Create database tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS races (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_code TEXT NOT NULL,
            race_number INTEGER NOT NULL,
            race_date TEXT NOT NULL,
            post_time TEXT,
            distance TEXT,
            surface TEXT,
            race_type TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(track_code, race_number, race_date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id INTEGER NOT NULL,
            program_number TEXT NOT NULL,
            horse_name TEXT NOT NULL,
            jockey TEXT,
            trainer TEXT,
            morning_line_odds REAL,
            post_position INTEGER,
            scratched INTEGER DEFAULT 0,
            status TEXT DEFAULT 'in',
            FOREIGN KEY (race_id) REFERENCES races(id),
            UNIQUE(race_id, program_number)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            horse_name TEXT NOT NULL,
            program_number TEXT,
            rank INTEGER,
            comment TEXT,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY (race_id) REFERENCES races(id),
            UNIQUE(race_id, source, horse_name)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id INTEGER NOT NULL,
            program_number TEXT NOT NULL,
            horse_name TEXT NOT NULL,
            finish_position INTEGER,
            win_payoff REAL,
            place_payoff REAL,
            show_payoff REAL,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY (race_id) REFERENCES races(id),
            UNIQUE(race_id, program_number)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accuracy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            top_pick_horse TEXT,
            finish_position INTEGER,
            hit_win INTEGER,
            hit_place INTEGER,
            hit_show INTEGER,
            computed_at TEXT NOT NULL,
            FOREIGN KEY (race_id) REFERENCES races(id),
            UNIQUE(race_id, source)
        )
    """)

    # ── entries.status migration ───────────────────────────────────────────
    # Added to record scratch/MTO/AE status from the entries source. Older
    # databases created the entries table without this column.
    cursor.execute("PRAGMA table_info(entries)")
    existing_cols = {row["name"] for row in cursor.fetchall()}
    if "status" not in existing_cols:
        cursor.execute("ALTER TABLE entries ADD COLUMN status TEXT DEFAULT 'in'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_key TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            html TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            UNIQUE(report_key, period_start, period_end)
        )
    """)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Race / entries ───────────────────────────────────────────────────────

def save_race(race):
    """Upsert a race. Updates metadata fields if the race already exists.

    Returns the race_id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()

    cursor.execute(
        "SELECT id FROM races WHERE track_code = ? AND race_number = ? AND race_date = ?",
        (race.track_code, race.race_number, race.race_date),
    )
    row = cursor.fetchone()

    if row:
        race_id = row["id"]
        cursor.execute(
            "UPDATE races SET post_time = COALESCE(?, post_time), "
            "distance = COALESCE(?, distance), surface = COALESCE(?, surface), "
            "race_type = COALESCE(?, race_type), fetched_at = ? WHERE id = ?",
            (race.post_time or None, race.distance or None,
             race.surface or None, race.race_type or None, now, race_id),
        )
        conn.commit()
        conn.close()
        return race_id

    cursor.execute(
        "INSERT INTO races (track_code, race_number, race_date, post_time, distance, "
        "surface, race_type, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (race.track_code, race.race_number, race.race_date,
         race.post_time or None, race.distance or None, race.surface or None,
         race.race_type or None, now),
    )
    conn.commit()
    race_id = cursor.lastrowid
    conn.close()
    return race_id


def save_entries(race_id, entries):
    """Save/refresh the entries (horses) for a race.

    Replaces existing entries for the race so re-fetches don't grow stale rows.
    De-duplicates by program_number (keeps the first) so coupled entries or
    parser duplicates don't violate the (race_id, program_number) UNIQUE
    constraint; entries with a null program_number are all kept (NULLs are
    distinct under SQLite UNIQUE).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM entries WHERE race_id = ?", (race_id,))
    seen_progs = set()
    for e in entries:
        prog = e.get("program_number")
        if prog is not None and prog in seen_progs:
            continue  # skip duplicate program number (coupled entry / parser dup)
        if prog is not None:
            seen_progs.add(prog)
        status = e.get("status", "in")
        cursor.execute(
            "INSERT INTO entries (race_id, program_number, horse_name, jockey, trainer, "
            "morning_line_odds, post_position, scratched, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (race_id, prog, e.get("horse_name"),
             e.get("jockey"), e.get("trainer"), e.get("morning_line_odds"),
             e.get("post_position"), 1 if e.get("scratched") else 0, status),
        )
    conn.commit()
    conn.close()


def get_entries(race_id, conn=None):
    """Return list of entry dicts for a race, ordered by post_position.

    Pass `conn=` to reuse an open connection in hot loops."""
    with connect(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            "SELECT * FROM entries WHERE race_id = ? ORDER BY "
            "CASE WHEN post_position IS NULL THEN 9999 ELSE post_position END",
            (race_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_race_id(track_code, race_number, race_date, conn=None):
    """Look up a race_id by identity. Returns None if not stored."""
    with connect(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            "SELECT id FROM races WHERE track_code = ? AND race_number = ? AND race_date = ?",
            (track_code, race_number, race_date),
        )
        row = cursor.fetchone()
        return row["id"] if row else None


def get_race(race_id):
    """Return a race row dict, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM races WHERE id = ?", (race_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Picks ────────────────────────────────────────────────────────────────

def save_picks(race_id, source, picks):
    """Save/refresh the picks from one source for a race.

    Replaces existing picks for (race_id, source) so re-fetches don't grow
    stale rows. `picks` is a list of normalized pick dicts.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute("DELETE FROM picks WHERE race_id = ? AND source = ?", (race_id, source))
    for p in picks:
        cursor.execute(
            "INSERT INTO picks (race_id, source, horse_name, program_number, rank, "
            "comment, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (race_id, source, p.get("horse_name"), p.get("program_number"),
             p.get("rank"), p.get("comment"), now),
        )
    conn.commit()
    conn.close()


def get_picks(race_id, source=None, conn=None):
    """Return pick dicts for a race, optionally filtered by source."""
    with connect(conn) as c:
        cursor = c.cursor()
        if source:
            cursor.execute(
                "SELECT * FROM picks WHERE race_id = ? AND source = ? ORDER BY rank",
                (race_id, source),
            )
        else:
            cursor.execute(
                "SELECT * FROM picks WHERE race_id = ? ORDER BY source, rank",
                (race_id,),
            )
        return [dict(r) for r in cursor.fetchall()]


# ── Results ──────────────────────────────────────────────────────────────

def save_results(race_id, results):
    """Save/refresh official results for a race. Replaces existing rows."""
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute("DELETE FROM results WHERE race_id = ?", (race_id,))
    for r in results:
        cursor.execute(
            "INSERT INTO results (race_id, program_number, horse_name, finish_position, "
            "win_payoff, place_payoff, show_payoff, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (race_id, r.get("program_number"), r.get("horse_name"),
             r.get("finish_position"), r.get("win_payoff"),
             r.get("place_payoff"), r.get("show_payoff"), now),
        )
    conn.commit()
    conn.close()


def get_results(race_id, conn=None):
    """Return result dicts ordered by finish_position."""
    with connect(conn) as c:
        cursor = c.cursor()
        cursor.execute(
            "SELECT * FROM results WHERE race_id = ? ORDER BY "
            "CASE WHEN finish_position IS NULL THEN 9999 ELSE finish_position END",
            (race_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


# ── Accuracy snapshots ───────────────────────────────────────────────────

def save_accuracy_snapshot(race_id, source, top_pick_horse, finish_position,
                           hit_win, hit_place, hit_show):
    """Upsert one accuracy snapshot for (race_id, source)."""
    save_accuracy_snapshots(
        [(race_id, source, top_pick_horse, finish_position, hit_win, hit_place, hit_show)])


def save_accuracy_snapshots(rows, conn=None):
    """Batch-upsert accuracy snapshots in a single commit.

    `rows` is an iterable of (race_id, source, top_pick_horse, finish_position,
    hit_win, hit_place, hit_show) tuples. Same upsert semantics as
    save_accuracy_snapshot, but one transaction for the whole batch - a scored
    race writes ~10+ snapshots, and per-row commits dominate that cost.
    """
    with connect(conn) as c:
        cursor = c.cursor()
        now = _now()
        for race_id, source, top_pick_horse, finish_position, hit_win, hit_place, hit_show in rows:
            cursor.execute(
                "SELECT id FROM accuracy_snapshots WHERE race_id = ? AND source = ?",
                (race_id, source),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    "UPDATE accuracy_snapshots SET top_pick_horse = ?, finish_position = ?, "
                    "hit_win = ?, hit_place = ?, hit_show = ?, computed_at = ? WHERE id = ?",
                    (top_pick_horse, finish_position, hit_win, hit_place, hit_show, now, row["id"]),
                )
            else:
                cursor.execute(
                    "INSERT INTO accuracy_snapshots (race_id, source, top_pick_horse, "
                    "finish_position, hit_win, hit_place, hit_show, computed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (race_id, source, top_pick_horse, finish_position,
                     hit_win, hit_place, hit_show, now),
                )
        c.commit()


def get_accuracy_snapshots(race_id=None, conn=None):
    """Return accuracy snapshots, optionally for one race."""
    with connect(conn) as c:
        cursor = c.cursor()
        if race_id:
            cursor.execute("SELECT * FROM accuracy_snapshots WHERE race_id = ?", (race_id,))
        else:
            cursor.execute("SELECT * FROM accuracy_snapshots")
        return [dict(r) for r in cursor.fetchall()]


def get_scored_races():
    """Return list of races that have both picks and results stored."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT r.* FROM races r "
        "WHERE EXISTS (SELECT 1 FROM picks p WHERE p.race_id = r.id) "
        "AND EXISTS (SELECT 1 FROM results res WHERE res.race_id = r.id) "
        "ORDER BY r.race_date DESC, r.track_code, r.race_number"
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ── Reports (stored HTML) ────────────────────────────────────────────────

def save_report(report_key, html, period_start=None, period_end=None):
    """Upsert a generated HTML report keyed by (report_key, period_start, period_end)."""
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute(
        "SELECT id FROM reports WHERE report_key = ? AND period_start IS ? "
        "AND period_end IS ?",
        (report_key, period_start, period_end),
    )
    row = cursor.fetchone()
    if row:
        cursor.execute(
            "UPDATE reports SET html = ?, generated_at = ? WHERE id = ?",
            (html, now, row["id"]),
        )
    else:
        cursor.execute(
            "INSERT INTO reports (report_key, period_start, period_end, html, "
            "generated_at) VALUES (?, ?, ?, ?, ?)",
            (report_key, period_start, period_end, html, now),
        )
    conn.commit()
    conn.close()


def get_report(report_key, period_start=None, period_end=None):
    """Return the latest stored report dict for the key, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM reports WHERE report_key = ? AND period_start IS ? "
        "AND period_end IS ? ORDER BY generated_at DESC LIMIT 1",
        (report_key, period_start, period_end),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_reports():
    """Return all stored reports, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, report_key, period_start, period_end, generated_at "
                   "FROM reports ORDER BY generated_at DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows