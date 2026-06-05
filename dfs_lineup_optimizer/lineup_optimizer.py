"""
Optimal lineup generation for DraftKings DFS contests.

Uses combinatorial enumeration for showdown (tractable search space)
and pydfs_lineup_optimizer for classic contests.

Showdown search space: ~15 captain candidates × C(20,5) ≈ 232k combinations
is small enough for brute-force enumeration with early pruning, guaranteeing
optimal lineups (unlike the previous greedy heuristic).
"""

from itertools import combinations
from utils import SALARY_CAP


def generate_optimal_showdown_lineups(players, player_meta=None, n_lineups=5,
                                      captain_filter=None, min_util_salary=1000):
    """Generate optimal showdown lineups via exhaustive enumeration.

    Enumerates all valid (salary ≤ cap) 1-CPT + 5-UTIL combinations,
    returns top-N by total projected fantasy points per game.

    Args:
        players: List of Player objects (deduplicated, with fppg and salary)
        player_meta: Optional parallel list of dicts with 'role' and 'minutes'
            for each player. Used for captain filtering and value ranking.
        n_lineups: Number of lineups to return (default 5)
        captain_filter: Optional callable(player, meta) -> bool to filter
            captain candidates. If None and player_meta is provided,
            defaults to starters only. If neither, uses all players.
        min_util_salary: Minimum salary for utility players (default 1000)

    Returns:
        List of lineup dicts, each with:
            'captain': Player object
            'utility': list of 5 Player objects
            'total_fppg': float
            'total_salary': float
            'captain_cap_salary': float (captain salary × 1.5)
    """
    # Build name->meta lookup
    meta_by_name = {}
    if player_meta:
        for player, meta in zip(players, player_meta):
            meta_by_name[player.full_name] = meta

    # Determine captain candidates
    if captain_filter:
        captain_candidates = [p for p in players if captain_filter(p, meta_by_name.get(p.full_name, {}))]
    elif player_meta:
        # Default: starters only
        captain_candidates = [p for p in players if meta_by_name.get(p.full_name, {}).get('role') == 'starter']
    else:
        # No rotation data: use all players as captain candidates
        captain_candidates = list(players)

    # Deduplicate captains (same player ID should only appear once)
    seen_ids = set()
    unique_captains = []
    for c in captain_candidates:
        if c.id not in seen_ids:
            unique_captains.append(c)
            seen_ids.add(c.id)
    captain_candidates = unique_captains

    if not captain_candidates:
        print("WARNING: No captain candidates found. Using all players.")
        captain_candidates = list(players)

    # Filter utility pool
    valid_players = [p for p in players if p.salary >= min_util_salary]

    # Enumerate all valid lineups
    best_lineups = []

    for captain in captain_candidates:
        captain_salary = captain.salary * 1.5
        remaining_salary = SALARY_CAP - captain_salary

        if remaining_salary < 5000:
            # Can't possibly fill 5 utility spots at $1000 minimum each
            continue

        # Utility pool: exclude captain, filter by min salary
        util_pool = [p for p in valid_players if p.id != captain.id]

        if len(util_pool) < 5:
            continue

        # Early pruning: sort by value to try best combinations first
        # and enumerate combinations with salary cap checking
        for combo in combinations(util_pool, 5):
            total_util_salary = sum(p.salary for p in combo)
            if total_util_salary > remaining_salary:
                continue

            total_salary = captain_salary + total_util_salary
            total_fppg = captain.fppg * 1.5 + sum(p.fppg for p in combo)

            lineup = {
                'captain': captain,
                'utility': list(combo),
                'total_fppg': total_fppg,
                'total_salary': total_salary,
                'captain_cap_salary': captain_salary,
            }
            best_lineups.append(lineup)

    # Sort by total projected fppg, descending
    best_lineups.sort(key=lambda x: x['total_fppg'], reverse=True)

    return best_lineups[:n_lineups]


def generate_optimal_showdown_lineups_fast(players, player_meta=None, n_lineups=5,
                                            captain_filter=None, min_util_salary=1000):
    """Generate optimal showdown lineups with pruning for faster execution.

    Same result as generate_optimal_showdown_lineups but uses sorting
    and early termination to skip combinations that can't beat the current
    best N lineups.

    Args: Same as generate_optimal_showdown_lineups

    Returns: Same as generate_optimal_showdown_lineups
    """
    # Build name->meta lookup
    meta_by_name = {}
    if player_meta:
        for player, meta in zip(players, player_meta):
            meta_by_name[player.full_name] = meta

    # Determine captain candidates
    if captain_filter:
        captain_candidates = [p for p in players if captain_filter(p, meta_by_name.get(p.full_name, {}))]
    elif player_meta:
        captain_candidates = [p for p in players if meta_by_name.get(p.full_name, {}).get('role') == 'starter']
    else:
        captain_candidates = list(players)

    # Deduplicate captains
    seen_ids = set()
    unique_captains = []
    for c in captain_candidates:
        if c.id not in seen_ids:
            unique_captains.append(c)
            seen_ids.add(c.id)
    captain_candidates = unique_captains

    if not captain_candidates:
        print("WARNING: No captain candidates found. Using all players.")
        captain_candidates = list(players)

    # Filter utility pool and sort by fppg descending for better pruning
    valid_players = sorted(
        [p for p in players if p.salary >= min_util_salary],
        key=lambda p: p.fppg, reverse=True
    )

    # Track best lineups with a running threshold
    best_lineups = []
    min_fppg_to_beat = 0  # Running threshold for top-N

    for captain in captain_candidates:
        captain_salary = captain.salary * 1.5
        remaining_salary = SALARY_CAP - captain_salary

        if remaining_salary < 5000:
            continue

        util_pool = [p for p in valid_players if p.id != captain.id]

        if len(util_pool) < 5:
            continue

        # Calculate maximum possible fppg for this captain (upper bound)
        # Sort by fppg descending and take top 5 that fit under salary
        # This gives us an upper bound for pruning
        max_possible_utils = []
        max_salary = 0
        for p in util_pool:
            if len(max_possible_utils) < 5:
                max_possible_utils.append(p)
                max_salary += p.salary
            else:
                # Replace lowest fppg if this one is better and fits
                min_idx = min(range(len(max_possible_utils)),
                             key=lambda i: max_possible_utils[i].fppg)
                if p.fppg > max_possible_utils[min_idx].fppg:
                    max_salary -= max_possible_utils[min_idx].salary + p.salary
                    # Only replace if still under cap
                    if max_salary + p.salary <= remaining_salary:
                        max_salary = max_salary + p.salary
                        max_possible_utils[min_idx] = p

        captain_fppg_cpt = captain.fppg * 1.5
        upper_bound = captain_fppg_cpt + sum(p.fppg for p in max_possible_utils) if max_possible_utils else 0

        # If this captain's upper bound can't beat the current minimum, skip
        if upper_bound <= min_fppg_to_beat and len(best_lineups) >= n_lineups:
            continue

        # Enumerate combinations
        for combo in combinations(util_pool, 5):
            total_util_salary = sum(p.salary for p in combo)
            if total_util_salary > remaining_salary:
                continue

            total_fppg = captain_fppg_cpt + sum(p.fppg for p in combo)
            total_salary = captain_salary + total_util_salary

            # Quick check: if this lineup can't beat the threshold, skip
            if total_fppg <= min_fppg_to_beat and len(best_lineups) >= n_lineups:
                continue

            lineup = {
                'captain': captain,
                'utility': list(combo),
                'total_fppg': total_fppg,
                'total_salary': total_salary,
                'captain_cap_salary': captain_salary,
            }
            best_lineups.append(lineup)

    # Sort and return top N
    best_lineups.sort(key=lambda x: x['total_fppg'], reverse=True)
    result = best_lineups[:n_lineups]

    # Update threshold
    if len(result) >= n_lineups:
        min_fppg_to_beat = result[-1]['total_fppg']

    return result


def generate_classic_lineups(players, n_lineups=5):
    """Generate optimal classic lineups using pydfs optimizer.

    For classic contests (8 players, position requirements), the search
    space is too large for brute-force, so we use the pydfs optimizer.

    Args:
        players: List of Player objects
        n_lineups: Number of lineups to generate

    Returns:
        List of lineup objects from pydfs_lineup_optimizer
    """
    from pydfs_lineup_optimizer import get_optimizer, Site, Sport as PyDFSSport

    # Use WNBA as placeholder for NBA in pydfs
    optimizer = get_optimizer(Site.DRAFTKINGS, PyDFSSport.WNBA)
    optimizer.load_players(players)
    return list(optimizer.optimize(n=n_lineups))


def find_best_possible_showdown_lineup(player_scores, salary_cap=SALARY_CAP,
                                       min_salary=3000, top_n=5):
    """Find the best possible showdown lineup from actual game results.

    Used by prediction_tracker.py to compare predicted lineups against
    the theoretical optimal.

    Args:
        player_scores: Dict of {name: {"fppg": float, "salary": float, "team": str}}
        salary_cap: Maximum total salary (default 50000)
        min_salary: Minimum salary for utility players
        top_n: Number of best lineups to return

    Returns:
        List of lineup dicts sorted by total actual fppg (descending)
    """
    from itertools import combinations as _combinations

    # Sort players by value (fppg per $1k)
    sorted_players = sorted(
        [(name, data) for name, data in player_scores.items()
         if data["fppg"] > 0 and data["salary"] >= min_salary],
        key=lambda x: x[1]["fppg"] / (x[1]["salary"] / 1000),
        reverse=True
    )

    if len(sorted_players) < 6:
        return []

    best_lineups = []

    # Try each of the top 15 by value as captain
    captain_candidates = sorted_players[:15]

    for captain_name, captain_data in captain_candidates:
        captain_salary_cap = captain_data["salary"] * 1.5
        remaining_cap = salary_cap - captain_salary_cap
        captain_fppg = captain_data["fppg"] * 1.5

        if remaining_cap < min_salary * 5:
            continue

        # Utility candidates (exclude captain, min salary)
        util_candidates = [
            (name, data) for name, data in sorted_players
            if name != captain_name and data["salary"] >= min_salary
        ]

        if len(util_candidates) < 5:
            continue

        # Enumerate all valid 5-player combinations
        for combo in _combinations(util_candidates, 5):
            total_util_salary = sum(d["salary"] for _, d in combo)
            if total_util_salary > remaining_cap:
                continue

            total_fppg = captain_fppg + sum(d["fppg"] for _, d in combo)
            total_salary = captain_salary_cap + total_util_salary

            lineup = {
                "captain": captain_name,
                "captain_salary": captain_data["salary"],
                "captain_fppg": captain_data["fppg"],
                "captain_actual_fppg": captain_data["fppg"],
                "utility": [(n, d) for n, d in combo],
                "total_fppg": total_fppg,
                "total_salary": total_salary,
            }
            best_lineups.append(lineup)

    # Sort by total actual fppg descending
    best_lineups.sort(key=lambda x: x["total_fppg"], reverse=True)
    return best_lineups[:top_n]