"""
Source registry and dispatcher for stock target price fetchers.

Provides a unified fetch_all_targets() function that tries each source
in priority order, aggregates results, and logs per-source success/failure.
Follows the same multi-source pattern as the DFS project's game_results.py.
"""

from sources import yahoo_finance, fmp, marketbeat

# Source modules keyed by name
SOURCES = {
    "yahoo_finance": yahoo_finance,
    "fmp": fmp,
    "marketbeat": marketbeat,
}

# Default priority order (most reliable first)
SOURCE_PRIORITY = ["yahoo_finance", "fmp", "marketbeat"]


def fetch_all_targets(symbol, sources=None):
    """Fetch analyst target prices from all (or specified) sources.

    Tries each source in priority order. Continues even if a source fails.
    Returns combined list of target price dicts.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL').
        sources: Optional list of source names to use (e.g., ['yahoo_finance', 'fmp']).
                 If None, uses all sources in SOURCE_PRIORITY order.

    Returns:
        List of dicts: [{source, target_price, rating, analyst_name,
                         analyst_firm, date_posted, raw_data}, ...]
    """
    source_list = sources or SOURCE_PRIORITY
    results = []
    source_stats = {}

    for source_name in source_list:
        if source_name not in SOURCES:
            print(f"    [{source_name}] Unknown source — skipping")
            continue

        try:
            targets = SOURCES[source_name].fetch_targets(symbol)
            count = len(targets)
            source_stats[source_name] = count
            if count > 0:
                prices = [t['target_price'] for t in targets if t.get('target_price')]
                mean_price = sum(prices) / len(prices) if prices else 0
                low_price = min(prices) if prices else 0
                high_price = max(prices) if prices else 0
                print(f"    [{source_name}] {count} targets fetched "
                      f"(mean: ${mean_price:.2f}, range: ${low_price:.2f}-${high_price:.2f})")
            else:
                print(f"    [{source_name}] No targets found")
            results.extend(targets)
        except Exception as e:
            source_stats[source_name] = 0
            print(f"    [{source_name}] Failed for {symbol}: {e}")

    return results


def get_available_sources():
    """Return list of available source names."""
    return list(SOURCES.keys())