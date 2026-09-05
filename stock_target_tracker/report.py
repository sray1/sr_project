"""
Generate an HTML report of stock target price accuracy results.

Queries the SQLite database and produces a standalone HTML file with
interactive dashboard, per-symbol breakdowns, key insights, and charts.

Usage:
  python stock_target_tracker/report.py
  python stock_target_tracker/report.py --output custom_report.html
"""

import os
import sys
import re
import json
import argparse
import html as _html
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import (
    init_db, get_symbols, get_target_prices, get_actual_prices,
    get_accuracy_snapshots, get_connection, save_price_history_rows,
)
import price_fetcher
from accuracy import HIT_TOLERANCE_PCT
from utils import run_and_save


def _fmt_sig3(value):
    """Format a numeric value to at most 3 significant digits.

    Caps precision so a value like 53.37% renders as 53.4% (not 53.37%) while
    36.4% stays 36.4% and 4.8% stays 4.8%. Trailing zeros are stripped. Returns
    "0" for zero/None so callers can interpolate directly.
    """
    if value is None:
        return "0"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if v == 0:
        return "0"
    return f"{v:.3g}"


def _price_range_for(history, start_date, horizon_days=360):
    """Compute a symbol's price range over [start_date, start_date+horizon_days],
    clamped to the available data.

    Args:
        history: list of dicts {price_date, low, high, close} sorted ascending
            (a symbol's fetched daily history).
        start_date: 'YYYY-MM-DD' the analyst target was made (date_posted).
        horizon_days: how many days forward to include (default 360 —
                      ~one year of trading days; intentionally a touch
                      shorter than the 365-day whole-window/ever-hit window).

    Returns:
        dict {low, high, start, end, n_points} where low/high are the min low and
        max high over the window, start/end the first and last trading dates with
        data in the window. Returns None if start_date is missing/invalid or no
        price data falls in the window ("if possible").
    """
    if not start_date or not history:
        return None
    try:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None
    end_str = (start_dt + timedelta(days=horizon_days)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')
    if end_str > today:
        end_str = today

    lows, highs = [], []
    first, last = None, None
    for p in history:
        d = p['price_date']
        if start_date <= d <= end_str:
            if p.get('low') is not None:
                lows.append(p['low'])
            if p.get('high') is not None:
                highs.append(p['high'])
            if first is None:
                first = d
            last = d
    if not lows or not highs:
        return None
    return {
        'low': round(min(lows), 2),
        'high': round(max(highs), 2),
        'start': first,
        'end': last,
        'n_points': len(lows),
    }


def _whole_window_stats(history, date_posted, target_price, horizon_days=365):
    """Whole-window accuracy measures for one target over
    [date_posted, date_posted + horizon_days], clamped to today.

    Unlike the 30/90/180/365 checkpoints (a single point each), these evaluate the
    target against the stock's *entire* price path across the window:

      met_any           — did the price ever touch the target? True if any day's
                          intraday low..high range straddles the target
                          (direction-agnostic, so it works for both bullish and
                          bearish targets).
      first_hit_date    — the first trading day the target was touched (None if
                          never).
      days_to_hit       — calendar days from date_posted to first_hit_date
                          (None if never).
      within_band_pct   — % of trading days the close was within +/-HIT_TOLERANCE_PCT
                          of the target (a continuous version of the checkpoint
                          "hit" rule, averaged across the whole window).
      mean_signed_pct   — mean of (close - target)/target*100 across days.
                          Negative = targets sat above the price on average
                          (analysts too optimistic), positive = too pessimistic.
      n_days            — number of trading days with a close in the window.
      window_end        — last trading date in the window.

    Returns None if date_posted is missing/invalid, target_price <= 0, or no
    price data falls in the window (same "if possible" grace as the price range).
    """
    if not date_posted or not history or target_price is None or target_price <= 0:
        return None
    try:
        start_dt = datetime.strptime(date_posted, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None
    end_str = (start_dt + timedelta(days=horizon_days)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')
    if end_str > today:
        end_str = today

    points = [p for p in history if date_posted <= p['price_date'] <= end_str]
    if not points:
        return None

    met = False
    first_hit_date = None
    days_to_hit = None
    within_band = 0
    signed_sum = 0.0
    n = 0
    for p in points:
        d = p['price_date']
        low = p.get('low')
        high = p.get('high')
        close = p.get('close')
        # Touch: the day's traded range included the target price.
        if low is not None and high is not None and low <= target_price <= high:
            if not met:
                met = True
                first_hit_date = d
                days_to_hit = (datetime.strptime(d, '%Y-%m-%d') - start_dt).days
        if close is not None:
            n += 1
            pct = (close - target_price) / target_price * 100
            signed_sum += pct
            if abs(pct) <= HIT_TOLERANCE_PCT:
                within_band += 1

    if n == 0:
        return None

    return {
        'met_any': met,
        'first_hit_date': first_hit_date,
        'days_to_hit': days_to_hit,
        'within_band_pct': round(within_band / n * 100, 1),
        'mean_signed_pct': round(signed_sum / n, 1),
        'n_days': n,
        'window_start': date_posted,
        'window_end': points[-1]['price_date'],
    }


def _is_cash_fund(sym):
    """True for money-market / sweep / cash funds (e.g. SPAXX) — these are cash
    positions, not stocks: no analyst coverage, ~$1.00 NAV. They're filtered out
    of the report so the symbol list only shows investable equities + ETFs.

    Matches by fund-name keywords, or by the $1.00-NAV signature (price near $1,
    no sector, no analyst targets) so similarly-named sweep funds are caught too.
    Real ETFs (ARKK, MSOS) and low-priced stocks (GEVO, SNDL) are NOT matched:
    they either have a sector, analyst targets, or a price far from $1.
    """
    name = (sym.get("company_name") or "").lower()
    if any(k in name for k in ("money market", "sweep", "cash fund",
                                "government money", "fdic", "treasury only")):
        return True
    price = sym.get("latest_price")
    if (price is not None and 0.95 <= price <= 1.05
            and not sym.get("sector") and (sym.get("target_count") or 0) == 0):
        return True
    return False


def _fetch_report_data():
    """Query all data needed for the HTML report."""
    conn = get_connection()
    cursor = conn.cursor()

    # ── Overall stats ──
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

    # ── Per-checkpoint stats ──
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
    by_checkpoint = [dict(row) for row in cursor.fetchall()]

    # ── Per-source stats ──
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
    by_source = [dict(row) for row in cursor.fetchall()]

    # ── Per-symbol stats ──
    # NOTE: target_count and the snapshot stats are computed in separate
    # subqueries. Joining target_prices and accuracy_snapshots to `symbols`
    # independently (on symbol_id only) creates a cartesian product that
    # multiplies the per-symbol hit count by the number of targets, producing
    # hit rates above 100% (e.g. 12 hits &times; 26 targets / 33 snapshots = 945%).
    # Keeping each aggregate in its own subquery avoids the fan-out entirely.
    cursor.execute("""
        SELECT s.id, s.symbol, s.company_name, s.sector,
               ap.close_price as latest_price, ap.price_date as latest_price_date,
               tp_stats.target_count,
               sa.snapshot_count,
               sa.hits, sa.miss_low, sa.miss_high,
               sa.avg_pct_diff, sa.avg_abs_pct_diff
        FROM symbols s
        LEFT JOIN actual_prices ap ON s.id = ap.symbol_id
            AND ap.price_date = (
                SELECT MAX(price_date) FROM actual_prices WHERE symbol_id = s.id
            )
        LEFT JOIN (
            SELECT symbol_id, COUNT(*) as target_count
            FROM target_prices
            GROUP BY symbol_id
        ) tp_stats ON tp_stats.symbol_id = s.id
        LEFT JOIN (
            SELECT symbol_id,
                   COUNT(*) as snapshot_count,
                   SUM(CASE WHEN accuracy_rating = 'hit' THEN 1 ELSE 0 END) as hits,
                   SUM(CASE WHEN accuracy_rating = 'miss_low' THEN 1 ELSE 0 END) as miss_low,
                   SUM(CASE WHEN accuracy_rating = 'miss_high' THEN 1 ELSE 0 END) as miss_high,
                   ROUND(AVG(pct_diff), 2) as avg_pct_diff,
                   ROUND(AVG(ABS(pct_diff)), 2) as avg_abs_pct_diff
            FROM accuracy_snapshots
            GROUP BY symbol_id
        ) sa ON sa.symbol_id = s.id
        WHERE s.is_active = 1
        ORDER BY s.symbol
    """)
    symbols = [dict(row) for row in cursor.fetchall()]

    # LEFT JOINs yield NULL for symbols with no targets/snapshots; coerce to 0
    # so downstream hit-rate math (hits / max(snapshot_count, 1)) is safe.
    for sym in symbols:
        for k in ("target_count", "snapshot_count", "hits", "miss_low", "miss_high",
                 "avg_pct_diff", "avg_abs_pct_diff"):
            if sym.get(k) is None:
                sym[k] = 0

    # Filter out money-market / cash funds (cash positions, not equities).
    cash = [s for s in symbols if _is_cash_fund(s)]
    if cash:
        print(f"  Filtering out {len(cash)} money-market/cash fund(s): "
              f"{', '.join(s['symbol'] for s in cash)}")
    symbols = [s for s in symbols if not _is_cash_fund(s)]

    # Cache of per-symbol price history (symbol -> sorted list of daily price
    # dicts). One BATCHED download serves every symbol's 360-day price ranges,
    # whole-window stats, and chart data (instead of one request per symbol).
    # The fetched rows are persisted into actual_prices so the DB accumulates
    # a dense daily price history; symbols whose download fails fall back to
    # whatever rows the DB already has.
    min_date_rows = cursor.execute(
        "SELECT symbol_id, MIN(date_posted) as md FROM target_prices "
        "WHERE date_posted IS NOT NULL GROUP BY symbol_id"
    ).fetchall()
    min_dates = {r["symbol_id"]: r["md"] for r in min_date_rows if r["md"]}
    hist_requests = {s["symbol"]: min_dates[s["id"]] for s in symbols
                     if s["id"] in min_dates}
    history_cache = {}
    if hist_requests:
        print(f"  Fetching price histories for {len(hist_requests)} symbols "
              f"(batch, from {min(hist_requests.values())})...")
        history_cache = price_fetcher.fetch_price_histories(hist_requests)
        for s in symbols:
            hist = history_cache.get(s["symbol"])
            if hist:
                save_price_history_rows(s["id"], hist)

    def _db_history_fallback(sym):
        """History rows from actual_prices, in fetch_price_history's format."""
        start = min_dates.get(sym["id"])
        if not start:
            return []
        return [
            {"price_date": r["price_date"], "open": r["open_price"],
             "high": r["high_price"], "low": r["low_price"],
             "close": r["close_price"], "volume": r["volume"]}
            for r in get_actual_prices(sym["id"], start, None)
        ]

    for s in symbols:
        if s["symbol"] in min_dates and s["symbol"] not in history_cache:
            fallback = _db_history_fallback(s)
            if fallback:
                history_cache[s["symbol"]] = fallback

    # Memoized whole-window stats: the per-symbol loop and the per-org loop
    # evaluate overlapping target sets against the same cached history, so each
    # (symbol, date_posted, target_price) is computed exactly once.
    ww_stats_cache = {}

    def _ww_stats(symbol, t):
        key = (symbol, t["date_posted"], t["target_price"])
        if key not in ww_stats_cache:
            ww_stats_cache[key] = _whole_window_stats(
                history_cache.get(symbol, []), t["date_posted"], t["target_price"])
        return ww_stats_cache[key]

    # ── Per-symbol: latest targets ──
    for sym in symbols:
        cursor.execute("""
            SELECT tp.source, tp.analyst_firm, tp.rating, tp.target_price,
                   tp.date_posted
            FROM target_prices tp
            WHERE tp.symbol_id = ?
            ORDER BY tp.date_posted DESC
            LIMIT 10
        """, (sym["id"],))
        sym["latest_targets"] = [dict(row) for row in cursor.fetchall()]

        # ── Per-symbol: consensus from each source ──
        cursor.execute("""
            SELECT tp.source,
                   ROUND(AVG(tp.target_price), 2) as consensus_target,
                   MIN(tp.target_price) as low_target,
                   MAX(tp.target_price) as high_target,
                   COUNT(*) as analyst_count
            FROM target_prices tp
            WHERE tp.symbol_id = ?
            GROUP BY tp.source
        """, (sym["id"],))
        sym["source_consensus"] = [dict(row) for row in cursor.fetchall()]

        # ── Per-symbol: accuracy by checkpoint ──
        cursor.execute("""
            SELECT a.checkpoint_days,
                   COUNT(*) as total,
                   SUM(CASE WHEN a.accuracy_rating = 'hit' THEN 1 ELSE 0 END) as hits,
                   ROUND(AVG(a.pct_diff), 2) as avg_pct_diff,
                   ROUND(AVG(ABS(a.pct_diff)), 2) as avg_abs_pct_diff
            FROM accuracy_snapshots a
            WHERE a.symbol_id = ?
            GROUP BY a.checkpoint_days
            ORDER BY a.checkpoint_days
        """, (sym["id"],))
        sym["accuracy_by_checkpoint"] = [dict(row) for row in cursor.fetchall()]

        # ── Per-symbol: best/worst analysts (deduplicated by firm) ──
        # Each analyst_firm appears at most once per list: ROW_NUMBER()
        # partitioned by firm keeps only the firm's single closest (or farthest)
        # call. We over-fetch, then mutually exclude so a firm is never listed
        # as both most AND least accurate for the same symbol.
        cursor.execute("""
            SELECT analyst_firm, source, target_price, pct_diff,
                   accuracy_rating, checkpoint_days, date_posted
            FROM (
                SELECT tp.analyst_firm, tp.source, tp.target_price,
                       a.pct_diff, a.accuracy_rating, a.checkpoint_days,
                       tp.date_posted,
                       ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(tp.analyst_firm, '')
                           ORDER BY ABS(a.pct_diff) ASC, a.checkpoint_days ASC
                       ) as rn
                FROM accuracy_snapshots a
                JOIN target_prices tp ON a.target_price_id = tp.id
                WHERE a.symbol_id = ?
                  AND a.accuracy_rating != 'no_data'
                  AND COALESCE(tp.analyst_firm, '') != ''
            )
            WHERE rn = 1
            ORDER BY ABS(pct_diff) ASC
            LIMIT 6
        """, (sym["id"],))
        sym["best_analysts"] = [dict(row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT analyst_firm, source, target_price, pct_diff,
                   accuracy_rating, checkpoint_days, date_posted
            FROM (
                SELECT tp.analyst_firm, tp.source, tp.target_price,
                       a.pct_diff, a.accuracy_rating, a.checkpoint_days,
                       tp.date_posted,
                       ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(tp.analyst_firm, '')
                           ORDER BY ABS(a.pct_diff) DESC, a.checkpoint_days DESC
                       ) as rn
                FROM accuracy_snapshots a
                JOIN target_prices tp ON a.target_price_id = tp.id
                WHERE a.symbol_id = ?
                  AND a.accuracy_rating != 'no_data'
                  AND COALESCE(tp.analyst_firm, '') != ''
            )
            WHERE rn = 1
            ORDER BY ABS(pct_diff) DESC
            LIMIT 6
        """, (sym["id"],))
        sym["worst_analysts"] = [dict(row) for row in cursor.fetchall()]

        # Trim to 3 and make the two lists mutually exclusive (best wins).
        # Consensus aggregates (oanor monthly consensus) are split out from
        # individual firms so an aggregate is never listed as a symbol's
        # "Most Accurate Analyst" alongside a real firm; each group is trimmed
        # and mutually excluded independently.
        sym["best_analysts"], sym["worst_analysts"], \
            sym["best_analysts_consensus"], sym["worst_analysts_consensus"] = \
            _split_best_worst(sym["best_analysts"], sym["worst_analysts"])

        # Attach the 360-day price range to each displayed analyst target.
        # (The history itself was fetched in one batch above; symbols with no
        # targets have no history and ranges show as "if possible"/unavailable.)
        hist = history_cache.get(sym["symbol"], [])
        for a in sym["best_analysts"] + sym["worst_analysts"]:
            a["price_range"] = _price_range_for(hist, a.get("date_posted"))

        # All targets for this symbol (date + price + rating + firm) for the
        # price-over-time chart, plus the cached daily history so the chart can
        # be rendered client-side with no extra network calls.
        cursor.execute("""
            SELECT date_posted, target_price, rating, analyst_firm, source
            FROM target_prices
            WHERE symbol_id = ? AND date_posted IS NOT NULL
            ORDER BY date_posted ASC
        """, (sym["id"],))
        sym["chart_targets"] = [dict(row) for row in cursor.fetchall()]
        sym["price_history"] = hist

        # Per-symbol whole-window accuracy: aggregate the whole-window measures
        # (Met_any, time-to-hit, within-band %, bias) across this symbol's own
        # targets, complementing the 30/90/180/365 checkpoint bars. Reuses the
        # cached history; None when no target has usable price data.
        ww_n = 0
        ww_met = 0
        ww_dth = []
        ww_band = []
        ww_bias = []
        for t in sym["chart_targets"]:
            stats = _ww_stats(sym["symbol"], t)
            if not stats:
                continue
            ww_n += 1
            if stats["met_any"]:
                ww_met += 1
                if stats["days_to_hit"] is not None:
                    ww_dth.append(stats["days_to_hit"])
            ww_band.append(stats["within_band_pct"])
            ww_bias.append(stats["mean_signed_pct"])
        if ww_n:
            sym["whole_window"] = {
                "n_targets": len(sym["chart_targets"]),
                "n_evaluated": ww_n,
                "met_any_count": ww_met,
                "met_any_rate": round(ww_met / ww_n * 100, 1),
                "avg_days_to_hit": (round(sum(ww_dth) / len(ww_dth), 1) if ww_dth else None),
                "avg_within_band_pct": round(sum(ww_band) / len(ww_band), 1),
                "avg_bias_pct": round(sum(ww_bias) / len(ww_bias), 1),
            }
        else:
            sym["whole_window"] = None

    # ── Overall analyst accuracy (aggregated across all symbols) ──
    # One row per analyst firm: aggregate hit rate and avg deviation across
    # every checkpoint snapshot they have (excluding no_data rows and unnamed
    # firms). Ranked below into Most/Least Accurate.
    cursor.execute("""
        SELECT COALESCE(tp.analyst_firm, '') as analyst_firm,
               COUNT(*) as total,
               SUM(CASE WHEN a.accuracy_rating = 'hit' THEN 1 ELSE 0 END) as hits,
               ROUND(AVG(a.pct_diff), 2) as avg_pct_diff,
               ROUND(AVG(ABS(a.pct_diff)), 2) as avg_abs_pct_diff
        FROM accuracy_snapshots a
        JOIN target_prices tp ON a.target_price_id = tp.id
        WHERE a.accuracy_rating != 'no_data'
          AND COALESCE(tp.analyst_firm, '') != ''
        GROUP BY COALESCE(tp.analyst_firm, '')
        ORDER BY avg_abs_pct_diff ASC
    """)
    analyst_stats = [dict(row) for row in cursor.fetchall()]
    for a in analyst_stats:
        a["hit_rate"] = round(a["hits"] / a["total"] * 100, 1) if a["total"] else 0.0

    # Per-firm representative target: the firm's single closest call (best) and
    # single farthest call (worst), with the date the target was made
    # (date_posted) and the date it was evaluated/realized (eval_date =
    # date_posted + checkpoint_days = "the date the target was hit"). Used in
    # the Most/Least Accurate panels to show when the target was hit.
    cursor.execute("""
        SELECT analyst_firm, date_posted, checkpoint_days, target_price,
               actual_price, pct_diff, accuracy_rating, eval_date,
               rn_best, rn_worst, symbol
        FROM (
            SELECT COALESCE(tp.analyst_firm, '') as analyst_firm,
                   tp.date_posted, a.checkpoint_days, tp.target_price,
                   a.actual_price, a.pct_diff, a.accuracy_rating,
                   DATE(tp.date_posted, '+' || a.checkpoint_days || ' days') as eval_date,
                   s.symbol,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(tp.analyst_firm, '')
                       ORDER BY ABS(a.pct_diff) ASC, a.checkpoint_days ASC
                   ) as rn_best,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(tp.analyst_firm, '')
                       ORDER BY ABS(a.pct_diff) DESC, a.checkpoint_days DESC
                   ) as rn_worst
            FROM accuracy_snapshots a
            JOIN target_prices tp ON a.target_price_id = tp.id
            JOIN symbols s ON tp.symbol_id = s.id
            WHERE a.accuracy_rating != 'no_data'
              AND COALESCE(tp.analyst_firm, '') != ''
        )
        WHERE rn_best = 1 OR rn_worst = 1
    """)
    reps = {}
    for row in cursor.fetchall():
        d = dict(row)
        f = d["analyst_firm"]
        entry = reps.setdefault(f, {})
        target = {
            "symbol": d["symbol"],
            "date_posted": d["date_posted"],
            "eval_date": d["eval_date"],
            "checkpoint_days": d["checkpoint_days"],
            "target_price": d["target_price"],
            "actual_price": d["actual_price"],
            "pct_diff": d["pct_diff"],
            "accuracy_rating": d["accuracy_rating"],
        }
        # A single-snapshot firm is both its best and worst call.
        if d["rn_best"] == 1:
            entry["best_target"] = target
        if d["rn_worst"] == 1:
            entry["worst_target"] = target
    for a in analyst_stats:
        r = reps.get(a["analyst_firm"], {})
        a["best_target"] = r.get("best_target")
        a["worst_target"] = r.get("worst_target")
        # Attach the 360-day price range to each representative target using the
        # cached history for that target's symbol (fetched in the per-symbol
        # loop above). Missing history -> None ("if possible").
        for key in ("best_target", "worst_target"):
            t = a[key]
            if t:
                t["price_range"] = _price_range_for(
                    history_cache.get(t.get("symbol"), []), t.get("date_posted")
                )

    # Split individual firms from consensus aggregates (oanor monthly consensus)
    # so the Most/Least Accurate panels rank each group separately — an aggregate
    # is never ranked alongside a real analyst firm.
    firm_stats = [a for a in analyst_stats if not _is_consensus_firm(a["analyst_firm"])]
    consensus_stats = [a for a in analyst_stats if _is_consensus_firm(a["analyst_firm"])]

    most_accurate, least_accurate = _rank_analysts(firm_stats)
    most_accurate_cons, least_accurate_cons = _rank_analysts(consensus_stats)

    # ── Per-org price-target methodology ──
    # One row per analyst org (firm) present in the data, with how its target
    # is calculated. Consensus orgs get the exact formula we compute (mean of
    # individual targets); individual firms get an honest general description of
    # the standard valuation building blocks, since firm-specific weighting is
    # proprietary and not disclosed by our data sources.
    cursor.execute("""
        SELECT COALESCE(analyst_firm, '') as analyst_firm,
               GROUP_CONCAT(DISTINCT source) as sources,
               COUNT(*) as target_count,
               MIN(date_posted) as first_posted,
               MAX(date_posted) as last_posted
        FROM target_prices
        WHERE COALESCE(analyst_firm, '') != ''
        GROUP BY COALESCE(analyst_firm, '')
        ORDER BY target_count DESC, analyst_firm
    """)
    org_methodologies = []
    for row in cursor.fetchall():
        d = dict(row)
        org_methodologies.append({
            "org": d["analyst_firm"],
            "sources": (d["sources"] or "").split(","),
            "target_count": d["target_count"],
            "first_posted": d["first_posted"],
            "last_posted": d["last_posted"],
            **_methodology_for(d["analyst_firm"]),
        })

    # Collapse oanor's many consensus rows into one. oanor emits a dated
    # consensus target per month ("oanor (Nasdaq consensus, 2025-06)", ...) plus
    # an undated current consensus ("oanor consensus (Nasdaq)", sometimes split by
    # low/high band). Each would otherwise be its own row in the methodology
    # table and counted as a separate Consensus org. Group them into a single
    # "oanor consensus (Nasdaq)" row with summed targets and a per-month chip
    # cloud (expandable) so the table stays compact. Other consensus orgs (Yahoo)
    # and all individual firms are left untouched.
    org_methodologies = _collapse_oanor_consensus(org_methodologies)
    # Likewise collapse FMP's consensus + period-averaged rows (which vary per
    # symbol by their high/low/median and analyst-count labels) into one
    # "FMP consensus" row with a per-window chip cloud.
    org_methodologies = _collapse_fmp_consensus(org_methodologies)

    # ── Whole-window target accuracy (over the full year, not just
    # checkpoints) ──
    # For each analyst org, evaluate every target against the stock's whole
    # price path (Met_any, time-to-hit, time-within-band, bias) using the
    # batched history cached above. These complement the 30/90/180/365
    # checkpoints: a target that missed at day 365 may still have been touched
    # mid-window. history_cache was fetched in one batch before the per-symbol
    # loop, so no extra network calls are made here.
    cursor.execute("""
        SELECT COALESCE(tp.analyst_firm, '') as analyst_firm,
               s.symbol, tp.date_posted, tp.target_price
        FROM target_prices tp
        JOIN symbols s ON tp.symbol_id = s.id
        WHERE COALESCE(tp.analyst_firm, '') != ''
          AND tp.date_posted IS NOT NULL
          AND tp.target_price > 0
    """)
    org_targets = {}
    for row in cursor.fetchall():
        d = dict(row)
        org_targets.setdefault(d["analyst_firm"], []).append(d)

    whole_window = []
    total_eval = 0
    total_met = 0
    for org, targets in org_targets.items():
        met_any_count = 0
        days_to_hit_vals = []
        within_band_vals = []
        bias_vals = []
        n_with_data = 0
        for t in targets:
            stats = _ww_stats(t["symbol"], t)
            if not stats:
                continue
            n_with_data += 1
            if stats["met_any"]:
                met_any_count += 1
                if stats["days_to_hit"] is not None:
                    days_to_hit_vals.append(stats["days_to_hit"])
            within_band_vals.append(stats["within_band_pct"])
            bias_vals.append(stats["mean_signed_pct"])
        if n_with_data == 0:
            continue
        total_eval += n_with_data
        total_met += met_any_count
        whole_window.append({
            "analyst_firm": org,
            "is_consensus": _is_consensus_firm(org),
            "n_targets": len(targets),
            "n_evaluated": n_with_data,
            "met_any_count": met_any_count,
            "met_any_rate": round(met_any_count / n_with_data * 100, 1),
            "avg_days_to_hit": (round(sum(days_to_hit_vals) / len(days_to_hit_vals), 1)
                                 if days_to_hit_vals else None),
            "avg_within_band_pct": round(sum(within_band_vals) / len(within_band_vals), 1),
            "avg_bias_pct": round(sum(bias_vals) / len(bias_vals), 1),
        })

    # Most-reached orgs first, then by sample size.
    whole_window.sort(key=lambda w: (-w["met_any_rate"], -w["n_evaluated"]))
    whole_window_overall = {
        "n_evaluated": total_eval,
        "met_any_count": total_met,
        "met_any_rate": round(total_met / total_eval * 100, 1) if total_eval else 0,
    }

    conn.close()

    return {
        "overall": overall,
        "by_checkpoint": by_checkpoint,
        "by_source": by_source,
        "symbols": symbols,
        "most_accurate_analysts": most_accurate,
        "least_accurate_analysts": least_accurate,
        "most_accurate_analysts_consensus": most_accurate_cons,
        "least_accurate_analysts_consensus": least_accurate_cons,
        "org_methodologies": org_methodologies,
        "whole_window": whole_window,
        "whole_window_overall": whole_window_overall,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _is_consensus_firm(firm):
    """True for consensus aggregates (Yahoo/FMP/oanor), which are computed means
    rather than an individual analyst firm's call.

    oanor is the only consensus source with DATED targets, so its monthly
    consensus entries ("oanor (Nasdaq consensus, 2025-06)") are the ones that
    flow into the accuracy rankings — they must be separated from real firms
    (JPMorgan, Morgan Stanley…) so an aggregate is never ranked alongside an
    individual analyst.

    FMP is consensus-only on the free tier: it publishes a current consensus
    ("FMP consensus (high/low/median)") and period-averaged targets ("FMP avg
    (last month/quarter/year, N analysts)") — no per-analyst targets. Both are
    aggregates, so every firm string starting with "FMP" is treated as consensus.
    Detection: name contains 'consensus' (Yahoo/oanor/FMP) OR starts with 'FMP'
    (covers the FMP avg rows whose name has no 'consensus' word).
    """
    f = (firm or "").lower()
    return "consensus" in f or f.startswith("fmp")


def _methodology_for(firm):
    """Return (type, methodology) describing how an analyst org's price target
    is calculated.

    Consensus orgs (whose name contains 'consensus') get the exact formula we
    compute. Individual firms get a general description of the standard valuation
    methods — firm-specific weighting is proprietary and not published by our
    data sources, so we describe the common building blocks rather than claim any
    firm's specific formula.
    """
    if _is_consensus_firm(firm):
        return {
            "org_type": "Consensus",
            "methodology": (
                "Computed, not modeled: the mean of the individual analyst targets "
                "collected for the symbol (Σ target_price ÷ N). The low/high are the "
                "MIN and MAX of those targets. Our per-source consensus pills "
                "recompute this as AVG(target_price)."
            ),
        }
    return {
        "org_type": "Analyst firm",
        "methodology": (
            "Proprietary 12-month forward target from the firm's own valuation "
            "model. Sell-side targets typically blend discounted cash flow (DCF), "
            "comparable-company / relative valuation (P/E, EV/EBITDA multiples), "
            "and a dividend discount model (DDM) for income stocks; conglomerates "
            "may use sum-of-the-parts (SOTP). The exact weighting is not publicly "
            "disclosed, so this is the general approach, not the firm's specific formula."
        ),
    }


_OANOR_MONTH_RE = re.compile(r"(\d{4}-\d{2})")


def _collapse_oanor_consensus(orgs):
    """Merge all oanor consensus rows in ``orgs`` into a single row.

    oanor produces one dated consensus target per month ("oanor (Nasdaq
    consensus, 2025-06)") plus an undated current consensus, optionally split by
    low/high band — so a dozen+ rows that differ only by month/band. They are
    the same source and the same methodology, so they collapse into one
    "oanor consensus (Nasdaq)" row: summed target_count, unioned sources,
    min/max posted dates, and a ``months`` list (month label + target count,
    including the undated rows) for an expandable per-period chip cloud.

    Non-oanor rows (Yahoo/FMP consensus, all individual firms) are unchanged.
    Returns the rows in their original order with the single oanor row placed
    where the first oanor row was.
    """
    oanor_idx = next((i for i, o in enumerate(orgs)
                      if o["org"] and re.match(r"^oanor\b", o["org"], re.I)), None)
    if oanor_idx is None:
        return orgs  # nothing to collapse

    oanor_rows = [o for o in orgs if o["org"] and re.match(r"^oanor\b", o["org"], re.I)]
    others = [o for o in orgs if not (o["org"] and re.match(r"^oanor\b", o["org"], re.I))]

    sources = []
    for s in (o.get("sources") or [] for o in oanor_rows):
        for src in s:
            if src and src not in sources:
                sources.append(src)

    months = []
    for o in oanor_rows:
        m = _OANOR_MONTH_RE.search(o["org"] or "")
        months.append({
            "label": m.group(1) if m else o["org"],
            "target_count": o.get("target_count") or 0,
        })
    # Undated (no YYYY-MM) first, then months ascending — current consensus
    # reads as the latest view, months show the history.
    months.sort(key=lambda m: m["label"] if re.match(r"\d{4}-\d{2}", m["label"]) else "~")

    firsts = [o.get("first_posted") for o in oanor_rows if o.get("first_posted")]
    lasts = [o.get("last_posted") for o in oanor_rows if o.get("last_posted")]
    collapsed = {
        "org": "oanor consensus (Nasdaq)",
        "sources": sources,
        "target_count": sum(o.get("target_count") or 0 for o in oanor_rows),
        "first_posted": min(firsts) if firsts else None,
        "last_posted": max(lasts) if lasts else None,
        "months": months,
        **_methodology_for("oanor consensus (Nasdaq)"),
    }

    # Place the collapsed row where the first oanor row was; preserve the
    # order of the non-oanor rows around it.
    result = []
    inserted = False
    for o in orgs:
        if o["org"] and re.match(r"^oanor\b", o["org"], re.I):
            if not inserted:
                result.append(collapsed)
                inserted = True
            # skip the rest of the oanor rows (folded into `collapsed`)
        else:
            result.append(o)
    if not inserted:  # safety net
        result.append(collapsed)
    return result


_FMP_WINDOW_RE = re.compile(r"^FMP\s+(consensus|avg)\s*\(([^,]+)")


def _collapse_fmp_consensus(orgs):
    """Merge all FMP consensus/average rows in ``orgs`` into a single row.

    FMP publishes a current consensus ("FMP consensus (high …, low …, median …)")
    plus period-averaged targets ("FMP avg (last month/quarter/year, N analysts)").
    Each row's label carries per-symbol numbers (high/low/median, analyst count),
    so they'd otherwise each be their own methodology row (a dozen+ for a handful
    of symbols). Collapse into one "FMP consensus" row: summed target_count,
    unioned sources, min/max posted dates, and a per-window chip cloud
    (consensus / last month / last quarter / last year + target count) reusing
    the ``months`` field so the existing expandable chip-cloud renderer applies.

    Non-FMP rows are unchanged; the collapsed row is placed where the first FMP
    row was. Returns orgs unchanged if there are no FMP rows.
    """
    def is_fmp(o):
        return bool(o.get("org")) and re.match(r"^FMP\b", o["org"], re.I)

    if not any(is_fmp(o) for o in orgs):
        return orgs

    fmp_rows = [o for o in orgs if is_fmp(o)]

    sources = []
    for s in (o.get("sources") or [] for o in fmp_rows):
        for src in s:
            if src and src not in sources:
                sources.append(src)

    # Aggregate target counts per window label.
    by_label = {}
    for o in fmp_rows:
        m = _FMP_WINDOW_RE.match(o["org"] or "")
        if m and m.group(1) == "consensus":
            label = "consensus"
        elif m:
            label = m.group(2).strip()
        else:
            label = o["org"] or "FMP"
        by_label[label] = by_label.get(label, 0) + (o.get("target_count") or 0)

    # Stable order: current consensus first, then periods chronologically.
    order = {"consensus": 0, "last month": 1, "last quarter": 2, "last year": 3}
    months = sorted(
        ({"label": lbl, "target_count": cnt} for lbl, cnt in by_label.items()),
        key=lambda m: (order.get(m["label"], 99), m["label"]),
    )

    firsts = [o.get("first_posted") for o in fmp_rows if o.get("first_posted")]
    lasts = [o.get("last_posted") for o in fmp_rows if o.get("last_posted")]
    collapsed = {
        "org": "FMP consensus",
        "sources": sources,
        "target_count": sum(o.get("target_count") or 0 for o in fmp_rows),
        "first_posted": min(firsts) if firsts else None,
        "last_posted": max(lasts) if lasts else None,
        "months": months,
        **_methodology_for("FMP consensus"),
    }

    result = []
    inserted = False
    for o in orgs:
        if is_fmp(o):
            if not inserted:
                result.append(collapsed)
                inserted = True
            # skip the rest of the FMP rows (folded into `collapsed`)
        else:
            result.append(o)
    if not inserted:  # safety net
        result.append(collapsed)
    return result


def _split_best_worst(best, worst, n=3):
    """Split per-symbol best/worst analyst lists into individual firms and
    consensus aggregates, trimming each group to ``n`` and making each pair
    mutually exclusive (a firm in best is excluded from worst; same for
    consensus). Consensus entries (oanor monthly consensus) never share a list
    with real firms.

    Args:
        best, worst: ordered lists (already deduplicated by firm) from the
            per-symbol best/worst queries.

    Returns:
        (best_firms, worst_firms, best_consensus, worst_consensus).
    """
    best_firms = [a for a in best if not _is_consensus_firm(a["analyst_firm"])]
    best_cons = [a for a in best if _is_consensus_firm(a["analyst_firm"])]
    worst_firms = [a for a in worst if not _is_consensus_firm(a["analyst_firm"])]
    worst_cons = [a for a in worst if _is_consensus_firm(a["analyst_firm"])]

    best_firms, worst_firms = _mutually_exclude(best_firms, worst_firms, n)
    best_cons, worst_cons = _mutually_exclude(best_cons, worst_cons, n)
    return best_firms, worst_firms, best_cons, worst_cons


def _mutually_exclude(best, worst, n=3):
    """Trim best/worst to ``n`` each; drop any firm in best from worst."""
    best = best[:n]
    best_names = {a["analyst_firm"] for a in best}
    worst = [a for a in worst if a["analyst_firm"] not in best_names][:n]
    return best, worst


def _rank_analysts(analyst_stats, min_samples=3, n=5):
    """Rank analyst firms overall into Most and Least Accurate lists.

    Most Accurate = highest hit rate (lands within ±5% most often), ties broken
    by smallest average error. Least Accurate = lowest hit rate, ties broken by
    largest average error. This matches the hit-rate bars the chart renders and
    the report's core "hit" concept.

    A minimum sample count is required so rankings aren't dominated by one-shot
    flukes (e.g. 1/1 = 100%); the threshold relaxes if too few firms qualify so
    the chart still populates. The two lists are mutually exclusive — a firm is
    never both Most AND Least Accurate.

    Args:
        analyst_stats: list of dicts from the overall analyst query, each with
            analyst_firm, total, hits, avg_pct_diff, avg_abs_pct_diff, hit_rate.
        min_samples: minimum checkpoint count a firm must have to qualify.
        n: number of firms per list.

    Returns:
        Tuple (most_accurate, least_accurate), each a list of up to n dicts.
    """
    if not analyst_stats:
        return [], []

    candidates = [a for a in analyst_stats if a["total"] >= min_samples]
    if len(candidates) < n:
        candidates = [a for a in analyst_stats if a["total"] >= max(2, min_samples // 2)]
    if len(candidates) < n:
        candidates = analyst_stats  # not enough data — show what we have

    # Most accurate: highest hit rate, then smallest average error.
    most = sorted(candidates, key=lambda a: (-a["hit_rate"], a["avg_abs_pct_diff"]))[:n]
    most_firms = {a["analyst_firm"] for a in most}
    # Least accurate: lowest hit rate, then largest average error.
    least = sorted(
        [a for a in candidates if a["analyst_firm"] not in most_firms],
        key=lambda a: (a["hit_rate"], -a["avg_abs_pct_diff"]),
    )[:n]
    return most, least


def _generate_insights(data):
    """Generate key insights from the data."""
    insights = []
    overall = data["overall"]
    symbols = data["symbols"]

    if not overall["total"]:
        return ["No accuracy data available yet. Run fetch and accuracy commands first."]

    hit_rate = overall["hits"] / overall["total"] * 100 if overall["total"] else 0
    miss_high_pct = overall["miss_high"] / overall["total"] * 100 if overall["total"] else 0

    # Insight 1: Overall hit rate
    if hit_rate >= 20:
        insights.append({
            "type": "positive",
            "icon": "&#10003;",
            "title": "Decent Analyst Accuracy",
            "text": f"Overall hit rate is {_fmt_sig3(hit_rate)}% — about 1 in {max(1, round(100/hit_rate))} analyst targets land within 5% of the actual price."
        })
    else:
        insights.append({
            "type": "warning",
            "icon": "&#9888;",
            "title": "Low Analyst Accuracy",
            "text": f"Overall hit rate is only {_fmt_sig3(hit_rate)}% — just {overall['hits']} out of {overall['total']} checkpoints were within 5%."
        })

    # Insight 2: Analyst bias
    if miss_high_pct > 50:
        insights.append({
            "type": "negative",
            "icon": "&#8595;",
            "title": "Strong Optimistic Bias",
            "text": f"{_fmt_sig3(miss_high_pct)}% of misses are because targets were too HIGH — analysts consistently overestimate stock prices. Average deviation is {_fmt_sig3(overall['avg_pct_diff'])}%."
        })
    elif overall["miss_low"] > overall["miss_high"]:
        insights.append({
            "type": "info",
            "icon": "&#8593;",
            "title": "Pessimistic Bias Detected",
            "text": f"More misses are because targets were too LOW ({overall['miss_low']} miss_low vs {overall['miss_high']} miss_high) — analysts underestimate these stocks."
        })

    # Insight 3: Time horizon
    cp_data = data["by_checkpoint"]
    if len(cp_data) >= 2:
        best_cp = min(cp_data, key=lambda c: c.get("avg_abs_pct_diff", 999))
        worst_cp = max(cp_data, key=lambda c: c.get("avg_abs_pct_diff", 0))
        insights.append({
            "type": "info",
            "icon": "&#128339;",
            "title": "Longer Horizons Are More Accurate",
            "text": f"The {best_cp['checkpoint_days']}-day checkpoint has {_fmt_sig3(best_cp.get('avg_abs_pct_diff', 0))}% avg deviation vs {_fmt_sig3(worst_cp.get('avg_abs_pct_diff', 0))}% for the {worst_cp['checkpoint_days']}-day. Analysts are better at long-term predictions than short-term."
        })

    # Insight 4: Best symbol
    if symbols:
        syms_with_data = [s for s in symbols if s.get("hits") and s.get("snapshot_count", 0) > 0]
        if syms_with_data:
            best_sym = max(syms_with_data, key=lambda s: s["hits"] / max(s["snapshot_count"], 1))
            best_hit_rate = min(100, best_sym["hits"] / best_sym["snapshot_count"] * 100)
            insights.append({
                "type": "positive",
                "icon": "&#9733;",
                "title": f"Most Predictable: {best_sym['symbol']}",
                "text": f"{best_sym['symbol']} ({best_sym.get('company_name', '')}) has the highest hit rate at {_fmt_sig3(best_hit_rate)}% — analysts are most accurate for this stock."
            })

            worst_sym = min(syms_with_data, key=lambda s: s["hits"] / max(s["snapshot_count"], 1))
            worst_hit_rate = min(100, worst_sym["hits"] / worst_sym["snapshot_count"] * 100)
            insights.append({
                "type": "negative",
                "icon": "&#9734;",
                "title": f"Least Predictable: {worst_sym['symbol']}",
                "text": f"{worst_sym['symbol']} ({worst_sym.get('company_name', '')}) has the lowest hit rate at {_fmt_sig3(worst_hit_rate)}% with {_fmt_sig3(worst_sym.get('avg_abs_pct_diff', 0))}% avg deviation."
            })

    # Insight 5: Target vs actual gap — group every >20% gap into ONE compact
    # insight (one card listing all of them) instead of one card per symbol.
    gap_entries = []
    for sym in symbols:
        if not (sym.get("latest_price") and sym.get("source_consensus")):
            continue
        for sc in sym["source_consensus"]:
            if sc.get("consensus_target") and sym["latest_price"]:
                gap = (sc["consensus_target"] - sym["latest_price"]) / sym["latest_price"] * 100
                if abs(gap) > 20:
                    gap_entries.append((sym["symbol"], gap))
                    break  # one gap per symbol (top-consensus source)
    if gap_entries:
        gap_entries.sort(key=lambda e: abs(e[1]), reverse=True)
        any_big = any(abs(g) > 40 for _, g in gap_entries)
        parts = [f"{sym} {'+' if g > 0 else ''}{g:.0f}%" for sym, g in gap_entries]
        insights.append({
            "type": "warning" if any_big else "info",
            "icon": "&#128200;",
            "title": f"Big Gap: {len(gap_entries)} targets vs price (>20%)",
            "text": "Consensus targets diverge >20% from current price (largest gap first): "
                    + ", ".join(parts) + ". + = target above price (analysts bullish); − = below."
        })

    return insights


def _build_html(data, insights):
    """Build the complete HTML report."""
    symbols_json = json.dumps(data["symbols"], default=str)
    insights_json = json.dumps(insights, default=str)
    data_json = json.dumps({
        "overall": data["overall"],
        "by_checkpoint": data["by_checkpoint"],
        "by_source": data["by_source"],
        "most_accurate_analysts": data["most_accurate_analysts"],
        "least_accurate_analysts": data["least_accurate_analysts"],
        "most_accurate_analysts_consensus": data["most_accurate_analysts_consensus"],
        "least_accurate_analysts_consensus": data["least_accurate_analysts_consensus"],
        "org_methodologies": data["org_methodologies"],
        "whole_window": data["whole_window"],
        "whole_window_overall": data["whole_window_overall"],
    }, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Target Price Tracker — Accuracy Report</title>
<style>
:root {{
  --bg: #0f1117;
  --card: #1a1d27;
  --card-hover: #222632;
  --border: #2a2e3a;
  --text: #e1e4ea;
  --text-dim: #8b8f9a;
  --accent: #6c8cff;
  --accent-dim: #3a4f8f;
  --green: #34d399;
  --green-dim: #065f46;
  --red: #f87171;
  --red-dim: #7f1d1d;
  --orange: #fbbf24;
  --orange-dim: #78350f;
  --blue: #60a5fa;
  --date: #7dd3fc;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  padding: 0;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: 4px; }}
h2 {{ font-size: 1.3rem; font-weight: 600; margin-bottom: 16px; color: var(--accent); }}
.subtitle {{ color: var(--text-dim); font-size: 0.9rem; margin-bottom: 24px; }}

/* Report description box */
.report-desc {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 24px;
  color: var(--text-dim);
  font-size: 0.88rem;
  line-height: 1.65;
}}
.report-desc strong {{ color: var(--text); }}

/* Summary cards row */
.summary-row {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}}
.summary-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  text-align: center;
  transition: border-color 0.2s;
}}
.summary-card:hover {{ border-color: var(--accent); }}
.summary-card .label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin-bottom: 8px; }}
.summary-card .value {{ font-size: 1.8rem; font-weight: 700; }}
.summary-card .sub {{ font-size: 0.8rem; color: var(--text-dim); margin-top: 4px; }}
.summary-card .def {{ font-size: 0.72rem; color: var(--text-dim); margin-top: 8px; line-height: 1.4; opacity: 0.85; border-top: 1px solid var(--border); padding-top: 8px; }}
.text-green {{ color: var(--green); }}
.text-red {{ color: var(--red); }}
.text-orange {{ color: var(--orange); }}
.text-blue {{ color: var(--blue); }}

/* Insights */
.insights {{ margin-bottom: 32px; }}
.insight-item {{
  display: flex;
  gap: 12px;
  align-items: flex-start;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 18px;
  margin-bottom: 10px;
  border-left: 3px solid var(--accent);
  transition: border-color 0.2s;
}}
.insight-item.positive {{ border-left-color: var(--green); }}
.insight-item.negative {{ border-left-color: var(--red); }}
.insight-item.warning {{ border-left-color: var(--orange); }}
.insight-item.info {{ border-left-color: var(--blue); }}
.insight-icon {{ font-size: 1.2rem; flex-shrink: 0; margin-top: 2px; }}
.insight-title {{ font-weight: 600; font-size: 0.95rem; margin-bottom: 2px; }}
.insight-text {{ font-size: 0.85rem; color: var(--text-dim); }}

/* Checkpoint comparison */
.checkpoint-table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--card);
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 32px;
}}
.checkpoint-table th, .checkpoint-table td {{
  padding: 12px 16px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}}
.checkpoint-table th {{
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  background: rgba(0,0,0,0.2);
}}
.checkpoint-table tr:last-child td {{ border-bottom: none; }}

/* Bar chart */
.bar-chart {{ margin: 4px 0; }}
.bar-track {{
  background: rgba(255,255,255,0.06);
  border-radius: 4px;
  height: 8px;
  overflow: hidden;
  position: relative;
}}
.bar-fill {{
  height: 100%;
  border-radius: 4px;
  transition: width 0.6s ease;
}}

/* Symbol cards */
.symbols-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 20px;
  margin-bottom: 32px;
}}

/* Symbols with no analyst targets, stacked vertically into one card-sized box
   (sits in one grid cell, the same width as a normal symbol card). */
.no-targets-strip {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 12px 16px;
  align-self: start;
}}
.no-targets-strip .strip-label {{
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  margin-bottom: 6px;
}}
.notile {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
}}
.notile:last-child {{ border-bottom: none; }}
.notile .notile-ticker {{ font-size: 0.95rem; font-weight: 700; }}
.notile .notile-price {{ font-size: 0.8rem; color: var(--text-dim); margin-left: 8px; }}
.notile .notile-tag {{
  font-size: 0.58rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-dim);
}}
.symbol-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  transition: border-color 0.2s, transform 0.15s;
}}
.symbol-card:hover {{ border-color: var(--accent); transform: translateY(-2px); }}
.symbol-header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
}}
.symbol-ticker {{ font-size: 1.35rem; font-weight: 700; line-height: 1.1; }}
.symbol-company {{ font-size: 0.78rem; color: var(--text-dim); }}
.symbol-sector {{
  font-size: 0.68rem;
  background: var(--accent-dim);
  color: var(--accent);
  padding: 1px 8px;
  border-radius: 12px;
  margin-top: 3px;
  display: inline-block;
}}
.symbol-price {{
  text-align: right;
}}
.symbol-price .price {{ font-size: 1.25rem; font-weight: 700; line-height: 1.1; }}
/* Date + verdict share one right-aligned row to save a line */
.symbol-price .price-sub {{
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
}}
.symbol-price .date {{ font-size: 0.7rem; color: var(--date); font-weight: 600; }}

/* Per-symbol price chart (inline SVG: close line + high/low band + targets) */
.sym-chart {{
  margin: 0 0 10px;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px 12px 6px;
}}
.sym-chart svg {{ width: 100%; height: auto; display: block; }}
.chart-legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 0.7rem;
  color: var(--text-dim);
  margin-top: 8px;
}}
.chart-legend .sw {{
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-right: 4px;
  vertical-align: middle;
}}
.chart-na {{
  font-size: 0.8rem;
  color: var(--text-dim);
  padding: 18px;
  text-align: center;
  font-style: italic;
}}

/* 52-week high/low range bar (sits under the current price in the header) */
.week52 {{ margin-top: 4px; text-align: right; }}
.week52 svg {{ display: block; margin-left: auto; width: 150px; max-width: 100%; }}

/* Consensus verdict badge (Buy more / Hold / Sell off) */
.verdict {{
  display: inline-block;
  padding: 2px 9px;
  border-radius: 6px;
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}}
.verdict .verdict-gap {{ font-weight: 600; margin-left: 4px; opacity: 0.9; }}
.verdict-buy {{ background: var(--green-dim); color: var(--green); }}
.verdict-hold {{ background: var(--orange-dim); color: var(--orange); }}
.verdict-sell {{ background: var(--red-dim); color: var(--red); }}
.verdict-none {{ background: rgba(255,255,255,0.06); color: var(--text-dim); }}

/* No-targets placeholder (symbol has no analyst price targets yet) */
.no-targets {{
  margin-top: 16px;
  padding: 14px 16px;
  background: rgba(255,255,255,0.03);
  border: 1px dashed var(--border);
  border-radius: 8px;
  color: var(--text-dim);
  font-size: 0.82rem;
  line-height: 1.5;
}}
.no-targets code {{
  background: rgba(108,140,255,0.12);
  color: var(--accent);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 0.78rem;
}}

/* Consensus row */
.consensus-row {{
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}}
.consensus-pill {{
  background: rgba(108, 140, 255, 0.1);
  border: 1px solid rgba(108, 140, 255, 0.2);
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 0.8rem;
}}
.consensus-pill .source {{ color: var(--accent); font-weight: 600; }}
.consensus-pill .target {{ font-weight: 700; }}

/* Accuracy mini-table */
.accuracy-row {{
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}}
.accuracy-row:last-child {{ border-bottom: none; }}
.accuracy-label {{ font-size: 0.8rem; width: 70px; color: var(--text-dim); }}
.accuracy-bar {{ flex: 1; }}
.accuracy-num {{ font-size: 0.85rem; font-weight: 600; width: 60px; text-align: right; }}

/* Analyst list */
.analyst-list {{ margin-top: 12px; }}
.analyst-item {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 0.8rem;
  border-bottom: 1px solid rgba(255,255,255,0.03);
  gap: 10px;
}}
.analyst-item:last-child {{ border-bottom: none; }}
.analyst-left {{ display: flex; flex-direction: column; min-width: 0; }}
.analyst-right {{ display: flex; align-items: center; gap: 10px; flex-shrink: 0; }}
.analyst-firm {{ color: var(--text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.analyst-date {{ font-size: 0.68rem; color: var(--date); font-weight: 600; }}
.analyst-range {{ font-size: 0.7rem; color: var(--text-dim); margin-top: 4px; line-height: 1.45; }}
.analyst-range .range {{ color: var(--text); font-weight: 500; }}
.analyst-range .range-meta {{ color: var(--text-dim); font-weight: 400; }}
.analyst-range .range-na {{ color: var(--text-dim); font-style: italic; }}
.date-hl {{ color: var(--date); font-weight: 600; }}
.analyst-target {{ font-weight: 600; }}
/* Checkpoint chip on each best/worst analyst target: which checkpoint the
   representative call was evaluated at, and whether it hit. */
.analyst-cp {{
  font-size: 0.62rem;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  white-space: nowrap;
  border: 1px solid currentColor;
  opacity: 0.92;
}}
.analyst-rating {{
  font-size: 0.7rem;
  padding: 1px 8px;
  border-radius: 4px;
  font-weight: 600;
}}
.rating-buy {{ background: var(--green-dim); color: var(--green); }}
.rating-hold {{ background: var(--orange-dim); color: var(--orange); }}
.rating-sell {{ background: var(--red-dim); color: var(--red); }}
.rating-other {{ background: var(--accent-dim); color: var(--accent); }}

/* Sub-group label separating consensus aggregates from individual analyst firms
   (in the Most/Least Accurate panels and per-symbol best/worst lists). */
.analyst-subgroup {{
  margin: 12px 0 6px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-dim);
  font-weight: 600;
}}
/* Section-divider row in the Whole-Window Target Accuracy table. */
tr.ww-divider td {{
  background: var(--bg-elevated, rgba(0,0,0,0.03));
  border-top: 1px solid var(--border);
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-dim);
  font-weight: 600;
  padding: 8px 12px;
}}

/* Best/worst section */
.bw-section {{
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid rgba(255,255,255,0.06);
}}
.bw-label {{
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}}
.bw-label.best {{ color: var(--green); }}
.bw-label.worst {{ color: var(--red); }}

/* Filter tabs */
.filter-tabs {{
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}}
.filter-tab {{
  padding: 6px 16px;
  border-radius: 8px;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text-dim);
  transition: all 0.2s;
}}
.filter-tab:hover {{ border-color: var(--accent); color: var(--text); }}
.filter-tab.active {{ background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }}

/* Overall analyst accuracy panels */
.analyst-panels {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 32px;
}}
.analyst-panel {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
}}
.analyst-panel h3 {{
  font-size: 0.95rem;
  font-weight: 600;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.analyst-panel.best h3 {{ color: var(--green); }}
.analyst-panel.worst h3 {{ color: var(--red); }}
.analyst-row {{
  padding: 10px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}}
.analyst-row:last-child {{ border-bottom: none; }}
.analyst-row-head {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}}
.analyst-firm-name {{ font-size: 0.85rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.analyst-hit {{ font-size: 0.8rem; font-weight: 700; }}
.analyst-meta {{ font-size: 0.72rem; color: var(--text-dim); margin-top: 4px; }}
.analyst-call {{ font-size: 0.7rem; color: var(--text-dim); margin-top: 4px; line-height: 1.45; }}
.analyst-call strong {{ color: var(--text); font-weight: 600; }}
.analyst-bar {{ height: 6px; border-radius: 4px; background: rgba(255,255,255,0.06); overflow: hidden; }}
.analyst-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.6s ease; }}
.empty-note {{ color: var(--text-dim); font-size: 0.85rem; padding: 8px 0; }}

/* How price targets are calculated */
.method-cards {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 14px;
}}
/* Brief per-source description for the consensus data sources. */
.source-notes {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 0 0 16px;
}}
.source-note {{
  background: var(--card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 0.78rem;
  line-height: 1.5;
  color: var(--text-dim);
}}
.source-note .src-name {{ color: var(--text); font-weight: 600; }}
.source-note .src-tag {{ font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--accent); margin-left: 6px; }}
/* Footnote on comparable public services and how this report differs. */
.comparable-note {{
  background: var(--card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--text-dim);
  border-radius: 8px;
  padding: 12px 16px;
  margin: 16px 0 0;
  font-size: 0.78rem;
  line-height: 1.55;
  color: var(--text-dim);
}}
.comparable-note .cn-title {{ color: var(--text); font-weight: 600; margin-bottom: 4px; }}
.comparable-note strong {{ color: var(--text); font-weight: 600; }}
.method-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}}
.method-card .mc-head {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}}
.method-card .mc-count {{ font-size: 0.78rem; color: var(--text-dim); }}
.method-card .mc-body {{ font-size: 0.82rem; color: var(--text-dim); line-height: 1.55; }}
.method-key {{
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  font-size: 0.76rem;
  color: var(--text-dim);
  margin-bottom: 18px;
  padding: 10px 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
}}
.method-key-title {{ color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.7rem; margin-right: 4px; }}
.method-key strong {{ color: var(--accent); margin-right: 3px; }}

/* Methodology table: scroll the container (not the tbody), normal table layout
   so columns stay aligned and the sticky header renders correctly. */
.method-table-wrap {{
  max-height: 480px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 32px;
}}
.method-table {{
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  background: var(--card);
  margin: 0;
  table-layout: fixed;
}}
.method-table th, .method-table td {{
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  font-size: 0.85rem;
  vertical-align: top;
  word-wrap: break-word;
  overflow-wrap: anywhere;
}}
.method-table thead th {{
  position: sticky;
  top: 0;
  z-index: 1;
  background: #11141c;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  font-weight: 600;
}}
.method-table tr:last-child td {{ border-bottom: none; }}
.method-table td.org {{ font-weight: 600; color: var(--text); word-break: break-word; }}
.method-table .type-pill {{
  font-size: 0.7rem;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 600;
  white-space: nowrap;
  display: inline-block;
}}
.type-consensus {{ background: var(--accent-dim); color: var(--accent); }}
.type-firm {{ background: rgba(255,255,255,0.06); color: var(--text-dim); }}
.method-table td.method {{ font-size: 0.8rem; color: var(--text-dim); line-height: 1.5; }}
.method-table td.num {{ font-variant-numeric: tabular-nums; }}

/* Consensus Picks table (compact, one row per symbol; click to open the card). */
.consensus-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}}
.consensus-table th {{
  text-align: left;
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-dim);
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; background: var(--card);
}}
.consensus-table td {{
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}}
.consensus-table tr.cp-row {{ cursor: pointer; }}
.consensus-table tr.cp-row:hover {{ background: rgba(255,255,255,0.04); }}
.consensus-table .cp-ticker {{ font-weight: 700; }}
.consensus-table .cp-target {{ font-weight: 600; }}
.consensus-table .cp-src {{ color: var(--text-dim); font-size: 0.72rem; }}
.consensus-table .cp-na {{ color: var(--text-dim); font-style: italic; }}
.consensus-table .cp-conv {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.consensus-table .cp-conv-na {{ color: var(--text-dim); font-style: italic; font-weight: 400; }}
.consensus-table .verdict {{ font-size: 0.66rem; padding: 2px 8px; }}

/* Collapsed analyst-firm aggregate row (chip cloud behind a <details>). */
.method-table tr.firm-aggregate td {{ padding: 0; }}
.method-table tr.firm-aggregate details {{ padding: 10px 14px; }}
.method-table tr.firm-aggregate summary {{
  cursor: pointer; list-style: none; display: flex; align-items: center; gap: 10px;
  font-size: 0.85rem;
}}
.method-table tr.firm-aggregate summary::-webkit-details-marker {{ display: none; }}
.method-table tr.firm-aggregate summary::before {{
  content: '▸'; color: var(--text-dim); transition: transform .15s; display: inline-block; font-size: 0.7rem;
}}
.method-table tr.firm-aggregate details[open] summary::before {{ transform: rotate(90deg); }}
.fa-title {{ font-weight: 600; color: var(--text); }}
.fa-meta {{ font-size: 0.78rem; color: var(--text-dim); }}
.firm-chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
.firm-chip {{
  font-size: 0.72rem; background: rgba(255,255,255,0.05); border: 1px solid var(--border);
  border-radius: 5px; padding: 2px 7px; color: var(--text-dim); white-space: nowrap;
}}
.firm-chip b {{ color: var(--text); font-variant-numeric: tabular-nums; }}

/* Whole-window references (where the evaluation measures come from) */
.ww-refs {{
  margin-top: 14px;
  padding: 12px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 0.78rem;
  color: var(--text-dim);
  line-height: 1.55;
}}
.ww-refs-title {{
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  margin-bottom: 8px;
}}
.ww-refs ul {{ margin: 0 0 10px 0; padding-left: 18px; }}
.ww-refs li {{ margin-bottom: 4px; }}
.ww-refs strong {{ color: var(--text); }}
.ww-refs-links {{ display: flex; flex-direction: column; gap: 5px; padding-left: 18px; }}
.ww-refs-links a {{ color: var(--accent); text-decoration: none; word-break: break-word; }}
.ww-refs-links a:hover {{ text-decoration: underline; }}

/* Per-symbol whole-window accuracy block (inside each symbol card) */
.ww-sym {{
  margin-bottom: 10px;
  padding: 6px 10px;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: 8px;
}}
.ww-sym-title {{
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  margin-bottom: 4px;
}}
.ww-sym-stats {{
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 0.78rem;
  color: var(--text-dim);
}}
.ww-sym-stats strong {{ color: var(--text); font-weight: 600; }}
.ww-sym-stats .dim {{ color: var(--text-dim); font-size: 0.72rem; }}

/* Responsive */
@media (max-width: 768px) {{
  .symbols-grid {{ grid-template-columns: 1fr; }}
  .summary-row {{ grid-template-columns: repeat(2, 1fr); }}
  .analyst-panels {{ grid-template-columns: 1fr; }}
  .method-cards {{ grid-template-columns: 1fr; }}
  .source-notes {{ grid-template-columns: 1fr; }}
  .container {{ padding: 12px; }}
}}

/* Footer */
.footer {{
  text-align: center;
  padding: 32px 0 16px;
  color: var(--text-dim);
  font-size: 0.75rem;
  border-top: 1px solid var(--border);
  margin-top: 32px;
}}
/* Run timing footer: per-step + e2e elapsed, populated by refresh.py after the
   run completes (so the report step's own time is included). Small, muted. */
.run-timing {{
  max-width: 1200px;
  margin: 24px auto 40px;
  padding: 10px 16px;
  font-size: 0.7rem;
  line-height: 1.5;
  color: var(--text-dim);
  border-top: 1px dashed var(--border);
}}
.run-timing .rt-title {{ font-weight: 600; color: var(--text-dim); margin-bottom: 4px; }}
.run-timing table {{ border-collapse: collapse; margin-top: 4px; }}
.run-timing td {{ padding: 1px 14px 1px 0; white-space: pre; }}
.run-timing td.rt-step {{ text-align: left; }}
.run-timing td.rt-secs {{ text-align: right; font-variant-numeric: tabular-nums; }}
.run-timing td.rt-pct {{ text-align: right; font-variant-numeric: tabular-nums; color: var(--text-dim); }}
.run-timing tr.rt-total td {{ font-weight: 600; color: var(--text); border-top: 1px solid var(--border); padding-top: 3px; }}
.run-timing .rt-stamp {{ margin-top: 6px; font-size: 0.66rem; }}
</style>
</head>
<body>
<div class="container">
  <h1>Stock Target Price Tracker</h1>
  <p class="subtitle">Analyst accuracy report &mdash; generated {data['generated_at']}</p>

  <!-- Report description -->
  <div class="report-desc">
    <strong>What this report shows:</strong> For every analyst target price we collect, we compare the
    target against the stock's <em>actual</em> price at four checkpoints &mdash; <strong>30</strong>,
    <strong>90</strong>, <strong>180</strong>, and <strong>365</strong> days after the target was issued &mdash; and measure how
    close the prediction was. A target <strong>hits</strong> when the actual price lands within
    <strong>&plusmn;5%</strong> of the target; otherwise it misses (too high = analyst too optimistic, too
    low = analyst too pessimistic). The cards below summarize accuracy across all tracked symbols; each
    symbol card then breaks down its own targets, per-horizon accuracy, and most/least accurate analysts.
    A separate <strong>Whole-Window Target Accuracy</strong> view evaluates each target against the stock's
    entire year-long price path (whether it was <em>ever</em> reached, not just at the checkpoints).
  </div>

  <!-- Consensus Picks: one compact row per symbol, the actionable view -->
  <div>
    <h2>Consensus Picks</h2>
    <p class="subtitle" style="margin-top:-8px;margin-bottom:14px">
      Each symbol's analyst <strong>consensus target</strong> (mean of the source with the most analyst targets)
      vs the <strong>current price</strong>. <strong>Conviction</strong> = (implied move + analyst bias) &times;
      symbol hit rate &mdash; a single sortable signal that rewards large <em>realistic</em> upside at stocks whose
      targets tend to get hit (and downweights names where analysts systematically overshoot or rarely hit).
      Sorted by conviction; click a row to expand that symbol's card. <strong style="color:var(--green)">BUY MORE</strong>
      &ge; +10%, <strong style="color:var(--orange)">HOLD</strong> within &plusmn;10%,
      <strong style="color:var(--red)">SELL OFF</strong> &le; &minus;10%.
    </p>
    <div class="method-table-wrap" style="max-height:520px">
      <table class="consensus-table" id="consensusPicksTable"></table>
    </div>
  </div>

  <!-- Overall Summary -->
  <div class="summary-row" id="summaryRow"></div>

  <!-- Insights -->
  <div class="insights">
    <h2>Key Insights</h2>
    <div id="insightsContainer"></div>
  </div>

  <!-- Overall Analyst Accuracy -->
  <div>
    <h2>Analyst Accuracy (Overall)</h2>
    <p class="subtitle" style="margin-top:-8px;margin-bottom:18px">Most and least accurate analyst firms across <em>all</em> symbols and checkpoints. Bars show hit rate; firms with fewer calls are filtered to avoid one-prediction flukes.</p>
    <div class="analyst-panels">
      <div class="analyst-panel best">
        <h3><span style="font-size:1.05rem">&#9733;</span> Most Accurate Analysts</h3>
        <div id="mostAccurateAnalysts"></div>
      </div>
      <div class="analyst-panel worst">
        <h3><span style="font-size:1.05rem">&#9734;</span> Least Accurate Analysts</h3>
        <div id="leastAccurateAnalysts"></div>
      </div>
    </div>
  </div>

  <!-- How price targets are calculated -->
  <div>
    <h2>How Price Targets Are Calculated</h2>
    <p class="subtitle" style="margin-top:-8px;margin-bottom:16px">
      Two target types are tracked: <em>consensus</em> aggregates (computed) and <em>individual analyst
      firm</em> targets (proprietary). How each type is calculated is shown once below; the table then lists
      every org and its coverage. Per-source consensus pills elsewhere use <code>consensus = AVG(target_price)</code>.
    </p>

    <div class="source-notes">
      <div class="source-note">
        <span class="src-name">oanor</span><span class="src-tag">consensus &middot; dated</span><br>
        oanor Analyst API serving Nasdaq-sourced analyst data. Provides a current low/mean/high consensus
        target and, uniquely, month-by-month <em>dated</em> consensus history &mdash; the only consensus
        source with dated targets, which is what feeds the 30/90/180/365-day checkpoint accuracy engine.
      </div>
      <div class="source-note">
        <span class="src-name">FMP</span><span class="src-tag">consensus &middot; averages</span><br>
        Financial Modeling Prep API (free tier). Consensus only: a current high/low/median target plus
        period-averaged targets over the last month, quarter, and year (each with its analyst count). No
        per-analyst targets, so every FMP row is an aggregate.
      </div>
    </div>

    <div id="methodSummary" class="method-cards"></div>

    <div class="method-key">
      <span class="method-key-title">Building blocks</span>
      <span><strong>DCF</strong> discounted cash flow</span>
      <span><strong>Comparables</strong> peer multiples (P/E, EV/EBITDA)</span>
      <span><strong>DDM</strong> dividend discount model</span>
      <span><strong>SOTP</strong> sum-of-the-parts</span>
    </div>

    <div class="method-table-wrap">
      <table class="method-table" id="methodologyTable"></table>
    </div>

    <div class="comparable-note">
      <div class="cn-title">Comparable services &amp; how this report differs</div>
      <strong>AnaChart</strong> tracks ~4,000 analysts over 20 years and ranks them by 12-month
      price-target <em>hit ratio</em> &mdash; the closest public analogue to the per-target hit logic used
      here. <strong>Eidolum</strong> scores each call HIT / NEAR / MISS at window expiry (timestamped, no
      revisions). <strong>TipRanks</strong>, <strong>MarketBeat</strong>, <strong>WallStreetZen</strong>, and
      <strong>Quiver Quantitative</strong> instead rank analysts by composite scores or Buy-call ROI over
      short/long windows, not per-target fulfillment.<br><br>
      <strong>Differentiator:</strong> this report publishes <em>both</em> measures &mdash; fixed-horizon
      accuracy at 30/90/180/365 days (TPMetEND) <em>and</em> a persisted ever-hit flag for whether the price
      touched the target at any point in the 365-day window (TPMetANY, direction-agnostic intraday touch) &mdash;
      per the academic methodology (Asquith, Bonini, Bilinski et al.). Public services typically expose only one.
    </div>
  </div>

  <!-- Whole-Window Target Accuracy -->
  <div>
    <h2>Whole-Window Target Accuracy</h2>
    <p class="subtitle" style="margin-top:-8px;margin-bottom:8px">
      Instead of a single 30/90/180/365-day checkpoint, this evaluates each target against the stock's
      <em>entire</em> price path over the year after it was issued: whether the price <strong>ever
      reached</strong> the target (Met_any), how many days that took, the share of days the price stayed
      <strong>within &plusmn;5%</strong> of the target, and the mean signed deviation (<strong>bias</strong>).
      A target that missed at day 365 may still have been touched mid-window.
    </p>
    <p class="subtitle" id="wholeWindowOverall" style="margin-top:0;margin-bottom:16px"></p>
    <div class="method-table-wrap" style="max-height:560px">
      <table class="method-table" id="wholeWindowTable"></table>
    </div>

    <div class="ww-refs">
      <div class="ww-refs-title">Where these measures come from</div>
      <ul>
        <li><strong>Met_any</strong> &amp; <strong>time-to-hit</strong> &mdash; the &ldquo;target reached at any point during the horizon&rdquo; idea (TPMETANY) is from Bradshaw, Brown &amp; Huang (2013) and Bilinski, Lyssimachou &amp; Walker (2013); days-to-hit is a first-passage variant of the same concept.</li>
        <li><strong>Within-band %</strong> &mdash; a continuous, whole-window analogue of our own &plusmn;5% checkpoint hit rule. Averaging forecast error across a window follows standard forecast-accuracy practice (Hyndman &amp; Athanasopoulos); see Svetunkov (2024) for why percentage-error measures need care.</li>
        <li><strong>Mean signed % error (bias)</strong> &mdash; the signed-forecast-error / bias dimension is one of the five in Lee et al. (2024), who document an upward bias in analyst targets.</li>
      </ul>
      <div class="ww-refs-links">
        <a href="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=698581" target="_blank" rel="noopener">Bradshaw, Brown &amp; Huang &mdash; Do Sell-Side Analysts Exhibit Differential Target Price Forecasting Ability? (Rev. of Accounting Studies, 2013)</a>
        <a href="https://eprints.lancs.ac.uk/id/eprint/71029/1/Analyst_TP_accuracy_Feb2012.pdf" target="_blank" rel="noopener">Bilinski, Lyssimachou &amp; Walker &mdash; Target Price Accuracy: International Evidence (The Accounting Review, 2013)</a>
        <a href="https://www.sciencedirect.com/science/article/abs/pii/S1059056024000960" target="_blank" rel="noopener">Lee et al. &mdash; A multi-dimensional assessment of the accuracy of analyst target prices (2024)</a>
        <a href="https://otexts.com/fpp3/accuracy.html" target="_blank" rel="noopener">Hyndman &amp; Athanasopoulos &mdash; Forecasting: Principles and Practice (3rd ed.), &sect;5.8 Evaluating point forecast accuracy</a>
        <a href="https://openforecast.org/wp-content/uploads/2024/07/Svetunkov-2024-Point-Forecast-Evaluation-State-of-the-Art.pdf" target="_blank" rel="noopener">Svetunkov &mdash; Point Forecast Evaluation: State of the Art (2024)</a>
      </div>
    </div>
  </div>

  <!-- Checkpoint Comparison -->
  <div>
    <h2>Accuracy by Time Horizon</h2>
    <table class="checkpoint-table" id="checkpointTable"></table>
  </div>

  <!-- Filter -->
  <div style="margin-top:32px">
    <h2 id="symbolsHeading">All Symbols</h2>

    <div class="method-key">
      <span class="method-key-title">Verdict</span>
      <span>each symbol's <strong>Buy more / Hold / Sell off</strong> badge compares the analyst <strong>consensus target</strong> &mdash; the mean of the source with the most analyst targets &mdash; to the <strong>current price</strong>:</span>
      <span><code style="background:rgba(108,140,255,0.12);color:var(--accent);padding:1px 5px;border-radius:4px;font-size:0.76rem">gap = (target &minus; current) / current &times; 100</code></span>
      <span><strong style="color:var(--green)">BUY MORE</strong> gap &ge; +10%</span>
      <span><strong style="color:var(--orange)">HOLD</strong> within &plusmn;10%</span>
      <span><strong style="color:var(--red)">SELL OFF</strong> gap &le; &minus;10%</span>
      <span><strong style="color:var(--text-dim)">NO TARGETS</strong> when no analyst targets have been fetched for the symbol</span>
    </div>

    <div class="filter-tabs" id="filterTabs"></div>
  </div>

  <!-- Symbol Cards -->
  <div class="symbols-grid" id="symbolsGrid"></div>

  <div class="footer">
    Stock Target Price Tracker &mdash; Data from Yahoo Finance &amp; MarketBeat &mdash; Report generated {data['generated_at']}
  </div>
</div>

<script>
const DATA = {data_json};
const SYMBOLS = {symbols_json};
const INSIGHTS = {insights_json};

// Format a number to at most 3 significant digits (e.g. 53.37 -> "53.4",
// 36.4 -> "36.4", 4.8 -> "4.8", 0 -> "0"). Trailing zeros are stripped.
function fmtSig3(n) {{
  n = Number(n);
  if (!isFinite(n) || n === 0) return '0';
  return parseFloat(n.toPrecision(3)).toString();
}}

// Format a price range object {{low, high, start, end, n_points}} into a short
// label, or a muted "unavailable" note when there is no price data in the window.
function rangeHtml(r) {{
  if (!r) return '<span class="range-na">price history unavailable</span>';
  const span = r.low === r.high
    ? '$' + r.low.toFixed(2)
    : '$' + r.low.toFixed(2) + '–$' + r.high.toFixed(2);
  return '<span class="range">' + span + ' <span class="range-meta"><span class="date-hl">' + r.start + '</span>&rarr;<span class="date-hl">' + r.end + '</span> (' + r.n_points + 'd)</span></span>';
}}

// Consensus pick for a symbol: the consensus target (mean of the source with
// the most analyst targets) vs the current price, as an implied move + a
// BUY MORE / HOLD / SELL OFF verdict. Shared by the Consensus Picks table and
// each card's collapsed summary line so the two always agree.
// Returns {{target, gap, verdict, vcls}} or null when there is no consensus or
// no current price.
function consensusPick(sym) {{
  if (!sym.latest_price || !sym.source_consensus || !sym.source_consensus.length) return null;
  const consensus = sym.source_consensus.reduce(
    (best, sc) => sc.analyst_count > (best.analyst_count || 0) ? sc : best,
    sym.source_consensus[0]);
  if (!consensus || !consensus.consensus_target) return null;
  const gap = (consensus.consensus_target - sym.latest_price) / sym.latest_price * 100;
  let verdict, vcls;
  if (gap >= 10) {{ verdict = 'BUY MORE'; vcls = 'verdict-buy'; }}
  else if (gap <= -10) {{ verdict = 'SELL OFF'; vcls = 'verdict-sell'; }}
  else {{ verdict = 'HOLD'; vcls = 'verdict-hold'; }}
  return {{ target: consensus.consensus_target, source: consensus.source,
            analyst_count: consensus.analyst_count || 0, gap, verdict, vcls }};
}}

// Conviction score: one sortable signal combining the *realistic* implied move
// with how reliably this stock's analyst targets actually get hit.
//   conviction = biasAdjustedGap &times; symbolHitRate
//   biasAdjustedGap = gap + avg_pct_diff   — shave the implied move by the
//                     stock's typical analyst overshoot (avg_pct_diff is mean
//                     actual−target; negative = analysts too optimistic, so it
//                     lowers the gap toward what's actually been realized)
//   symbolHitRate   = hits / snapshot_count (0..1) — share of this symbol's
//                     checkpoint comparisons that landed within ±5% of target
// Returns {{value, adjGap, hitRate, snaps}} or null when there are no
// snapshots (can't score reliability) or no pick.
function convictionScore(sym, pick) {{
  if (!pick) return null;
  const snaps = sym.snapshot_count || 0;
  if (!snaps) return null;
  const hitRate = Math.min(1, (sym.hits || 0) / snaps);
  const adjGap = pick.gap + (sym.avg_pct_diff || 0);
  return {{ value: adjGap * hitRate, adjGap, hitRate, snaps }};
}}

// ── Render Consensus Picks table (one row per symbol) ──
function renderConsensusPicks() {{
  const el = document.getElementById('consensusPicksTable');
  const rows = SYMBOLS.map(sym => {{
    const pick = consensusPick(sym);
    return {{ sym, pick, score: convictionScore(sym, pick) }};
  }});
  // Sort by conviction score desc (strongest realistic-conviction buys on
  // top). Unscored rows (a pick but no checkpoint history) follow, ordered by
  // implied move; symbols with no consensus trail last by ticker.
  rows.sort((a, b) => {{
    const sa = a.score ? a.score.value : null;
    const sb = b.score ? b.score.value : null;
    if (sa !== null && sb === null) return -1;
    if (sb !== null && sa === null) return 1;
    if (sa !== null && sb !== null) return sb - sa;
    if (a.pick && !b.pick) return -1;
    if (b.pick && !a.pick) return 1;
    if (a.pick && b.pick) return b.pick.gap - a.pick.gap;
    return a.sym.symbol.localeCompare(b.sym.symbol);
  }});
  el.innerHTML = `
    <colgroup>
      <col style="width:13%"><col style="width:15%"><col style="width:23%"><col style="width:13%"><col style="width:13%"><col style="width:23%">
    </colgroup>
    <thead>
      <tr><th>Ticker</th><th>Price</th><th>Consensus</th><th>Implied</th><th title="conviction = (implied move + analyst bias) &times; symbol hit rate">Conv.</th><th>Verdict</th></tr>
    </thead>
    <tbody>
      ${{rows.map(r => {{
        const sym = r.sym, p = r.pick, s = r.score;
        const price = sym.latest_price != null ? '$' + sym.latest_price.toFixed(2) : 'N/A';
        if (!p) {{
          return `<tr class="cp-row cp-none" data-symbol="${{sym.symbol}}">
            <td class="cp-ticker">${{sym.symbol}}</td>
            <td class="num">${{price}}</td>
            <td colspan="4" class="cp-na">no consensus target</td>
          </tr>`;
        }}
        const gapSign = p.gap > 0 ? '+' : '';
        // Conviction cell: scored rows show the number (green + / red −) with a
        // tooltip breaking down the components; unscored rows show an em dash.
        let convCell;
        if (s) {{
          const cls = s.value > 0 ? 'text-green' : s.value < 0 ? 'text-red' : '';
          const sign = s.value > 0 ? '+' : '';
          const bias = (sym.avg_pct_diff || 0);
          convCell = `<td class="num cp-conv ${{cls}}" title="conviction = (implied ${{gapSign}}${{fmtSig3(p.gap)}}% + bias ${{fmtSig3(bias)}}%) &times; hit rate ${{(s.hitRate*100).toFixed(0)}}% = ${{sign}}${{s.value.toFixed(1)}} (${{s.snaps}} snapshots)">${{sign}}${{s.value.toFixed(1)}}</td>`;
        }} else {{
          convCell = `<td class="num cp-conv-na" title="no checkpoint history yet — can't score reliability">—</td>`;
        }}
        return `<tr class="cp-row" data-symbol="${{sym.symbol}}">
          <td class="cp-ticker">${{sym.symbol}}</td>
          <td class="num">${{price}}</td>
          <td class="num"><span class="cp-target">$${{p.target.toFixed(2)}}</span> <span class="cp-src">${{p.source}} &middot; ${{p.analyst_count}} analysts</span></td>
          <td class="num ${{p.vcls}}">${{gapSign}}${{fmtSig3(p.gap)}}%</td>
          ${{convCell}}
          <td><span class="verdict ${{p.vcls}}">${{p.verdict}}</span></td>
        </tr>`;
      }}).join('')}}
    </tbody>
  `;
  // Click a row → scroll to that symbol's card below.
  el.querySelectorAll('.cp-row').forEach(tr => {{
    tr.addEventListener('click', () => {{
      const card = document.getElementById('card-' + tr.getAttribute('data-symbol'));
      if (card) {{
        card.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }}
    }});
  }});
}}

// ── Render Summary Cards ──
function renderSummary() {{
  const o = DATA.overall;
  const total = o.total || 0;
  const hits = o.hits || 0;
  const hitRateNum = total ? Math.min(100, hits / total * 100) : 0;
  const avgDev = o.avg_pct_diff || 0;
  const avgAbsDev = o.avg_abs_pct_diff || 0;

  const cards = [
    {{ label: 'Total Targets', value: SYMBOLS.reduce((s, sym) => s + (sym.target_count || 0), 0), sub: 'across ' + SYMBOLS.length + ' symbols', def: 'All analyst target prices collected for the tracked stocks.' }},
    {{ label: 'Checkpoints Evaluated', value: total, sub: 'at 30/90/180/365 days', def: 'Target-vs-actual comparisons made at the four checkpoint horizons.' }},
    {{ label: 'Hit Rate', value: fmtSig3(hitRateNum) + '%', sub: hits + ' hits / ' + total + ' total', cls: hitRateNum >= 20 ? 'text-green' : hitRateNum >= 10 ? 'text-orange' : 'text-red', def: 'Share of checkpoints where the actual price was within ±5% of the target.' }},
    {{ label: 'Avg Deviation', value: fmtSig3(avgDev) + '%', sub: avgDev < 0 ? 'analysts too optimistic' : 'analysts too pessimistic', cls: avgDev < -10 ? 'text-red' : avgDev < 0 ? 'text-orange' : 'text-green', def: 'Mean signed % gap (actual − target). Negative = analysts too optimistic.' }},
    {{ label: 'Avg |Deviation|', value: fmtSig3(avgAbsDev) + '%', sub: 'mean absolute error', def: 'Average size of the error, ignoring direction.' }},
    {{ label: 'Bias', value: (o.miss_high || 0) > (o.miss_low || 0) ? 'Optimistic' : 'Pessimistic', sub: (o.miss_high || 0) + ' too high / ' + (o.miss_low || 0) + ' too low', cls: (o.miss_high || 0) > (o.miss_low || 0) ? 'text-red' : 'text-green', def: 'Whether analysts leaned too high (optimistic) or too low (pessimistic), by miss counts.' }},
  ];

  document.getElementById('summaryRow').innerHTML = cards.map(c => `
    <div class="summary-card">
      <div class="label">${{c.label}}</div>
      <div class="value ${{c.cls || ''}}">${{c.value}}</div>
      <div class="sub">${{c.sub}}</div>
      <div class="def">${{c.def}}</div>
    </div>
  `).join('');
}}

// ── Render Insights ──
function renderInsights() {{
  document.getElementById('insightsContainer').innerHTML = INSIGHTS.map(i => `
    <div class="insight-item ${{i.type}}">
      <div class="insight-icon">${{i.icon}}</div>
      <div>
        <div class="insight-title">${{i.title}}</div>
        <div class="insight-text">${{i.text}}</div>
      </div>
    </div>
  `).join('');
}}

// ── Render Checkpoint Table ──
function renderCheckpoints() {{
  const rows = DATA.by_checkpoint;
  const maxAbsDev = Math.max(...rows.map(r => r.avg_abs_pct_diff || 0));

  document.getElementById('checkpointTable').innerHTML = `
    <thead>
      <tr>
        <th>Checkpoint</th>
        <th>Hit Rate</th>
        <th>Avg Deviation</th>
        <th>Avg |Deviation|</th>
        <th>Total</th>
      </tr>
    </thead>
    <tbody>
      ${{rows.map(r => {{
        const hitRateNum = r.total ? (r.hits / r.total * 100) : 0;
        const barPct = maxAbsDev ? (r.avg_abs_pct_diff / maxAbsDev * 100) : 0;
        const barColor = hitRateNum >= 20 ? 'var(--green)' : hitRateNum >= 10 ? 'var(--orange)' : 'var(--red)';
        return `
          <tr>
            <td style="font-weight:600">${{r.checkpoint_days}}-day</td>
            <td><span class="${{hitRateNum >= 20 ? 'text-green' : hitRateNum >= 10 ? 'text-orange' : 'text-red'}}" style="font-weight:700">${{fmtSig3(hitRateNum)}}%</span> (${{r.hits}}/${{r.total}})</td>
            <td class="${{(r.avg_pct_diff || 0) < -10 ? 'text-red' : (r.avg_pct_diff || 0) < 0 ? 'text-orange' : 'text-green'}}" style="font-weight:600">${{fmtSig3(r.avg_pct_diff || 0)}}%</td>
            <td>
              <div class="bar-chart">
                <div class="bar-track"><div class="bar-fill" style="width:${{barPct}}%; background:${{barColor}}"></div></div>
              </div>
              <span style="font-size:0.8rem; font-weight:600">${{fmtSig3(r.avg_abs_pct_diff || 0)}}%</span>
            </td>
            <td>${{r.total}}</td>
          </tr>
        `;
      }}).join('')}}
    </tbody>
  `;
}}

// ── Render Overall Analyst Accuracy Panels ──
function renderAnalysts() {{
  // repKey picks which representative target to feature ('best_target' for most
  // accurate, 'worst_target' for least). repLabel/repVerb describe it: a most-
  // accurate firm's best call was a "hit"; a least-accurate firm's worst call
  // was "missed". eval_date = date_posted + checkpoint_days = the date the
  // target's outcome was realized (the date the target was hit).
  function row(a, barColor, repKey, repLabel, repVerb) {{
    const hitRate = a.hit_rate != null ? a.hit_rate : (a.total ? a.hits / a.total * 100 : 0);
    const barPct = Math.min(100, Math.max(2, hitRate));  // tiny min so a 0% bar is still visible
    const rep = a[repKey];
    const made = rep && rep.date_posted ? rep.date_posted : 'unknown';
    const evald = rep && rep.eval_date ? rep.eval_date : 'unknown';
    const cp = rep ? rep.checkpoint_days : null;
    const symLbl = rep && rep.symbol ? rep.symbol + ': ' : '';
    const rangeLine = rep ? `<div class="analyst-call">${{repLabel}} call: ${{symLbl}}target $${{rep.target_price.toFixed(2)}} made <span class="date-hl">${{made}}</span>, ${{repVerb}} <span class="date-hl">${{evald}}</span> (${{cp}}-day, ${{fmtSig3(rep.pct_diff)}}%)</div><div class="analyst-range">stock ${{rangeHtml(rep.price_range)}}</div>` : '';
    return `
      <div class="analyst-row">
        <div class="analyst-row-head">
          <span class="analyst-firm-name" title="${{a.analyst_firm}}">${{a.analyst_firm || 'Unknown'}}</span>
          <span class="analyst-hit">${{fmtSig3(hitRate)}}% hit</span>
        </div>
        <div class="analyst-bar"><div class="analyst-bar-fill" style="width:${{barPct}}%; background:${{barColor}}"></div></div>
        <div class="analyst-meta">${{a.hits}}/${{a.total}} checkpoints hit &middot; ${{fmtSig3(a.avg_abs_pct_diff)}}% avg |dev| &middot; ${{fmtSig3(a.avg_pct_diff)}}% signed</div>
        ${{rangeLine}}
      </div>
    `;
  }}
  // firms = individual analyst firms; cons = consensus aggregates (oanor monthly
  // consensus). Ranked separately so an aggregate is never mixed with a real
  // firm; consensus rows appear under a labelled sub-section only when present.
  function panel(firms, cons, containerId, barColor, repKey, repLabel, repVerb) {{
    const el = document.getElementById(containerId);
    const hasFirms = firms && firms.length > 0;
    const hasCons = cons && cons.length > 0;
    if (!hasFirms && !hasCons) {{
      el.innerHTML = '<div class="empty-note">No qualified analyst data yet.</div>';
      return;
    }}
    let html = (firms || []).map(a => row(a, barColor, repKey, repLabel, repVerb)).join('');
    if (hasCons) {{
      html += `<div class="analyst-subgroup">Consensus sources (aggregates, not individual analysts)</div>`;
      html += cons.map(a => row(a, barColor, repKey, repLabel, repVerb)).join('');
    }}
    el.innerHTML = html;
  }}
  panel(DATA.most_accurate_analysts, DATA.most_accurate_analysts_consensus,
        'mostAccurateAnalysts', 'var(--green)', 'best_target', 'Best', 'hit');
  panel(DATA.least_accurate_analysts, DATA.least_accurate_analysts_consensus,
        'leastAccurateAnalysts', 'var(--red)', 'worst_target', 'Worst', 'missed');
}}

// ── Render Price-Target Methodology (per-type summary + compact org table) ──
// The "how the target is calculated" description only varies by type (Consensus
// vs Analyst firm), so it is shown once per type in summary cards rather than
// repeated on every org row. The table below then lists each org compactly
// (Org | Type | # Targets | Sources) with no per-row methodology column.
function renderMethodology() {{
  const rows = DATA.org_methodologies || [];
  const el = document.getElementById('methodologyTable');
  const sumEl = document.getElementById('methodSummary');
  if (!rows.length) {{
    if (sumEl) sumEl.innerHTML = '';
    el.innerHTML = `<tbody><tr><td colspan="4" style="text-align:center;color:var(--text-dim);padding:18px">No analyst orgs in the database yet.</td></tr></tbody>`;
    return;
  }}

  // Aggregate counts per org_type (description is identical within a type).
  const byType = {{}};
  for (const r of rows) {{
    const g = byType[r.org_type] = byType[r.org_type] || {{
      orgs: 0, targets: 0, methodology: r.methodology,
      pill: r.org_type === 'Consensus' ? 'type-consensus' : 'type-firm',
    }};
    g.orgs += 1;
    g.targets += r.target_count || 0;
  }}

  // Per-type summary cards (Consensus first).
  const order = ['Consensus', 'Analyst firm'];
  if (sumEl) {{
    sumEl.innerHTML = order.filter(t => byType[t]).map(t => {{
      const g = byType[t];
      const orgs = g.orgs + ' org' + (g.orgs !== 1 ? 's' : '');
      const targets = g.targets + ' target' + (g.targets !== 1 ? 's' : '');
      return `
        <div class="method-card">
          <div class="mc-head">
            <span class="type-pill ${{g.pill}}">${{t}}</span>
            <span class="mc-count">${{orgs}} &middot; ${{targets}}</span>
          </div>
          <div class="mc-body">${{g.methodology}}</div>
        </div>
      `;
    }}).join('');
  }}

  // Compact org table. Consensus orgs (Yahoo/FMP — varied sources) stay as full
  // rows. oanor's monthly consensus entries are pre-collapsed server-side into
  // one "oanor consensus (Nasdaq)" row with a per-month chip cloud (expandable).
  // The long tail of individual analyst firms (almost always MarketBeat,
  // differing only by name + target count) collapse into ONE expandable row
  // (chip cloud) so the table needs no scroll.
  const consensus = rows.filter(r => r.org_type === 'Consensus');
  const firms = rows.filter(r => r.org_type !== 'Consensus');

  const consensusRows = consensus.map(r => {{
    // Collapsed oanor row carries a `months` chip cloud; render it like the
    // firm-aggregate row (expandable) so the per-month breakdown is on demand.
    if (r.months && r.months.length) {{
      const monthsLine = r.months.length + ' period' + (r.months.length !== 1 ? 's' : '');
      return `
        <tr class="firm-aggregate">
          <td colspan="4">
            <details>
              <summary>
                <span class="type-pill type-consensus">Consensus</span>
                <span class="fa-title">${{r.org}}</span>
                <span class="fa-meta">${{r.target_count}} targets &middot; ${{monthsLine}}</span>
              </summary>
              <div class="firm-chips">
                ${{r.months.map(m => `<span class="firm-chip">${{m.label}} <b>${{m.target_count}}</b></span>`).join('')}}
              </div>
            </details>
          </td>
        </tr>`;
    }}
    const sources = (r.sources || []).join(', ');
    return `
      <tr>
        <td class="org">${{r.org}}</td>
        <td><span class="type-pill type-consensus">Consensus</span></td>
        <td class="num">${{r.target_count}}</td>
        <td style="font-size:0.78rem;color:var(--text-dim)">${{sources}}</td>
      </tr>`;
  }}).join('');

  const firmTotal = firms.reduce((s, r) => s + (r.target_count || 0), 0);
  const firmSources = [...new Set(firms.flatMap(r => r.sources || []))].join(', ');
  const firmRow = firms.length ? `
    <tr class="firm-aggregate">
      <td colspan="4">
        <details>
          <summary>
            <span class="type-pill type-firm">Analyst firm</span>
            <span class="fa-title">${{firms.length}} analyst firms</span>
            <span class="fa-meta">${{firmTotal}} targets${{firmSources ? ' &middot; ' + firmSources : ''}}</span>
          </summary>
          <div class="firm-chips">
            ${{firms.map(r => `<span class="firm-chip">${{r.org}} <b>${{r.target_count}}</b></span>`).join('')}}
          </div>
        </details>
      </td>
    </tr>` : '';

  el.innerHTML = `
    <colgroup>
      <col style="width:34%"><col style="width:16%"><col style="width:12%"><col style="width:38%">
    </colgroup>
    <thead>
      <tr><th>Analyst Org</th><th>Type</th><th># Targets</th><th>Sources</th></tr>
    </thead>
    <tbody>
      ${{consensusRows}}${{firmRow}}
    </tbody>
  `;
}}

// ── Render Whole-Window Target Accuracy (Met_any over the full year) ──
// Complementary to the 30/90/180/365 checkpoints: evaluates each org's targets
// against the stock's whole price path. met_any = target was touched at some
// point during the window; bias coloring mirrors the summary cards
// (negative = analysts too optimistic).
function renderWholeWindow() {{
  const rows = DATA.whole_window || [];
  const ov = DATA.whole_window_overall || {{}};
  const ovEl = document.getElementById('wholeWindowOverall');
  if (ovEl) {{
    const n = ov.n_evaluated || 0;
    if (n) {{
      ovEl.innerHTML = 'Across all evaluated targets, <strong>' + fmtSig3(ov.met_any_rate) +
        '%</strong> (' + ov.met_any_count + '/' + n + ') were reached at some point during their window.';
    }}
  }}
  const el = document.getElementById('wholeWindowTable');
  if (!rows.length) {{
    el.innerHTML = `<tbody><tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:18px">No whole-window data yet (needs price history for evaluated targets).</td></tr></tbody>`;
    return;
  }}
  el.innerHTML = `
    <colgroup>
      <col style="width:30%"><col style="width:22%"><col style="width:16%"><col style="width:18%"><col style="width:14%">
    </colgroup>
    <thead>
      <tr><th>Analyst Org</th><th>Targets Reached</th><th>Avg Days to Hit</th><th>Avg Time Within &plusmn;5%</th><th>Avg Bias</th></tr>
    </thead>
    <tbody>
      ${{(function() {{
        // Individual analyst firms first, then a labelled divider, then consensus
        // aggregates (oanor monthly consensus) — never mixed in one ranking.
        const firms = rows.filter(r => !r.is_consensus);
        const cons = rows.filter(r => r.is_consensus);
        function rowHtml(r) {{
          const metRate = r.met_any_rate || 0;
          const metCls = metRate >= 60 ? 'text-green' : metRate >= 30 ? 'text-orange' : 'text-red';
          const dth = r.avg_days_to_hit != null ? r.avg_days_to_hit + 'd' : '—';
          const wbp = r.avg_within_band_pct != null ? r.avg_within_band_pct : 0;
          const bias = r.avg_bias_pct != null ? r.avg_bias_pct : 0;
          const biasCls = bias < -10 ? 'text-red' : bias < 0 ? 'text-orange' : 'text-green';
          const biasSign = bias > 0 ? '+' : '';
          return `
            <tr>
              <td class="org">${{r.analyst_firm}}</td>
              <td class="num"><span class="${{metCls}}" style="font-weight:700">${{fmtSig3(metRate)}}%</span> <span style="color:var(--text-dim);font-size:0.76rem">${{r.met_any_count}}/${{r.n_evaluated}}</span></td>
              <td class="num">${{dth}}</td>
              <td class="num">${{fmtSig3(wbp)}}%</td>
              <td class="num ${{biasCls}}" style="font-weight:600">${{biasSign}}${{fmtSig3(bias)}}%</td>
            </tr>
          `;
        }}
        let html = firms.map(rowHtml).join('');
        if (cons.length) {{
          html += `<tr class="ww-divider"><td colspan="5">Consensus sources (aggregates, not individual analysts)</td></tr>`;
          html += cons.map(rowHtml).join('');
        }}
        return html;
      }})()}}
    </tbody>
  `;
}}

// ── Per-symbol whole-window accuracy block (inside each symbol card) ──
// Mirrors the org-level whole-window table but for one symbol's own targets.
function renderSymbolWholeWindow(sym) {{
  const w = sym.whole_window;
  if (!w) return '';
  const metRate = w.met_any_rate || 0;
  const metCls = metRate >= 60 ? 'text-green' : metRate >= 30 ? 'text-orange' : 'text-red';
  const dth = w.avg_days_to_hit != null ? w.avg_days_to_hit + 'd' : '—';
  const bias = w.avg_bias_pct != null ? w.avg_bias_pct : 0;
  const biasCls = bias < -10 ? 'text-red' : bias < 0 ? 'text-orange' : 'text-green';
  const biasSign = bias > 0 ? '+' : '';
  return '<div class="ww-sym">' +
    '<div class="ww-sym-title">Whole-Window Accuracy</div>' +
    '<div class="ww-sym-stats">' +
    '<span>Reached <span class="' + metCls + '" style="font-weight:700">' + fmtSig3(metRate) +
      '%</span> <span class="dim">(' + w.met_any_count + '/' + w.n_evaluated + ')</span></span>' +
    '<span>Days to hit <strong>' + dth + '</strong></span>' +
    '<span>Within &plusmn;5% <strong>' + fmtSig3(w.avg_within_band_pct) + '%</strong></span>' +
    '<span>Bias <span class="' + biasCls + '" style="font-weight:700">' + biasSign + fmtSig3(bias) + '%</span></span>' +
    '</div></div>';
}}

// ── Per-symbol price chart: close line + high/low band + analyst targets ──
// Day index (days since epoch) for x-axis scaling; shared by the chart and the
// 52-week range bar.
function dayIndex(d) {{ return Date.parse(d) / 86400000; }}

// 52-week high/low + range bar: a horizontal track from the 52-week low to the
// 52-week high, filled up to the current price, with a dot marking where the
// current price sits in that range. Computed from the cached daily history
// (last 365 calendar days). Returns '' when there is no history.
function render52Week(sym) {{
  const hist = sym.price_history || [];
  if (!hist.length) return '';
  const lastDay = dayIndex(hist[hist.length - 1].price_date);
  const cut = lastDay - 365;
  let lo = Infinity, hi = -Infinity;
  for (const p of hist) {{
    if (dayIndex(p.price_date) < cut) continue;
    if (p.low != null && p.low < lo) lo = p.low;
    if (p.high != null && p.high > hi) hi = p.high;
  }}
  if (!isFinite(lo) || !isFinite(hi)) return '';
  const cur = sym.latest_price != null ? sym.latest_price : hist[hist.length - 1].close;
  const W = 170, padX = 6, barY = 13, barH = 6;
  const xLo = padX, xHi = W - padX;
  let frac = hi > lo ? (cur - lo) / (hi - lo) : 0.5;
  if (frac < 0) frac = 0;
  if (frac > 1) frac = 1;
  const curX = xLo + frac * (xHi - xLo);
  const fmt = v => '$' + (v < 10 ? v.toFixed(2) : v.toFixed(0));
  // One compact element: a tiny "52wk" tag, the range bar, and the low/high
  // values as end labels aligned with the bar ends (no separate label line).
  const svg = '<svg viewBox="0 0 ' + W + ' 30" width="' + W + '" role="img" aria-label="52-week range">' +
    '<text x="' + xLo + '" y="9" font-size="8" fill="var(--text-dim)">52wk</text>' +
    '<rect x="' + xLo + '" y="' + barY + '" width="' + (xHi - xLo) + '" height="' + barH + '" rx="3" fill="rgba(255,255,255,0.08)"/>' +
    '<rect x="' + xLo + '" y="' + barY + '" width="' + (curX - xLo).toFixed(1) + '" height="' + barH + '" rx="3" fill="var(--accent)" opacity="0.65"/>' +
    '<circle cx="' + curX.toFixed(1) + '" cy="' + (barY + barH / 2) + '" r="3.5" fill="var(--accent)" stroke="var(--card)" stroke-width="1.5"/>' +
    '<text x="' + xLo + '" y="28" font-size="9" fill="var(--red)" font-weight="600">' + fmt(lo) + '</text>' +
    '<text x="' + xHi + '" y="28" text-anchor="end" font-size="9" fill="var(--green)" font-weight="600">' + fmt(hi) + '</text>' +
    '</svg>';
  return '<div class="week52">' + svg + '</div>';
}}

// Map an analyst rating string to a chart color (buy / hold / sell / other).
function ratingColor(r) {{
  const s = (r || '').toLowerCase();
  if (/(strong buy|buy|outperform|overweight|accumulate)/.test(s)) return 'var(--green)';
  if (/(sell|underperform|underweight|reduce)/.test(s)) return 'var(--red)';
  if (/(hold|neutral|market perform|equal|sector perform)/.test(s)) return 'var(--orange)';
  return 'var(--blue)';
}}

// Build an inline SVG chart for one symbol: a shaded high/low band, the daily
// close as a line, and every analyst target as a colored horizontal segment
// running from the issue date through its 1-year horizon (clipped to the last
// observed price date), with a dot at issuance and a hover tooltip. Uses only
// string concatenation (no nested template literals) so the f-string braces
// stay straightforward.
function renderChart(sym) {{
  const hist = sym.price_history || [];
  const targets = sym.chart_targets || [];
  if (!hist.length) return '<div class="chart-na">price history unavailable for chart</div>';
  const W = 640, H = 200, padL = 46, padR = 10, padT = 12, padB = 20;
  const di = d => Date.parse(d) / 86400000;
  const xs = hist.map(p => di(p.price_date));
  let xMin = Math.min.apply(null, xs), xMax = Math.max.apply(null, xs);
  const tDays = targets.map(t => di(t.date_posted)).filter(n => isFinite(n));
  if (tDays.length) {{
    xMin = Math.min(xMin, Math.min.apply(null, tDays));
    xMax = Math.max(xMax, Math.max.apply(null, tDays));
  }}
  if (!(xMax > xMin)) xMax = xMin + 1;
  let yMin = Math.min.apply(null, hist.map(p => p.low));
  let yMax = Math.max.apply(null, hist.map(p => p.high));
  if (targets.length) {{
    yMin = Math.min(yMin, Math.min.apply(null, targets.map(t => t.target_price)));
    yMax = Math.max(yMax, Math.max.apply(null, targets.map(t => t.target_price)));
  }}
  const yPad = ((yMax - yMin) * 0.05) || 1;
  yMin -= yPad; yMax += yPad;
  const xPx = x => padL + (x - xMin) / (xMax - xMin) * (W - padL - padR);
  const yPx = y => padT + (yMax - y) / (yMax - yMin) * (H - padT - padB);
  const f1 = n => n.toFixed(1);
  // High/low band: trace highs left->right, then lows right->left, close.
  const highPts = hist.map(p => f1(xPx(di(p.price_date))) + ' ' + f1(yPx(p.high))).join(' L ');
  const lowPts = hist.slice().reverse().map(p => f1(xPx(di(p.price_date))) + ' ' + f1(yPx(p.low))).join(' L ');
  const band = 'M ' + highPts + ' L ' + lowPts + ' Z';
  const closePts = hist.map(p => f1(xPx(di(p.price_date))) + ',' + f1(yPx(p.close))).join(' ');
  let txml = '';
  for (const t of targets) {{
    const x0 = di(t.date_posted);
    if (!isFinite(x0)) continue;
    const x1 = Math.min(x0 + 365, xMax);
    const col = ratingColor(t.rating);
    const firm = (t.analyst_firm || '?').replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const rt = (t.rating || '').replace(/&/g, '&amp;');
    txml += '<line x1="' + f1(xPx(x0)) + '" y1="' + f1(yPx(t.target_price)) +
            '" x2="' + f1(xPx(x1)) + '" y2="' + f1(yPx(t.target_price)) +
            '" stroke="' + col + '" stroke-width="1.2" opacity="0.4"/>' +
            '<circle cx="' + f1(xPx(x0)) + '" cy="' + f1(yPx(t.target_price)) +
            '" r="3" fill="' + col + '"><title>' + firm + ' · $' + t.target_price +
            ' · ' + t.date_posted + ' · ' + rt + '</title></circle>';
  }}
  const fmtP = v => '$' + (v < 10 ? v.toFixed(2) : v.toFixed(0));
  const firstDate = hist[0].price_date, lastDate = hist[hist.length - 1].price_date;
  const symEsc = sym.symbol.replace(/&/g, '&amp;');
  const svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Price chart for ' + symEsc + '">' +
    '<path d="' + band + '" fill="var(--accent)" opacity="0.10"/>' +
    '<polyline points="' + closePts + '" fill="none" stroke="var(--accent)" stroke-width="2"/>' +
    txml +
    '<text x="' + (padL - 6) + '" y="' + f1(yPx(yMax) + 4) + '" text-anchor="end" font-size="13" fill="var(--text-dim)">' + fmtP(yMax) + '</text>' +
    '<text x="' + (padL - 6) + '" y="' + (H - padB + 4) + '" text-anchor="end" font-size="13" fill="var(--text-dim)">' + fmtP(yMin) + '</text>' +
    '<text x="' + padL + '" y="' + (H - 5) + '" font-size="13" fill="var(--text-dim)">' + firstDate + '</text>' +
    '<text x="' + (W - padR) + '" y="' + (H - 5) + '" text-anchor="end" font-size="13" fill="var(--text-dim)">' + lastDate + '</text>' +
    '</svg>';
  const legend = '<div class="chart-legend">' +
    '<span><span class="sw" style="background:var(--accent)"></span>close</span>' +
    '<span><span class="sw" style="background:var(--accent);opacity:0.25"></span>high–low range</span>' +
    '<span><span class="sw" style="background:var(--green)"></span>buy target</span>' +
    '<span><span class="sw" style="background:var(--orange)"></span>hold target</span>' +
    '<span><span class="sw" style="background:var(--red)"></span>sell target</span>' +
    '</div>';
  return '<div class="sym-chart">' + svg + legend + '</div>';
}}

// ── Render Symbol Cards ──
// A symbol gets a full card only if it has DATED analyst targets (real per-analyst
// coverage) — those drive the chart, accuracy bars, analyst lists, and whole-window
// metric. Symbols with only undated consensus (or none at all) get a compact tile.
function renderSymbols(filter) {{
  let syms = SYMBOLS;
  if (filter && filter !== 'all') {{
    if (filter === 'other') {{
      // ETFs / funds / uncategorized (no sector)
      syms = syms.filter(s => !s.sector);
    }} else {{
      // Exact sector match (tabs are built from the real sectors present)
      syms = syms.filter(s => s.sector === filter);
    }}
  }}

  // Heading reflects the active category + how many symbols are in it.
  const heading = (filter === 'all' || !filter) ? 'All Symbols'
    : filter === 'other' ? 'Other (uncategorized)'
    : filter;
  document.getElementById('symbolsHeading').textContent = heading + ' (' + syms.length + ')';

  const hasDated = s => (s.chart_targets || []).length > 0;
  const withTargets = syms.filter(hasDated);
  const noTargets = syms.filter(s => !hasDated(s));
  const strip = noTargets.length
    ? '<div class="no-targets-strip"><div class="strip-label">No analyst targets (' + noTargets.length + ')</div>' +
      noTargets.map(s => '<div class="notile"><div><span class="notile-ticker">' + s.symbol + '</span><span class="notile-price">' + (s.latest_price != null ? '$' + s.latest_price.toFixed(2) : 'N/A') + '</span></div><span class="notile-tag">' + ((s.target_count || 0) === 0 ? 'no targets' : 'consensus only') + '</span></div>').join('') + '</div>'
    : '';

  document.getElementById('symbolsGrid').innerHTML = strip + withTargets.map(sym => {{
    const latestPrice = sym.latest_price ? sym.latest_price.toFixed(2) : 'N/A';
    const snapCount = sym.snapshot_count || 0;
    const hits = sym.hits || 0;
    const hitRate = snapCount ? Math.min(100, hits / snapCount * 100).toFixed(0) : '0';
    const avgDev = sym.avg_pct_diff || 0;
    const avgAbsDev = sym.avg_abs_pct_diff || 0;

    // Consensus pills
    const consensusHtml = (sym.source_consensus || []).map(sc => `
      <div class="consensus-pill">
        <span class="source">${{sc.source}}</span>:
        <span class="target">$${{sc.consensus_target}}</span>
        <span style="color:var(--text-dim);font-size:0.7rem"> (${{sc.low_target}}-${{sc.high_target}}, ${{sc.analyst_count}} analysts)</span>
      </div>
    `).join('');

    // Accuracy by checkpoint
    const accHtml = (sym.accuracy_by_checkpoint || []).map(cp => {{
      const cpHitRate = cp.total ? (cp.hits / cp.total * 100) : 0;
      const barColor = cpHitRate >= 20 ? 'var(--green)' : cpHitRate >= 10 ? 'var(--orange)' : 'var(--red)';
      return `
        <div class="accuracy-row">
          <div class="accuracy-label">${{cp.checkpoint_days}}-day</div>
          <div class="accuracy-bar">
            <div class="bar-track"><div class="bar-fill" style="width:${{cpHitRate}}%; background:${{barColor}}"></div></div>
          </div>
          <div class="accuracy-num ${{cpHitRate >= 20 ? 'text-green' : cpHitRate >= 10 ? 'text-orange' : 'text-red'}}">${{fmtSig3(cpHitRate)}}%</div>
        </div>
      `;
    }}).join('');

    // Best/worst analysts (each target shows the date it was made + the stock's
    // price range over the 360 days after the prediction, if available).
    // Consensus aggregates (oanor monthly consensus) are split out under a
    // labelled sub-section so they never appear as a real analyst firm.
    function analystItems(list) {{
      return (list || []).map(a => {{
        // Checkpoint chip: which horizon this representative call was scored at,
        // and whether it hit (e.g. "30d hit" / "180d miss"). Only dated targets
        // that produced a snapshot carry checkpoint_days.
        const hit = a.accuracy_rating === 'hit';
        const cpCls = hit ? 'text-green' : 'text-red';
        const cpChip = a.checkpoint_days
          ? `<span class="analyst-cp ${{cpCls}}" title="Evaluated at the ${{a.checkpoint_days}}-day checkpoint (${{hit ? 'hit' : 'miss'}})">${{a.checkpoint_days}}d ${{hit ? 'hit' : 'miss'}}</span>`
          : '';
        return `
        <div class="analyst-item">
          <div class="analyst-left">
            <span class="analyst-firm">${{a.analyst_firm || '?'}}</span>
            ${{a.date_posted ? '<span class="analyst-date">made ' + a.date_posted + '</span>' : '<span class="analyst-date">date unknown</span>'}}
            <span class="analyst-range">stock ${{rangeHtml(a.price_range)}}</span>
          </div>
          <div class="analyst-right">
            ${{cpChip}}
            <span class="analyst-target">$${{a.target_price}}</span>
            <span class="${{cpCls}}" style="font-weight:600">${{fmtSig3(a.pct_diff)}}%</span>
          </div>
        </div>
      `;
      }}).join('');
    }}
    const bestCons = sym.best_analysts_consensus || [];
    const worstCons = sym.worst_analysts_consensus || [];
    const bestHtml = analystItems(sym.best_analysts)
      + (bestCons.length ? '<div class="analyst-subgroup">Consensus sources</div>' + analystItems(bestCons) : '');
    const worstHtml = analystItems(sym.worst_analysts)
      + (worstCons.length ? '<div class="analyst-subgroup">Consensus sources</div>' + analystItems(worstCons) : '');

    // Consensus verdict: does the consensus target suggest Buy more / Hold / Sell off?
    // Uses the shared consensusPick() helper so the card's verdict and the
    // top-level Consensus Picks table always agree.
    const hasTargets = (sym.target_count || 0) > 0;
    const chartHtml = hasTargets ? renderChart(sym) : '';
    const week52Html = render52Week(sym);
    const wwSymHtml = hasTargets ? renderSymbolWholeWindow(sym) : '';
    const pick = consensusPick(sym);
    let verdictHtml = '';
    if (!hasTargets) {{
      verdictHtml = `<div class="verdict verdict-none" title="No analyst price targets have been fetched for this symbol">NO TARGETS</div>`;
    }} else if (pick) {{
      verdictHtml = `<div class="verdict ${{pick.vcls}}" title="Consensus target $${{pick.target.toFixed(2)}} vs current $${{sym.latest_price.toFixed(2)}}">${{pick.verdict}} <span class="verdict-gap">${{pick.gap > 0 ? '+' : ''}}${{fmtSig3(pick.gap)}}%</span></div>`;
    }}

    // Edge case: a symbol with no analyst targets — show a placeholder instead
    // of the empty consensus/accuracy/analyst sections.
    const noTargetsHtml = !hasTargets
      ? `<div class="no-targets">No analyst price targets available yet.<br>Run <code>python stock_target_tracker/tracker.py fetch --symbols ${{sym.symbol}}</code> to collect targets.</div>`
      : '';

    return `
      <div class="symbol-card" id="card-${{sym.symbol}}" data-sector="${{sym.sector || ''}}">
        <div class="symbol-header">
          <div>
            <div class="symbol-ticker">${{sym.symbol}}</div>
            <div class="symbol-company">${{sym.company_name || ''}}</div>
            <div class="symbol-sector">${{sym.sector || 'N/A'}}</div>
          </div>
          <div class="symbol-price">
            <div class="price">$${{latestPrice}}</div>
            <div class="price-sub">
              <div class="date">${{sym.latest_price_date || ''}}</div>
              ${{verdictHtml}}
            </div>
            ${{week52Html}}
          </div>
        </div>

        ${{noTargetsHtml}}

        ${{chartHtml}}

        ${{hasTargets && consensusHtml ? '<div class="consensus-row">' + consensusHtml + '</div>' : ''}}

        ${{hasTargets && accHtml ? '<div style="margin-bottom:8px"><div style="font-size:0.75rem;text-transform:uppercase;color:var(--text-dim);margin-bottom:6px">Accuracy by Horizon</div>' + accHtml + '</div>' : ''}}

        ${{wwSymHtml}}

        ${{hasTargets && bestHtml ? '<div class="bw-section"><div class="bw-label best">Most Accurate Analysts</div>' + bestHtml + '</div>' : ''}}
        ${{hasTargets && worstHtml ? '<div class="bw-section"><div class="bw-label worst">Least Accurate Analysts</div>' + worstHtml + '</div>' : ''}}
      </div>
    `;
  }}).join('');
}}

// ── Filter ──
let currentFilter = 'all';

// Build the category tabs dynamically from the sectors actually present in the
// data, each labelled with its symbol count. Tabs: All (N), then one per sector
// (sorted by count desc, then name), then Other (N) for symbols with no sector
// (ETFs / funds). Listeners are attached after building (no inline onclick).
function renderFilterTabs() {{
  const counts = {{}};
  let noneCount = 0;
  for (const s of SYMBOLS) {{
    if (s.sector) counts[s.sector] = (counts[s.sector] || 0) + 1;
    else noneCount++;
  }}
  const sectors = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
  const tab = (key, label, n) => '<div class="filter-tab' + (currentFilter === key ? ' active' : '') +
    '" data-filter="' + key + '">' + label + ' (' + n + ')</div>';
  let html = tab('all', 'All', SYMBOLS.length);
  for (const sec of sectors) html += tab(sec, sec, counts[sec]);
  if (noneCount) html += tab('other', 'Other', noneCount);
  const container = document.getElementById('filterTabs');
  container.innerHTML = html;
  container.querySelectorAll('.filter-tab').forEach(t => {{
    t.addEventListener('click', () => filterSymbols(t.getAttribute('data-filter')));
  }});
}}

function filterSymbols(filter) {{
  currentFilter = filter;
  renderSymbols(filter);
  renderFilterTabs();
}}

// ── Initialize ──
renderSummary();
renderConsensusPicks();
renderInsights();
renderCheckpoints();
renderAnalysts();
renderMethodology();
renderWholeWindow();
renderFilterTabs();
renderSymbols('all');
</script>
<!--RUN_TIMING-->
</body>
</html>"""


def generate_report(output_path=None, write_latest=True):
    """Generate the HTML accuracy report.

    Args:
        output_path: Optional path for the HTML file. If None, saves to
                     stock_target_tracker/output/report_<timestamp>.html.
        write_latest: Also write output/lastest.html (the portfolio "latest"
                      copy). Set False for the sample report so it never
                      clobbers the personal latest.html.
    """
    if not output_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(script_dir, 'output')
        os.makedirs(out_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        output_path = os.path.join(out_dir, f'report_{timestamp}.html')

    print("  Generating HTML report...")
    data = _fetch_report_data()
    insights = _generate_insights(data)

    # Check if there's any data. Even with zero targets we still generate the
    # report: every symbol card then renders a "NO TARGETS" placeholder so the
    # edge case (a tracked symbol with no price targets yet) is handled in the
    # output rather than the report refusing to build.
    total_targets = sum(s.get("target_count", 0) or 0 for s in data["symbols"])
    if total_targets == 0:
        print("  No target price data found yet — generating a report with "
              "per-symbol 'NO TARGETS' placeholders (run 'fetch' to populate).")

    html = _build_html(data, insights)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    file_size_kb = os.path.getsize(output_path) / 1024
    print(f"  Report saved to: {output_path} ({file_size_kb:.1f} KB)")
    print(f"  Contains data for {len(data['symbols'])} symbols, {total_targets} targets")

    # Also save as latest.html for easy access (skip for the sample report so it
    # never overwrites the personal portfolio latest.html).
    if write_latest and not output_path.endswith('latest.html'):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        latest_path = os.path.join(script_dir, 'output', 'latest.html')
        with open(latest_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"  Also saved as: {latest_path}")

    return output_path


def _format_step_times(step_times):
    """Build the small-font timing footer HTML from [(label, elapsed_seconds), ...].

    Includes a TOTAL (e2e) row. Returns '' if step_times is empty.
    """
    if not step_times:
        return ""
    total = sum(s for _, s in step_times)
    rows = ""
    for label, elapsed in step_times:
        pct = (elapsed / total * 100) if total else 0
        rows += (
            f'<tr><td class="rt-step">{_html.escape(str(label))}</td>'
            f'<td class="rt-secs">{elapsed:>7.1f}s</td>'
            f'<td class="rt-pct">({pct:5.1f}%)</td></tr>'
        )
    rows += (
        '<tr class="rt-total"><td class="rt-step">TOTAL (e2e)</td>'
        f'<td class="rt-secs">{total:>7.1f}s</td>'
        f'<td class="rt-pct">(100.0%)</td></tr>'
    )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        '<div class="run-timing">'
        '<div class="rt-title">Run timing (end-to-end refresh)</div>'
        f'<table>{rows}</table>'
        f'<div class="rt-stamp">Generated {stamp}</div>'
        '</div>'
    )


def inject_timing(html_path, step_times):
    """Replace the <!--RUN_TIMING--> placeholder in a generated report with the
    small-font step-timing footer. Called by refresh.py AFTER the report step
    completes, so the report's own generation time is included.

    Writes both the given html_path and, if it is latest.html / a timestamped
    report, leaves them consistent. Safe to call when the placeholder is absent
    (e.g. report.py run standalone without refresh.py) — it then no-ops.
    """
    if not step_times or not os.path.exists(html_path):
        return
    footer = _format_step_times(step_times)
    if not footer:
        return
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if "<!--RUN_TIMING-->" not in content:
        return
    content = content.replace("<!--RUN_TIMING-->", footer)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate HTML accuracy report")
    parser.add_argument("--output", type=str, help="Output HTML file path")
    args = parser.parse_args()

    init_db()
    report_path = generate_report(args.output)
    if report_path:
        print(f"\n  Open in browser: file:///{report_path.replace(os.sep, '/')}")