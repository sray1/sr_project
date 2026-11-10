"""
DB-driven flagship single-entry pick for showdown captain builds.

Why this exists: the unique-captain builds are ordered by projected
total, but the chalk build (rank 1) is the argmax of noisy estimates —
the winner's curse hits the captain slot hardest, since 1.5x multiplies
the most-selected player's error. Retro-grading every scored snapshot
in nfl_accuracy.db (weeks 1-9 2026, 27 contests): build rank 1
averaged -5.8 actual-vs-projected, rank 3 only -1.3 — the lower-ranked
builds beat the #1 build more often than not (TB@CHI rank 5 won,
JAX@BAL rank 4 won, BUF@MIN rank 3 won).

The pick: retro-grade every scored showdown snapshot — regenerate each
contest's 5 unique-captain builds from its saved pool, score each
against recorded actuals — and aggregate mean projected/actual per
build rank. A build's DB-corrected expectation is then its projected
total plus its rank's historical (mean_actual - mean_proj). The
flagship is the build with the best corrected expectation, which lands
on rank 2-3 in practice instead of paying the chalk's toll.

Self-updating: every graded contest sharpens the rank corrections, so
the flagship re-fits from the DB on each run. Gated at
MIN_SNAPSHOTS graded contests per rank — below that the module stays
silent and the chalk order stands.
"""

import os

import accuracy_db as db
from showdown_optimizer import generate_showdown_lineups

SNAPSHOT_SOURCE = 'dailyfantasyfuel'  # the pipeline's board of record
N_BUILDS = 5                         # unique-captain builds per snapshot
MIN_SNAPSHOTS = 10                   # graded contests required per rank
MIN_PLAYERS = 10                     # scored rows required in a pool


def captain_rank_stats(n_builds=N_BUILDS, min_snapshots=MIN_SNAPSHOTS,
                       min_players=MIN_PLAYERS):
    """Retro-grade every scored showdown snapshot, per build rank.

    For each graded showdown contest with a saved dailyfantasyfuel
    snapshot: rebuild the n_builds unique-captain lineups from the saved
    pool (the exact optimizer the live pipeline uses), score each
    against the contest's recorded actuals (players without recorded
    actuals count 0, matching how the tracker grades lineups), and
    accumulate projected/actual totals per build rank.

    Returns:
        {rank: {'n', 'mean_proj', 'mean_actual'}} — every rank carries
        the same n (snapshots with fewer than n_builds builds are
        skipped entirely); {} when too few snapshots to trust anything
    """
    if not db.DB_PATH or not os.path.exists(db.DB_PATH):
        return {}

    conn = db.get_connection()
    try:
        contest_rows = conn.execute("""
            SELECT DISTINCT sp.contest_id
            FROM source_projections sp
            JOIN contests c ON c.contest_id = sp.contest_id
            WHERE sp.source = ?
              AND c.mode = 'showdown'
              AND sp.actual_fppg IS NOT NULL
        """, (SNAPSHOT_SOURCE,)).fetchall()

        stats = {}  # rank -> {'projs': [], 'actuals': []}
        for row in contest_rows:
            contest_id = row['contest_id']
            players = conn.execute("""
                SELECT player_id, player_name, team, position, salary,
                       projected_fppg, actual_fppg
                FROM source_projections
                WHERE contest_id = ? AND source = ?
            """, (contest_id, SNAPSHOT_SOURCE)).fetchall()
            scored = [p for p in players if p['actual_fppg'] is not None]
            if len(scored) < min_players:
                continue  # unplayed slate / empty snapshot

            pool = [{'player_id': p['player_id'],
                     'name': p['player_name'],
                     'team': p['team'],
                     'positions': [p['position']],
                     'salary': p['salary'],
                     'projection': p['projected_fppg']}
                    for p in players]
            actuals = {p['player_id']: p['actual_fppg'] for p in scored}

            lineups = generate_showdown_lineups(
                pool, n_lineups=n_builds, unique_captains=True)
            if len(lineups) < n_builds:
                continue  # thin pool: ranks would not align

            for rank, lineup in enumerate(lineups, 1):
                members = [lineup['captain']] + lineup['flex']
                actual = sum(
                    (actuals.get(p['player_id']) or 0.0)
                    * (1.5 if p is lineup['captain'] else 1.0)
                    for p in members)
                bucket = stats.setdefault(
                    rank, {'projs': [], 'actuals': []})
                bucket['projs'].append(lineup['total_projection'])
                bucket['actuals'].append(actual)
    finally:
        conn.close()

    result = {}
    for rank, bucket in stats.items():
        n = len(bucket['projs'])
        if n < min_snapshots:
            return {}  # any ungated rank invalidates the whole table
        result[rank] = {
            'n': n,
            'mean_proj': sum(bucket['projs']) / n,
            'mean_actual': sum(bucket['actuals']) / n,
        }
    return result


def flagship_pick(lineups, stats):
    """The single-entry flagship build, by DB-corrected expectation.

    corrected_total(rank) = total_projection + (mean_actual - mean_proj)
    at that rank, from the retro-grade stats. The pick is the argmax —
    in practice the rank-2/3 build, not the chalk #1.

    Args:
        lineups: Unique-captain builds from generate_showdown_lineups
                 (ordered by projected total, descending)
        stats: {rank: {'n', 'mean_proj', 'mean_actual'}} from
               captain_rank_stats

    Returns:
        (index, expected_gain) — the build's index in lineups and its
        corrected advantage over the chalk build (index 0), or None
        when the stats are missing/ungated or the build ranks run past
        the table
    """
    if not stats or not lineups:
        return None

    best_index = None
    best_total = None
    for index, lineup in enumerate(lineups):
        rank = index + 1
        bucket = stats.get(rank)
        if bucket is None:
            return None  # build set and stats table disagree
        corrected = (lineup['total_projection']
                     + bucket['mean_actual'] - bucket['mean_proj'])
        if best_total is None or corrected > best_total:
            best_total = corrected
            best_index = index

    chalk = lineups[0]['total_projection'] + (
        stats[1]['mean_actual'] - stats[1]['mean_proj'])
    return best_index, best_total - chalk
