"""
APScheduler setup for daily automatic stock target price fetches.

Runs two cron jobs on weekdays:
1. Fetch job: Fetches analyst targets + current prices for all active symbols
2. Accuracy job: Runs accuracy checkpoint comparisons for due targets

The scheduler calls the same functions as the CLI commands, ensuring
consistent behavior whether run manually or automatically.
"""

import sys
import os
import time
import signal

# Add this module's directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from db import init_db, get_symbols, save_symbol, get_symbol_id
from csv_loader import load_symbols, validate_symbols
from sources import fetch_all_targets
import price_fetcher
from accuracy import run_accuracy_checks


# Default CSV path — the input whitelist (only symbols listed here may be tracked)
DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input", "sample_symbols.csv")


def _load_active_symbols():
    """Load active symbols from the database, refreshing from CSV first."""
    csv_path = DEFAULT_CSV
    if os.path.exists(csv_path):
        try:
            raw_symbols = load_symbols(csv_path)
            valid_symbols, _ = validate_symbols(raw_symbols)
            for entry in valid_symbols:
                save_symbol(entry['symbol'], entry.get('company_name'), entry.get('sector'))
        except Exception:
            pass

    symbols = get_symbols(active_only=True)
    return [s['symbol'] for s in symbols]


def scheduled_fetch():
    """Daily fetch job: fetch targets and current prices for all active symbols.

    Called by the scheduler at the configured time on weekdays.
    """
    from db import save_target_price, save_actual_price

    print(f"\n{'=' * 70}")
    print(f"SCHEDULED FETCH — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}")

    symbols = _load_active_symbols()

    if not symbols:
        print("  No active symbols found. Skipping fetch.")
        return

    print(f"  Fetching targets + prices for {len(symbols)} symbols...\n")

    total_targets = 0
    total_prices = 0

    for symbol in symbols:
        symbol_id = get_symbol_id(symbol)
        if not symbol_id:
            continue

        # Fetch analyst targets (each source module enforces its own rate limit)
        try:
            targets = fetch_all_targets(symbol)
            for target in targets:
                try:
                    save_target_price(
                        symbol_id=symbol_id,
                        source=target['source'],
                        target_price=target['target_price'],
                        rating=target.get('rating'),
                        analyst_name=target.get('analyst_name'),
                        analyst_firm=target.get('analyst_firm'),
                        date_posted=target.get('date_posted'),
                        raw_data=target.get('raw_data'),
                    )
                    total_targets += 1
                except Exception as e:
                    print(f"    [{symbol}] Error saving target: {e}")
        except Exception as e:
            print(f"    [{symbol}] Target fetch failed: {e}")

    # Fetch current prices — one batched download for all symbols
    try:
        price_map = price_fetcher.fetch_current_prices_batch(symbols)
        for symbol in symbols:
            symbol_id = get_symbol_id(symbol)
            if not symbol_id:
                continue
            price_data = price_map.get(symbol)
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
                total_prices += 1
    except Exception as e:
        print(f"    Batch price fetch failed: {e}")

    print(f"\n  Scheduled fetch complete: {total_targets} targets, {total_prices} prices")


def scheduled_accuracy_check():
    """Daily accuracy check job: run checkpoint comparisons for due targets.

    Called by the scheduler at the configured time on weekdays.
    """
    print(f"\n{'=' * 70}")
    print(f"SCHEDULED ACCURACY CHECK — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}")

    run_accuracy_checks()


def start_scheduler(fetch_time="09:30", accuracy_time="16:00"):
    """Start the daily scheduler (blocking).

    Args:
        fetch_time: Time for the daily fetch job (HH:MM format).
        accuracy_time: Time for the daily accuracy check job (HH:MM format).
    """
    init_db()

    scheduler = BlockingScheduler()

    # Parse fetch time
    fetch_parts = fetch_time.split(":")
    fetch_hour, fetch_minute = int(fetch_parts[0]), int(fetch_parts[1])

    scheduler.add_job(
        scheduled_fetch,
        trigger=CronTrigger(hour=fetch_hour, minute=fetch_minute, day_of_week="mon-fri"),
        id="daily_fetch",
        name="Fetch target prices + current prices",
        misfire_grace_time=3600,
    )

    # Parse accuracy time
    acc_parts = accuracy_time.split(":")
    acc_hour, acc_minute = int(acc_parts[0]), int(acc_parts[1])

    scheduler.add_job(
        scheduled_accuracy_check,
        trigger=CronTrigger(hour=acc_hour, minute=acc_minute, day_of_week="mon-fri"),
        id="daily_accuracy",
        name="Run accuracy checkpoint comparisons",
        misfire_grace_time=3600,
    )

    print(f"  Scheduler started.")
    print(f"  Fetch job: weekdays at {fetch_time}")
    print(f"  Accuracy job: weekdays at {accuracy_time}")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n  Scheduler stopped.")