"""
Source registry and dispatcher for Horse Race Predictor.

Three source categories, each a registry of modules:
  - ENTRY sources  - the authoritative horse list for a race (we take the first
                     source that returns entries; entries should agree across sources).
  - PICK sources   - expert top selections; we aggregate ALL that succeed.
  - RESULT sources - official finish order (take the first that succeeds).

Mirrors the multi-source pattern in stock_target_tracker/sources/__init__.py.
"""

from sources import equibase, equibase_results, drf_free, brisnet_free, abr, hrn, bloodhorse, equibase_parse

# Entry sources keyed by name (most reliable first). HRN is server-rendered and
# not bot-walled, so it's the primary entries source; Equibase is a fallback.
ENTRY_SOURCES = {
    "hrn": hrn,
    "equibase": equibase,
}
ENTRY_PRIORITY = ["hrn", "equibase"]

# Pick sources keyed by name
PICK_SOURCES = {
    "drf_free": drf_free,
    "brisnet_free": brisnet_free,
    "abr": abr,
}
PICK_PRIORITY = ["drf_free", "brisnet_free", "abr"]

# Result sources keyed by name (most reliable first).
# hrn (Horse Racing Nation direct scrape) is the primary results source: the
# same server-rendered entries-results page also carries per-race payouts tables
# with full top-4 finish order + $2 win/place/show payoffs, free and unlimited
# (no API budget). The parse.bot sources (bloodhorse bulk top-3, equibase_parse
# per-race + payoffs) are FALLBACKS for races HRN hasn't populated. equibase_results
# is a bot-walled stub kept last for completeness.
RESULT_SOURCES = {
    "hrn": hrn,
    "bloodhorse": bloodhorse,
    "equibase_parse": equibase_parse,
    "equibase_results": equibase_results,
}
RESULT_PRIORITY = ["hrn", "bloodhorse", "equibase_parse", "equibase_results"]


def fetch_entries(race, sources=None):
    """Fetch the horse list (entries) for a race.

    Tries entry sources in priority order and returns the first non-empty list.
    Entries are authoritative - we don't merge across sources (they should
    agree); we take whichever source succeeds first.

    Returns:
        List of normalized entry dicts:
        [{program_number, horse_name, jockey, trainer, morning_line_odds,
          post_position, scratched}, ...]
        Empty list if every entry source fails.
    """
    for source_name in (sources or ENTRY_PRIORITY):
        if source_name not in ENTRY_SOURCES:
            print(f"    [{source_name}] Unknown entry source - skipping")
            continue
        try:
            entries = ENTRY_SOURCES[source_name].fetch_entries(race)
            count = len(entries)
            if count > 0:
                print(f"    [{source_name}] {count} entries fetched")
                return entries
            print(f"    [{source_name}] No entries returned")
        except Exception as e:
            print(f"    [{source_name}] Failed: {e}")
    return []


def fetch_all_picks(race, sources=None):
    """Fetch expert picks for a race from all (or specified) pick sources.

    Tries each source in priority order. Continues even if a source fails.
    Returns combined list of normalized pick dicts and per-source stats.

    Returns:
        Tuple (picks, stats) where picks is a list of dicts:
        [{source, horse_name, program_number, rank, comment, raw_data}, ...]
        and stats maps source_name -> count.
    """
    source_list = sources or PICK_PRIORITY
    results = []
    stats = {}

    for source_name in source_list:
        if source_name not in PICK_SOURCES:
            print(f"    [{source_name}] Unknown pick source - skipping")
            continue
        try:
            picks = PICK_SOURCES[source_name].fetch_picks(race)
            count = len(picks)
            stats[source_name] = count
            if count > 0:
                print(f"    [{source_name}] {count} picks fetched")
            else:
                print(f"    [{source_name}] No picks found")
            results.extend(picks)
        except Exception as e:
            stats[source_name] = 0
            print(f"    [{source_name}] Failed: {e}")

    return results, stats


def fetch_results(race, sources=None):
    """Fetch official finish order for a race.

    Tries result sources in priority order; returns the first non-empty list.

    Returns:
        List of normalized result dicts:
        [{program_number, horse_name, finish_position, win_payoff,
          place_payoff, show_payoff}, ...]
        Empty list if every result source fails.
    """
    for source_name in (sources or RESULT_PRIORITY):
        if source_name not in RESULT_SOURCES:
            print(f"    [{source_name}] Unknown result source - skipping")
            continue
        try:
            results = RESULT_SOURCES[source_name].fetch_results(race)
            count = len(results)
            if count > 0:
                print(f"    [{source_name}] {count} finishers fetched")
                return results
            print(f"    [{source_name}] No results returned")
        except Exception as e:
            print(f"    [{source_name}] Failed: {e}")
    return []


def get_available_entry_sources():
    return list(ENTRY_SOURCES.keys())


def get_available_pick_sources():
    return list(PICK_SOURCES.keys())


def get_available_result_sources():
    return list(RESULT_SOURCES.keys())