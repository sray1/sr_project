"""
Stock Target Price Tracker — Main CLI entry point.

Fetches analyst target prices for stocks from multiple web sources,
stores them in a SQLite database, and tracks prediction accuracy over
time at 30/90/180/365 day checkpoints.

Usage:
  python stock_target_tracker/tracker.py init                          # Create DB + load CSV
  python stock_target_tracker/tracker.py fetch                         # Fetch targets for all symbols
  python stock_target_tracker/tracker.py fetch --symbols AAPL,MSFT     # Fetch specific symbols
  python stock_target_tracker/tracker.py fetch --source yahoo_finance   # One source only
  python stock_target_tracker/tracker.py prices                        # Fetch current prices
  python stock_target_tracker/tracker.py accuracy                      # Run accuracy checks
  python stock_target_tracker/tracker.py summary                       # View accuracy summary
  python stock_target_tracker/tracker.py detail AAPL                   # Symbol detail
  python stock_target_tracker/tracker.py schedule                      # Start daily scheduler
"""

import argparse
import os
import sys
import time

# Add this module's directory to the path so sibling modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import (
    init_db, save_symbol, get_symbols, get_symbol_id,
    save_target_price, save_actual_price,
    display_summary, display_symbol_detail,
)
from csv_loader import load_symbols, validate_symbols, load_allowed_symbols
from sources import fetch_all_targets, get_available_sources
import price_fetcher
from accuracy import run_accuracy_checks
from utils import run_and_save


# Default CSV path — the input whitelist (only symbols listed here may be tracked)
DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input", "sample_symbols.csv")


# ── Whitelist helpers ───────────────────────────────────────────────────

def _load_whitelist(csv_path):
    """Load the whitelist CSV and sync its symbols into the database.

    The input CSV is the authoritative list of trackable symbols. This loads it,
    saves every symbol (with metadata) into the DB so the DB reflects the
    whitelist, and returns the list of valid symbol entries plus the allowed set.

    Returns:
        Tuple (entries, allowed_set). entries is the list of dicts from
        validate_symbols(); allowed_set is a set of uppercase symbol strings.
        Returns (None, None) if the CSV is missing or empty.
    """
    if not os.path.exists(csv_path):
        print(f"  ERROR: Input CSV not found: {csv_path}")
        print(f"  The CSV is the whitelist of trackable symbols. Run 'init' first, "
              f"or create input/sample_symbols.csv with a 'symbol' column.")
        return None, None

    try:
        raw_symbols = load_symbols(csv_path)
    except ValueError as e:
        print(f"  ERROR: {e}")
        return None, None

    valid_symbols, warnings = validate_symbols(raw_symbols)
    for w in warnings:
        print(f"  WARNING: {w}")

    if not valid_symbols:
        print(f"  No valid symbols found in {csv_path}")
        return None, None

    # Sync every whitelist symbol into the DB (upsert keeps metadata fresh)
    for entry in valid_symbols:
        save_symbol(
            entry['symbol'],
            company_name=entry.get('company_name'),
            sector=entry.get('sector'),
        )

    allowed = {e['symbol'] for e in valid_symbols}
    return valid_symbols, allowed


def _resolve_symbols(args, action):
    """Resolve which symbols to process, enforcing the CSV whitelist.

    Only symbols present in the input CSV may be tracked. If --symbols is given,
    each requested symbol must be in the whitelist; symbols outside it are
    rejected (not added to the DB). Without --symbols, all whitelist symbols are
    used.

    Args:
        args: Parsed argparse args (expects .symbols and .csv attributes).
        action: Verb for messages, e.g. 'fetch', 'fetch prices for'.

    Returns:
        List of symbols to process, or None if there is nothing valid to do.
    """
    csv_path = args.csv or DEFAULT_CSV
    entries, allowed = _load_whitelist(csv_path)
    if allowed is None:
        return None

    if args.symbols:
        requested = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
        rejected = [s for s in requested if s not in allowed]
        symbol_list = [s for s in requested if s in allowed]

        if rejected:
            print(f"  Rejected {len(rejected)} symbol(s) not in the whitelist "
                  f"({os.path.basename(csv_path)}): {', '.join(rejected)}")
            print(f"  Only symbols listed in the input CSV may be tracked. "
                  f"Add them to {csv_path} to track them.")
        if not symbol_list:
            print(f"  None of the requested symbols are in the whitelist. "
                  f"Nothing to {action}.")
            return None
        return symbol_list

    return sorted(allowed)


# ── Command Handlers ─────────────────────────────────────────────────────

def do_init(args):
    """Initialize database and load symbols from CSV."""
    init_db()

    csv_path = args.csv or DEFAULT_CSV
    print(f"\nLoading symbols from {csv_path}...")

    try:
        raw_symbols = load_symbols(csv_path)
    except FileNotFoundError as e:
        print(f"  ERROR: {e}")
        print(f"  Create a CSV file with at least a 'symbol' column, or specify --csv path.")
        return

    valid_symbols, warnings = validate_symbols(raw_symbols)

    for w in warnings:
        print(f"  WARNING: {w}")

    if not valid_symbols:
        print("  No valid symbols found in CSV.")
        return

    added = 0
    skipped = 0
    for entry in valid_symbols:
        symbol_id = save_symbol(
            entry['symbol'],
            company_name=entry.get('company_name'),
            sector=entry.get('sector'),
        )
        if symbol_id:
            added += 1
        else:
            skipped += 1

    print(f"\n  Loaded {len(valid_symbols)} symbols ({added} new, {skipped} already existed)")
    print(f"  Database ready at stock_target_tracker/stock_tracker.db")


def do_fetch(args):
    """Fetch analyst target prices for whitelisted symbols."""
    # Determine which symbols to fetch (enforces the input CSV whitelist)
    symbol_list = _resolve_symbols(args, action="fetch")
    if not symbol_list:
        return

    # Determine which sources to use
    source_list = [args.source] if args.source else None
    if args.source and args.source not in get_available_sources():
        print(f"  Unknown source: {args.source}. Available: {get_available_sources()}")
        return

    print(f"\n  Fetching analyst targets for {len(symbol_list)} symbols...")
    if source_list:
        print(f"  Sources: {source_list}")
    else:
        print(f"  Sources: all ({', '.join(get_available_sources())})")
    print()

    total_targets = 0
    total_errors = 0

    for i, symbol in enumerate(symbol_list, 1):
        # Symbols are already synced into the DB by _resolve_symbols
        symbol_id = get_symbol_id(symbol)
        if not symbol_id:
            symbol_id = save_symbol(symbol)

        print(f"  [{i}/{len(symbol_list)}] {symbol}:")

        try:
            targets = fetch_all_targets(symbol, sources=source_list)
            saved = 0

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
                    saved += 1
                except Exception as e:
                    print(f"    Error saving target: {e}")
                    total_errors += 1

            total_targets += saved
            print(f"    -> Saved {saved} target prices")

        except Exception as e:
            print(f"    ERROR fetching {symbol}: {e}")
            total_errors += 1

        # Small delay between symbols
        if i < len(symbol_list):
            time.sleep(0.5)

    print(f"\n  Fetch complete: {total_targets} targets across {len(symbol_list)} symbols "
          f"({total_errors} errors)")


def do_prices(args):
    """Fetch current stock prices for whitelisted symbols."""
    symbol_list = _resolve_symbols(args, action="fetch prices for")
    if not symbol_list:
        return

    print(f"\n  Fetching prices for {len(symbol_list)} symbols...")

    if args.date:
        # Historical price fetch
        for symbol in symbol_list:
            symbol_id = get_symbol_id(symbol)
            if not symbol_id:
                print(f"    {symbol}: not in database, skipping")
                continue

            price_data = price_fetcher.fetch_price_on_date(symbol, args.date)
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
                print(f"    {symbol}: ${price_data['close']:.2f} ({price_data['price_date']})")
            else:
                print(f"    {symbol}: no data for {args.date}")
    else:
        # Current price fetch
        for symbol in symbol_list:
            symbol_id = get_symbol_id(symbol)
            if not symbol_id:
                print(f"    {symbol}: not in database, skipping")
                continue

            price_data = price_fetcher.fetch_current_price(symbol)
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
                print(f"    {symbol}: ${price_data['close']:.2f} (close {price_data['price_date']})")
            else:
                print(f"    {symbol}: price fetch failed")

    print("\n  Prices saved to database.")


def do_accuracy(args):
    """Run accuracy checks for due target/checkpoint pairs."""
    checkpoint = args.checkpoint
    symbol = args.symbol

    # Validate --symbol against the whitelist (only whitelisted symbols are tracked)
    if symbol:
        csv_path = args.csv or DEFAULT_CSV
        allowed = load_allowed_symbols(csv_path)
        symbol_upper = symbol.upper()
        if allowed and symbol_upper not in allowed:
            print(f"  Symbol {symbol_upper} is not in the whitelist ({os.path.basename(csv_path)}).")
            print(f"  Only symbols listed in the input CSV may be tracked. "
                  f"Add it to {csv_path} to track it.")
            return
        symbol = symbol_upper

    print("\n  Running accuracy checks...")
    run_accuracy_checks(checkpoint_days=checkpoint, symbol=symbol)


def do_summary(args):
    """Display aggregate accuracy summary."""
    display_summary(by_source=args.by_source, by_checkpoint=args.by_checkpoint)


def do_detail(args):
    """Display full detail for a single symbol."""
    display_symbol_detail(args.symbol.upper())


def do_schedule(args):
    """Start the daily scheduler for automatic fetches."""
    from scheduler import start_scheduler

    fetch_time = args.time or "09:30"
    accuracy_time = args.accuracy_time or "16:00"

    print(f"\n  Starting scheduler...")
    print(f"  Fetch time: {fetch_time} (weekdays)")
    print(f"  Accuracy check time: {accuracy_time} (weekdays)")
    print(f"  Press Ctrl+C to stop.\n")

    start_scheduler(fetch_time=fetch_time, accuracy_time=accuracy_time)


# ── CLI Setup ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Stock Target Price Tracker — fetch analyst targets and track accuracy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tracker.py init                                    # Set up database + load CSV
  tracker.py fetch                                    # Fetch targets for all symbols
  tracker.py fetch --symbols AAPL,MSFT --source fmp  # Fetch from FMP only
  tracker.py prices                                   # Get current stock prices
  tracker.py accuracy                                 # Run accuracy checkpoint checks
  tracker.py summary --by-source                      # Accuracy breakdown by source
  tracker.py detail AAPL                              # Full detail for a symbol
  tracker.py schedule --time 09:30                    # Start daily auto-fetch
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize database + load CSV")
    init_parser.add_argument("--csv", type=str, help="Path to symbols CSV file")

    # fetch
    fetch_parser = subparsers.add_parser("fetch", help="Fetch analyst target prices")
    fetch_parser.add_argument("--symbols", type=str, help="Comma-separated symbols (e.g., AAPL,MSFT)")
    fetch_parser.add_argument("--source", type=str, help="Source name only (yahoo_finance, fmp, marketbeat)")
    fetch_parser.add_argument("--csv", type=str, help="Path to symbols CSV file")

    # prices
    prices_parser = subparsers.add_parser("prices", help="Fetch current/historical stock prices")
    prices_parser.add_argument("--symbols", type=str, help="Comma-separated symbols")
    prices_parser.add_argument("--date", type=str, help="Historical date (YYYY-MM-DD)")
    prices_parser.add_argument("--csv", type=str, help="Path to symbols CSV whitelist file")

    # accuracy
    accuracy_parser = subparsers.add_parser("accuracy", help="Run accuracy checkpoint checks")
    accuracy_parser.add_argument("--checkpoint", type=int, choices=[30, 90, 180, 365], help="Specific checkpoint to check")
    accuracy_parser.add_argument("--symbol", type=str, help="Check specific symbol only")
    accuracy_parser.add_argument("--csv", type=str, help="Path to symbols CSV whitelist file")

    # summary
    summary_parser = subparsers.add_parser("summary", help="Display accuracy summary")
    summary_parser.add_argument("--by-source", action="store_true", help="Break down by source")
    summary_parser.add_argument("--by-checkpoint", action="store_true", help="Break down by checkpoint")

    # detail
    detail_parser = subparsers.add_parser("detail", help="Show full detail for a symbol")
    detail_parser.add_argument("symbol", type=str, help="Stock symbol (e.g., AAPL)")

    # schedule
    schedule_parser = subparsers.add_parser("schedule", help="Start daily scheduler")
    schedule_parser.add_argument("--time", type=str, help="Fetch time HH:MM (default: 09:30)")
    schedule_parser.add_argument("--accuracy-time", type=str, help="Accuracy check time HH:MM (default: 16:00)")

    args = parser.parse_args()

    # Initialize database on every command
    init_db()

    if args.command == "init":
        do_init(args)
    elif args.command == "fetch":
        do_fetch(args)
    elif args.command == "prices":
        do_prices(args)
    elif args.command == "accuracy":
        do_accuracy(args)
    elif args.command == "summary":
        do_summary(args)
    elif args.command == "detail":
        do_detail(args)
    elif args.command == "schedule":
        do_schedule(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    run_and_save(main, prefix='stock_tracker_')