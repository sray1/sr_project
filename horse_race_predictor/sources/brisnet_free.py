"""
Brisnet free expert picks source (stub).

The HTTP plumbing + parser for Brisnet's free per-card picks page will be wired
in Task #5, mirroring drf_free.py. Until then this returns an empty list so the
consensus pipeline degrades gracefully with the remaining pick sources.
"""

import re


def fetch_picks(race):
    """Fetch Brisnet free picks for a race.

    TODO(Task #5): implement against Brisnet's free picks URL, parse the
    handicapper's ranked selections, and return normalized pick dicts:
        [{source, horse_name, program_number, rank, comment, raw_data}, ...]
    For now, gracefully returns [] (no picks).
    """
    print(f"    [brisnet_free] Not yet wired - skipping {race.track_code} "
          f"R{race.race_number} on {race.race_date}")
    return []