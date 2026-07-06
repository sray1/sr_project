"""
Equibase official results source (stub).

The HTTP plumbing + parser for Equibase's free results pages will be wired in
Task #6 (the reconcile phase), mirroring equibase.py's entries fetcher. Until
then this returns an empty list so the `results` CLI command degrades
gracefully with a clear message.
"""


def fetch_results(race):
    """Fetch official finish order for a race from Equibase.

    TODO(Task #6): implement against Equibase's results URL (parallel to the
    entries page), parse the finish order + win/place/show payoffs, and return
    normalized result dicts:
        [{program_number, horse_name, finish_position, win_payoff,
          place_payoff, show_payoff}, ...]
    For now, gracefully returns [] (no results).
    """
    print(f"    [equibase_results] Not yet wired - no results for "
          f"{race.track_code} R{race.race_number} on {race.race_date}")
    return []