"""
America's Best Racing (ABR) free expert picks source (stub).

The HTTP plumbing + parser for ABR's free picks will be wired in Task #5,
mirroring drf_free.py. Until then this returns an empty list so the consensus
pipeline degrades gracefully with the remaining pick sources.
"""


def fetch_picks(race):
    """Fetch ABR free picks for a race.

    TODO(Task #5): implement against ABR's free picks page, parse the ranked
    selections, and return normalized pick dicts:
        [{source, horse_name, program_number, rank, comment, raw_data}, ...]
    For now, gracefully returns [] (no picks).
    """
    print(f"    [abr] Not yet wired - skipping {race.track_code} "
          f"R{race.race_number} on {race.race_date}")
    return []