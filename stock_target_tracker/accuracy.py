"""
Multi-checkpoint accuracy comparison for analyst target prices.

Compares target prices to actual stock prices at 30, 90, 180, and 365 day
checkpoints. Determines if analysts' predictions were accurate (within 5%),
too low (stock exceeded target), or too high (stock fell short).
"""

from datetime import datetime, timedelta

from db import (
    get_symbols_needing_accuracy_check, save_accuracy_snapshot,
    get_actual_prices, get_closest_price, save_actual_price,
    save_price_history_rows, get_symbol_id,
    get_targets_needing_ever_hit, save_ever_hit,
)
import price_fetcher


# Default checkpoint intervals in days
CHECKPOINTS = [30, 90, 180, 365]

# Tolerance for "hit" rating (within this % of target)
HIT_TOLERANCE_PCT = 5.0

# Window for the ever-hit (TPMetANY) measure: did the price touch the target
# at any point during this many days after it was issued?
EVER_HIT_HORIZON_DAYS = 365


def run_accuracy_checks(checkpoint_days=None, symbol=None):
    """Run accuracy checks for all due targets.

    Finds target prices that are old enough for a checkpoint comparison
    but don't have a snapshot yet, then fetches actual prices and computes
    accuracy metrics.

    One batched price-history fetch serves BOTH passes (checkpoint
    comparisons and the ever-hit evaluation), and the fetched history is
    persisted into actual_prices so later runs find dense cached prices
    instead of re-fetching.

    Args:
        checkpoint_days: Specific checkpoint to check (30, 90, or 365).
                         If None, checks all due checkpoints.
        symbol: Specific symbol to check. If None, checks all symbols.

    Returns:
        Dict with summary: {checked, hits, miss_low, miss_high, no_data, errors}
    """
    # Get targets needing accuracy checks
    targets = get_symbols_needing_accuracy_check(checkpoint_days)

    if symbol:
        symbol_upper = symbol.upper()
        targets = [t for t in targets if t['symbol'] == symbol_upper]

    # Ever-hit targets due this run (fetched here so their start dates join
    # the same batched history download as the checkpoint targets).
    ever_due = get_targets_needing_ever_hit(symbol=symbol)

    if not targets and not ever_due:
        print("\n  No targets due for accuracy checks.")
        return {"checked": 0, "hits": 0, "miss_low": 0, "miss_high": 0,
                "no_data": 0, "errors": 0,
                "ever_hit_total": 0, "ever_hit_hit": 0}

    if targets:
        print(f"\n  Found {len(targets)} target/checkpoint pairs due for accuracy check")

    # One history per symbol: the earliest date any due target needs, fetched
    # in a single batched download shared by both passes.
    starts = {}
    for t in targets:
        s = starts.setdefault(t['symbol'], t['checkpoint_date'])
        starts[t['symbol']] = min(s, t['checkpoint_date'])
    for t in ever_due:
        s = starts.setdefault(t['symbol'], t['date_posted'])
        starts[t['symbol']] = min(s, t['date_posted'])
    histories = price_fetcher.fetch_price_histories(starts)

    # Persist the fetched histories so actual_prices becomes a dense daily
    # cache (checkpoint lookups then hit the DB instead of the network,
    # and the report can fall back to DB rows if a download fails).
    for sym, hist in histories.items():
        sid = get_symbol_id(sym)
        if sid:
            save_price_history_rows(sid, hist)

    stats = {"checked": 0, "hits": 0, "miss_low": 0, "miss_high": 0, "no_data": 0, "errors": 0,
             "ever_hit_total": 0, "ever_hit_hit": 0}

    for target in targets:
        try:
            result = compute_accuracy_for_target(
                target, history=histories.get(target['symbol']))
            stats["checked"] += 1

            if result["accuracy_rating"] == "hit":
                stats["hits"] += 1
            elif result["accuracy_rating"] == "miss_low":
                stats["miss_low"] += 1
            elif result["accuracy_rating"] == "miss_high":
                stats["miss_high"] += 1
            elif result["accuracy_rating"] == "no_data":
                stats["no_data"] += 1

        except Exception as e:
            stats["errors"] += 1
            print(f"    Error checking {target['symbol']} target {target['target_price_id']}: {e}")

    # Whole-window ever-hit (TPMetANY) pass: did the price touch each target at
    # any point during its 365-day window? Complements the point-in-time
    # checkpoints above. Reuses the batched histories fetched above.
    ever_stats = update_ever_hit_flags(symbol=symbol, histories=histories)
    stats["ever_hit_total"] = ever_stats["evaluated"]
    stats["ever_hit_hit"] = ever_stats["hit"]

    print(f"\n  Accuracy check complete:")
    print(f"    Checked:    {stats['checked']}")
    print(f"    Hits:       {stats['hits']} (within {HIT_TOLERANCE_PCT}%)")
    print(f"    Miss (low): {stats['miss_low']} (stock exceeded target)")
    print(f"    Miss (high): {stats['miss_high']} (stock fell short)")
    print(f"    No data:    {stats['no_data']}")
    print(f"    Errors:      {stats['errors']}")
    if ever_stats["evaluated"]:
        print(f"    Ever-hit:    {ever_stats['hit']}/{ever_stats['evaluated']} "
              f"targets touched at any point in the {EVER_HIT_HORIZON_DAYS}-day window")

    return stats


def compute_accuracy_for_target(target_info, history=None):
    """Compute accuracy for a single target at a single checkpoint.

    Args:
        target_info: Dict from get_symbols_needing_accuracy_check(), containing
                     target_price_id, symbol_id, target_price, checkpoint_date,
                     checkpoint_days, symbol.
        history: Optional list of this symbol's daily price rows (ascending,
                 {price_date, low, high, close, ...}), typically the shared
                 batched history fetched by run_accuracy_checks. Consulted
                 when the DB has no price near the checkpoint, before any
                 per-date network fetch.

    Returns:
        Dict with accuracy results: {accuracy_rating, actual_price, price_diff, pct_diff}
    """
    target_price_id = target_info["target_price_id"]
    symbol_id = target_info["symbol_id"]
    target_price = target_info["target_price"]
    checkpoint_date = target_info["checkpoint_date"]
    checkpoint_days = target_info["checkpoint_days"]
    symbol = target_info["symbol"]

    # Try to get the actual price from the database first. get_closest_price
    # returns the latest price <= checkpoint_date, which can be a *different*
    # checkpoint's price far from this one (actual_prices is sparse), so only
    # accept it when it is within ~10 days of the checkpoint; otherwise fetch
    # the actual checkpoint-date price so each checkpoint uses its own price.
    price_data = get_closest_price(symbol_id, checkpoint_date)
    if price_data and price_data.get('price_date'):
        try:
            cp_dt = datetime.strptime(checkpoint_date, '%Y-%m-%d')
            pd_dt = datetime.strptime(price_data['price_date'], '%Y-%m-%d')
            if abs((cp_dt - pd_dt).days) > 10:
                price_data = None
        except (ValueError, TypeError):
            price_data = None

    if not price_data and history:
        # Same nearest-prior-trading-day logic as fetch_price_on_date, served
        # from the shared in-memory history (no network call).
        for p in reversed(history):
            if p.get('price_date') and p['price_date'] <= checkpoint_date:
                try:
                    cp_dt = datetime.strptime(checkpoint_date, '%Y-%m-%d')
                    pd_dt = datetime.strptime(p['price_date'], '%Y-%m-%d')
                    if abs((cp_dt - pd_dt).days) <= 10:
                        price_data = {'price_date': p['price_date'],
                                      'close': p.get('close')}
                except (ValueError, TypeError):
                    pass
                break

    if not price_data:
        # Fetch from yfinance and save to database
        price_data = price_fetcher.fetch_price_on_date(symbol, checkpoint_date)
        if price_data:
            save_actual_price(
                symbol_id=symbol_id,
                price_date=price_data['price_date'],
                open_price=price_data['open'],
                close_price=price_data['close'],
                high_price=price_data['high'],
                low_price=price_data['low'],
                volume=price_data['volume'],
            )

    # get_closest_price rows use 'close_price'; freshly-fetched dicts use
    # 'close' — accept either so a first-run fetch is used immediately (not only
    # on a later run that re-reads the saved row).
    actual_price = None
    if price_data:
        actual_price = price_data.get('close_price')
        if actual_price is None:
            actual_price = price_data.get('close')

    # Compute accuracy metrics
    price_diff = None
    pct_diff = None
    accuracy_rating = "no_data"

    if actual_price is not None and target_price > 0:
        price_diff = actual_price - target_price
        pct_diff = price_diff / target_price * 100

        if abs(pct_diff) <= HIT_TOLERANCE_PCT:
            accuracy_rating = "hit"
        elif pct_diff > HIT_TOLERANCE_PCT:
            accuracy_rating = "miss_low"  # target was too low
        else:
            accuracy_rating = "miss_high"  # target was too high

    # Save snapshot
    save_accuracy_snapshot(
        target_price_id=target_price_id,
        symbol_id=symbol_id,
        checkpoint_days=checkpoint_days,
        actual_price=actual_price,
        target_price=target_price,
        price_diff=price_diff,
        pct_diff=pct_diff,
        accuracy_rating=accuracy_rating,
    )

    firm = target_info.get('analyst_firm') or 'Unknown'
    cp_label = f"{checkpoint_days}-day"
    if accuracy_rating == "no_data":
        print(f"    [{symbol}] {cp_label} {firm} ${target_price:.2f} -> no price data")
    else:
        print(f"    [{symbol}] {cp_label} {firm} "
              f"${target_price:.2f} -> ${actual_price:.2f} "
              f"({pct_diff:+.1f}%) [{accuracy_rating.upper()}]")

    return {
        "accuracy_rating": accuracy_rating,
        "actual_price": actual_price,
        "price_diff": price_diff,
        "pct_diff": pct_diff,
    }


# ── Ever-hit (TPMetANY) ────────────────────────────────────────────────────

def compute_ever_hit(history, date_posted, target_price,
                     horizon_days=EVER_HIT_HORIZON_DAYS):
    """Whole-window "ever reached" measure for one dated target.

    Evaluates the target against the stock's daily price path over
    [date_posted, min(today, date_posted + horizon_days)]. A touch is
    direction-agnostic: any trading day whose intraday low..high range
    straddles the target (low <= target <= high), so it works for both
    bullish and bearish targets. This mirrors report._whole_window_stats so
    the persisted flag and the on-screen report agree.

    Args:
        history: list of dicts with price_date, low, high, close (ascending).
        date_posted: 'YYYY-MM-DD' the target was issued.
        target_price: the analyst's target price.
        horizon_days: window length (default 365).

    Returns:
        Dict {ever_hit: bool, first_hit_date: str|None, days_to_hit: int|None}
        or None if date_posted is missing/invalid, target_price <= 0, or no
        price data falls in the window.
    """
    if not date_posted or target_price is None or target_price <= 0 or not history:
        return None
    try:
        start_dt = datetime.strptime(date_posted, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None

    end_str = (start_dt + timedelta(days=horizon_days)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')
    if end_str > today:
        end_str = today

    met = False
    first_hit_date = None
    days_to_hit = None
    for p in history:
        d = p.get('price_date')
        if not d or not (date_posted <= d <= end_str):
            continue
        low = p.get('low')
        high = p.get('high')
        if low is None or high is None:
            continue
        if low <= target_price <= high:
            if not met:
                met = True
                first_hit_date = d
                days_to_hit = (datetime.strptime(d, '%Y-%m-%d') - start_dt).days

    if not met and first_hit_date is None:
        # Distinguish "evaluated, never touched" from "no data in window":
        # only return a 0 verdict if at least one in-window day had a usable
        # low/high range. Otherwise return None so it's re-tried later.
        if not any(p.get('low') is not None and p.get('high') is not None
                   and date_posted <= (p.get('price_date') or '') <= end_str
                   for p in history):
            return None

    return {
        'ever_hit': met,
        'first_hit_date': first_hit_date,
        'days_to_hit': days_to_hit,
    }


def update_ever_hit_flags(symbol=None, histories=None):
    """Evaluate and persist the ever-hit flag for all due dated targets.

    Uses one daily price history per symbol (over [earliest date_posted,
    today]) and evaluates every eligible target of that symbol against it.
    Hits are sticky; a 0 is re-evaluated while its window is still open.

    Args:
        symbol: limit to one symbol (uppercase). None = all symbols.
        histories: Optional pre-fetched {symbol: [history rows]} (e.g. the
                   batch shared with the checkpoint pass in
                   run_accuracy_checks). When None, one batched download
                   is made for all due symbols.

    Returns:
        Dict {evaluated: int, hit: int, errors: int}.
    """
    due = get_targets_needing_ever_hit(symbol=symbol)
    if not due:
        return {"evaluated": 0, "hit": 0, "errors": 0}

    # Group eligible targets by symbol so each symbol's history is fetched once.
    by_symbol = {}
    for t in due:
        by_symbol.setdefault(t["symbol"], []).append(t)

    if histories is None:
        histories = price_fetcher.fetch_price_histories(
            {sym: min(t["date_posted"] for t in targets)
             for sym, targets in by_symbol.items()}
        )

    evaluated = 0
    hit_count = 0
    errors = 0

    for sym, targets in by_symbol.items():
        history = histories.get(sym) or []
        if not history:
            # No price data at all — leave these for a later run rather than
            # stamping a false 0 (which would be sticky once the window closes).
            errors += len(targets)
            continue

        for t in targets:
            try:
                res = compute_ever_hit(history, t["date_posted"], t["target_price"])
                if res is None:
                    # No usable in-window data yet; skip without persisting.
                    continue
                save_ever_hit(t["target_price_id"], res["ever_hit"],
                              first_hit_date=res["first_hit_date"],
                              days_to_hit=res["days_to_hit"])
                evaluated += 1
                if res["ever_hit"]:
                    hit_count += 1
                    firm = t.get("analyst_firm") or t["source"]
                    dth = res["days_to_hit"]
                    dth_str = f" on day {dth}" if dth is not None else ""
                    print(f"    [{sym}] EVER-HIT {firm} ${t['target_price']:.2f} "
                          f"(issued {t['date_posted']}){dth_str}")
            except Exception as e:
                errors += 1
                print(f"    [{sym}] ever-hit error target {t['target_price_id']}: {e}")

    return {"evaluated": evaluated, "hit": hit_count, "errors": errors}