"""One-off DB health check + trend report for the stock_target_tracker DBs.

Run: python stock_target_tracker/db_check.py
Prints per-DB: schema/integrity checks, data-quality issues, and overall trends.
"""
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

# Windows console defaults to cp1252; force UTF-8 so arrows/box chars print.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DBS = [
    ("portfolio (main)", os.path.join(HERE, "stock_tracker.db")),
    ("sample (isolated)", os.path.join(HERE, "sample_tracker.db")),
]

EXPECTED_TABLES = {"symbols", "target_prices", "actual_prices", "accuracy_snapshots"}
EXPECTED_TP_COLS = {"ever_hit", "first_hit_date", "days_to_hit", "ever_hit_eval_at"}
DATED_SOURCES = {"oanor", "marketbeat"}  # fmp summary is dated too; check by firm
CHECKPOINT_DAYS = [30, 90, 180, 365]
CASH_SYMBOLS = {"SPAXX", "SPAXX**", "VMFXX", "FDRXX"}


def conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def q(c, sql, params=()):
    return c.execute(sql, params).fetchall()


def scalar(c, sql, params=()):
    r = c.execute(sql, params).fetchone()
    return r[0] if r else 0


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def check_db(label, path):
    section(f"DB: {label}  ({os.path.basename(path)})")
    if not os.path.exists(path):
        print("  MISSING — skipping.")
        return
    c = conn(path)
    sz = os.path.getsize(path) / 1024
    print(f"  size: {sz:.0f} KB")

    # ── Schema ──
    tables = {r[0] for r in q(c, "SELECT name FROM sqlite_master WHERE type='table'")}
    missing_tables = EXPECTED_TABLES - tables
    print(f"  tables: {sorted(tables)}")
    if missing_tables:
        print(f"  !! MISSING TABLES: {missing_tables}")
    tp_cols = cols(c, "target_prices")
    missing_cols = EXPECTED_TP_COLS - tp_cols
    print(f"  target_prices cols ok: {not missing_cols}" + (f"  !! MISSING: {missing_cols}" if missing_cols else ""))

    # ── Row counts ──
    n_sym = scalar(c, "SELECT COUNT(*) FROM symbols")
    n_tp = scalar(c, "SELECT COUNT(*) FROM target_prices")
    n_ap = scalar(c, "SELECT COUNT(*) FROM actual_prices")
    n_sn = scalar(c, "SELECT COUNT(*) FROM accuracy_snapshots")
    print(f"  rows: symbols={n_sym}, target_prices={n_tp}, actual_prices={n_ap}, accuracy_snapshots={n_sn}")

    # ── Orphan FK checks ──
    orph_tp = scalar(c, "SELECT COUNT(*) FROM target_prices tp LEFT JOIN symbols s ON tp.symbol_id=s.id WHERE s.id IS NULL")
    orph_sn = scalar(c, "SELECT COUNT(*) FROM accuracy_snapshots a LEFT JOIN target_prices tp ON a.target_price_id=tp.id WHERE tp.id IS NULL")
    orph_ap = scalar(c, "SELECT COUNT(*) FROM actual_prices ap LEFT JOIN symbols s ON ap.symbol_id=s.id WHERE s.id IS NULL")
    print(f"  orphans: tp→symbols={orph_tp}, snapshots→targets={orph_sn}, prices→symbols={orph_ap}")

    # ── Cash/fund filter check ──
    cash = q(c, "SELECT symbol FROM symbols")
    cash_present = [r["symbol"] for r in cash if (r["symbol"] or "").upper() in CASH_SYMBOLS]
    print(f"  cash/fund symbols present (should be 0): {cash_present}")

    # ── Dated sources with NULL date_posted ──
    null_dated = q(c, """
        SELECT source, COUNT(*) n FROM target_prices
        WHERE date_posted IS NULL AND source IN ('oanor','marketbeat')
        GROUP BY source""", )
    # fmp avg rows are dated; fmp consensus is undated (expected)
    fmp_null = q(c, """
        SELECT analyst_firm, COUNT(*) n FROM target_prices
        WHERE source='fmp' AND date_posted IS NULL GROUP BY analyst_firm""", )
    print("  NULL date_posted on dated sources:")
    for r in null_dated:
        print(f"     {r['source']}: {r['n']}  !! unexpected (dated source should have a date)")
    for r in fmp_null:
        print(f"     fmp: {r['n']} ({r['analyst_firm'][:40]}) — ok if consensus (undated)")

    # ── Duplicate targets (should be prevented by UNIQUE) ──
    dups = scalar(c, """
        SELECT COUNT(*) FROM (
          SELECT symbol_id, source, analyst_name, date_posted, COUNT(*) n
          FROM target_prices GROUP BY symbol_id, source, analyst_name, date_posted
          HAVING n > 1)""")
    print(f"  duplicate (symbol,source,analyst,date) groups: {dups}")

    # ── Bad target prices ──
    bad = scalar(c, "SELECT COUNT(*) FROM target_prices WHERE target_price IS NULL OR target_price <= 0")
    print(f"  target_price NULL/<=0: {bad}")

    # ── Dated targets missing snapshots ──
    dated_no_snap = scalar(c, """
        SELECT COUNT(*) FROM target_prices tp
        WHERE tp.date_posted IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM accuracy_snapshots a WHERE a.target_price_id=tp.id)""")
    print(f"  dated targets with NO snapshots: {dated_no_snap}")

    # ── ever_hit NULL on eligible dated targets (window closed but unrecorded) ──
    eh_total = scalar(c, "SELECT COUNT(*) FROM target_prices WHERE date_posted IS NOT NULL")
    eh_done = scalar(c, "SELECT COUNT(*) FROM target_prices WHERE date_posted IS NOT NULL AND ever_hit IS NOT NULL")
    eh_null_closed = scalar(c, """
        SELECT COUNT(*) FROM target_prices
        WHERE date_posted IS NOT NULL AND ever_hit IS NULL
          AND DATE(date_posted,'+365 days') <= DATE('now')""")
    print(f"  ever_hit: {eh_done}/{eh_total} dated targets scored; NULL with CLOSED window (stale): {eh_null_closed}")

    # ── actual_prices staleness ──
    price_dates = q(c, """
        SELECT s.symbol, MAX(ap.price_date) latest
        FROM symbols s JOIN actual_prices ap ON ap.symbol_id=s.id
        GROUP BY s.symbol ORDER BY latest ASC LIMIT 5""", )
    print("  oldest latest-price dates (stalest symbols):")
    for r in price_dates:
        print(f"     {r['symbol']:6} latest price {r['latest']}")

    # ── no_data snapshots ──
    nodata = scalar(c, "SELECT COUNT(*) FROM accuracy_snapshots WHERE accuracy_rating='no_data'")
    print(f"  no_data snapshots: {nodata} / {n_sn}")

    # ── Trends ──
    print("\n  -- TRENDS --")
    # targets by source
    by_src = q(c, "SELECT source, COUNT(*) n FROM target_prices GROUP BY source ORDER BY n DESC")
    print("  targets by source:")
    for r in by_src:
        print(f"     {r['source']:14} {r['n']}")

    # targets by month (dated only)
    by_month = q(c, """
        SELECT strftime('%Y-%m', date_posted) m, COUNT(*) n
        FROM target_prices WHERE date_posted IS NOT NULL
        GROUP BY m ORDER BY m""")
    print("  dated targets by month:")
    months = [(r["m"], r["n"]) for r in by_month]
    # print first, last, and a compact sparkline of last 12
    if months:
        print(f"     earliest: {months[0][0]} ({months[0][1]})   latest: {months[-1][0]} ({months[-1][1]})")
        recent = months[-12:]
        spark = " ".join(f"{m[0][-2:]}:{m[1]}" for m in recent)
        print(f"     last 12 months (MM:n): {spark}")
        # trend direction
        if len(recent) >= 2:
            first_half = sum(m[1] for m in recent[:len(recent)//2])
            second_half = sum(m[1] for m in recent[len(recent)//2:])
            arrow = "▲" if second_half > first_half else "▼" if second_half < first_half else "→"
            print(f"     recent activity {arrow} (1st half {first_half} vs 2nd half {second_half})")

    # hit rate by checkpoint
    print("  hit rate by checkpoint:")
    for cp in CHECKPOINT_DAYS:
        row = c.execute("""
            SELECT COUNT(*) total,
                   SUM(CASE WHEN accuracy_rating='hit' THEN 1 ELSE 0 END) hits,
                   AVG(pct_diff) avg_pct
            FROM accuracy_snapshots WHERE checkpoint_days=? AND accuracy_rating!='no_data'""", (cp,)).fetchone()
        if row["total"]:
            hr = row["hits"] / row["total"] * 100
            print(f"     {cp:>3}-day: {row['hits']}/{row['total']} = {hr:5.1f}% hit, avg pct_diff {row['avg_pct']:+.1f}% (bias {'too optimistic' if row['avg_pct'] < 0 else 'too pessimistic'})")
        else:
            print(f"     {cp:>3}-day: no data")

    # ever-hit summary
    eh = c.execute("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN ever_hit=1 THEN 1 ELSE 0 END) hit,
               AVG(CASE WHEN ever_hit=1 AND days_to_hit IS NOT NULL THEN days_to_hit END) avg_days
        FROM target_prices WHERE ever_hit IS NOT NULL""").fetchone()
    if eh["total"]:
        print(f"  ever-hit (TPMetANY): {eh['hit']}/{eh['total']} = {eh['hit']/eh['total']*100:.1f}% touched target in 365d window, avg days_to_hit {eh['avg_days']:.0f}" if eh["hit"] else f"  ever-hit: 0/{eh['total']}")

    # consensus vs firm hit rate
    def hit_rate_where(where, params=()):
        r = c.execute(f"SELECT COUNT(*) t, SUM(CASE WHEN a.accuracy_rating='hit' THEN 1 ELSE 0 END) h FROM accuracy_snapshots a JOIN target_prices tp ON a.target_price_id=tp.id WHERE a.accuracy_rating!='no_data' {where}", params).fetchone()
        return (r["h"], r["t"]) if r["t"] else (0, 0)
    cons_h, cons_t = hit_rate_where("AND (LOWER(tp.analyst_firm) LIKE '%consensus%' OR LOWER(tp.analyst_firm) LIKE 'fmp%')")
    firm_h, firm_t = hit_rate_where("AND LOWER(tp.analyst_firm) NOT LIKE '%consensus%' AND LOWER(tp.analyst_firm) NOT LIKE 'fmp%'")
    if cons_t:
        print(f"  hit rate: consensus {cons_h}/{cons_t} = {cons_h/cons_t*100:.1f}%   |   individual firms {firm_h}/{firm_t} = {firm_h/firm_t*100:.1f}%" if firm_t else f"  hit rate: consensus {cons_h/cons_t*100:.1f}%")

    # top / bottom firms by hit rate (min 5 snapshots)
    firms = c.execute("""
        SELECT tp.analyst_firm firm, COUNT(*) total,
               SUM(CASE WHEN a.accuracy_rating='hit' THEN 1 ELSE 0 END) hits,
               AVG(a.pct_diff) avg_pct
        FROM accuracy_snapshots a JOIN target_prices tp ON a.target_price_id=tp.id
        WHERE a.accuracy_rating!='no_data' AND COALESCE(tp.analyst_firm,'')!=''
        GROUP BY tp.analyst_firm HAVING total >= 5
        ORDER BY hits*1.0/total DESC, total DESC""").fetchall()
    if firms:
        print(f"  most accurate firms (min 5 snapshots, n={len(firms)}):")
        for r in list(firms)[:5]:
            print(f"     {r['firm'][:34]:34} {r['hits']}/{r['total']} = {r['hits']/r['total']*100:5.1f}%  avg {r['avg_pct']:+.1f}%")
        print("  least accurate firms:")
        for r in list(firms)[-3:]:
            print(f"     {r['firm'][:34]:34} {r['hits']}/{r['total']} = {r['hits']/r['total']*100:5.1f}%  avg {r['avg_pct']:+.1f}%")

    c.close()


def main():
    for label, path in DBS:
        check_db(label, path)
    print("\n" + "=" * 70)
    print("done")
    print("=" * 70)


if __name__ == "__main__":
    main()