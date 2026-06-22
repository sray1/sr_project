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
    get_symbol_id,
)
import price_fetcher


# Default checkpoint intervals in days
CHECKPOINTS = [30, 90, 180, 365]

# Tolerance for "hit" rating (within this % of target)
HIT_TOLERANCE_PCT = 5.0


def run_accuracy_checks(checkpoint_days=None, symbol=None):
    """Run accuracy checks for all due targets.

    Finds target prices that are old enough for a checkpoint comparison
    but don't have a snapshot yet, then fetches actual prices and computes
    accuracy metrics.

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

    if not targets:
        print("\n  No targets due for accuracy checks.")
        return {"checked": 0, "hits": 0, "miss_low": 0, "miss_high": 0, "no_data": 0, "errors": 0}

    print(f"\n  Found {len(targets)} target/checkpoint pairs due for accuracy check")

    stats = {"checked": 0, "hits": 0, "miss_low": 0, "miss_high": 0, "no_data": 0, "errors": 0}

    for target in targets:
        try:
            result = compute_accuracy_for_target(target)
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

    print(f"\n  Accuracy check complete:")
    print(f"    Checked:    {stats['checked']}")
    print(f"    Hits:       {stats['hits']} (within {HIT_TOLERANCE_PCT}%)")
    print(f"    Miss (low): {stats['miss_low']} (stock exceeded target)")
    print(f"    Miss (high): {stats['miss_high']} (stock fell short)")
    print(f"    No data:    {stats['no_data']}")
    print(f"    Errors:      {stats['errors']}")

    return stats


def compute_accuracy_for_target(target_info):
    """Compute accuracy for a single target at a single checkpoint.

    Args:
        target_info: Dict from get_symbols_needing_accuracy_check(), containing
                     target_price_id, symbol_id, target_price, checkpoint_date,
                     checkpoint_days, symbol.

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