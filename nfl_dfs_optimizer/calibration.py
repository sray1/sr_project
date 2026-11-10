"""
DB-driven per-position projection calibration.

Reads the accuracy history from nfl_accuracy.db and computes each source's
per-position bias — mean(actual - projected) over MATCHED rows (rows the
board actually projected, not salary-fallback fills). A positive bias means
the source under-projects that position, so the correction is added to the
projection before optimizing.

Season evidence (weeks 1-2, 2026): DFF's matched-row bias by position is
small but systematic (TE under, kicker over), and the salary fallback is
overconfident across the board (+35 lineup MAE). Calibration turns those
observed biases into a per-source additive correction.

Gated per (source, position) at MIN_N graded rows: at 9 contests the
kicker sample is 14 rows — too thin to correct on its own (the kicker
model handles kickers separately). Positions under the gate are skipped
with a printed note.

Used via --calibrate (analyzer.py, prediction_tracker.py). The tracker
saves calibrated snapshots as '{source}+cal' so raw and calibrated
accuracy are tracked side by side in the summary.
"""

import os

import accuracy_db as db

MIN_N = 30  # graded (source, position) rows required to trust a correction
SLOPE_MIN_N = 100  # graded rows required to trust a shrinkage slope fit


def position_biases():
    """Per-(source, position) bias from the accuracy DB.

    Bias = AVG(actual_fppg - projected_fppg) over MATCHED rows only —
    rows the source actually projected. Fallback-filled rows carry the
    salary curve's error, not the board's, so they must not pollute the
    board's bias.

    Returns:
        {source: {position: {'bias': float, 'n': int}}} — positions are
        included regardless of n (load_corrections applies the gate);
        {} when there is no accuracy DB yet.
    """
    if not os.path.exists(db.DB_PATH):
        return {}
    conn = db.get_connection()
    try:
        rows = conn.execute("""
            SELECT source, position, COUNT(*) AS n,
                   AVG(actual_fppg - projected_fppg) AS bias
            FROM source_projections
            WHERE actual_fppg IS NOT NULL
              AND projected_fppg IS NOT NULL
              AND matched = 1
              AND position IS NOT NULL
            GROUP BY source, position
        """).fetchall()
    finally:
        conn.close()

    biases = {}
    for row in rows:
        biases.setdefault(row['source'], {})[row['position']] = {
            'bias': row['bias'], 'n': row['n']}
    return biases


def load_corrections(min_n=MIN_N, verbose=True):
    """Corrections to add to projections, gated on sample size.

    Returns:
        {source: {position: correction}} where correction = bias (added to
        the source's projected points). (source, position) pairs with
        n < min_n are excluded — small samples fit noise.
    """
    biases = position_biases()
    corrections = {}
    for source, positions in biases.items():
        for position, stats in sorted(positions.items()):
            if stats['n'] < min_n:
                continue
            corrections.setdefault(source, {})[position] = round(stats['bias'], 2)

    if verbose:
        if not biases:
            print("Calibration: no accuracy history found (run "
                  "prediction_tracker --save/--score first) — projections "
                  "unchanged")
        else:
            print(f"Calibration from accuracy DB (matched rows, "
                  f"n >= {min_n} per source-position):")
            for source, positions in sorted(biases.items()):
                parts = []
                for position, stats in sorted(positions.items()):
                    corr = corrections.get(source, {}).get(position)
                    if corr is not None:
                        parts.append(f"{position} {corr:+.2f} (n={stats['n']})")
                    else:
                        parts.append(f"{position} skipped "
                                     f"(n={stats['n']} < {min_n})")
                print(f"  {source}: " + ', '.join(parts))
    return corrections


def apply_corrections(attached, players, corrections):
    """Add per-position corrections to an attached-projection dict.

    Each row is corrected by its OWN source's bias (a row matched by DFF
    gets DFF's correction; a fallback row gets the fallback curve's), and
    only when that (source, position) has a trusted correction — rows
    labeled 'kicker_model' or 'csv' have no correction history and stay
    untouched.

    Args:
        attached: {player_id: {'projection': float, 'source': str}} —
                  MUTATED in place
        players: Player dicts (player_id, position) the dict was resolved
                 from
        corrections: {source: {position: correction}} from load_corrections

    Returns:
        Number of projections corrected
    """
    corrected = 0
    for player in players:
        info = attached.get(player['player_id'])
        if info is None:
            continue
        correction = (corrections.get(info.get('source'), {})
                      .get(player.get('position')))
        if correction:
            info['projection'] = round(info['projection'] + correction, 2)
            corrected += 1
    return corrected


def position_slopes(mode='classic', min_n=SLOPE_MIN_N):
    """Per-(source, position) OLS fit of actual on projected points.

    The mid-season review found the classic lineup bias (+18.8 pts/lineup)
    is NOT board bias — matched-row bias is ~±1 per position. It is the
    winner's curse: the optimizer selects the top of a noisy projection
    distribution, and selected slots over-project even when the full board
    is unbiased (classic quintiles run +1.2 actual-vs-proj at the bottom
    to -1.0 at the top; 9 slots from a 100+ player pool select much harder
    than showdown's 6 from ~48). Showdown shows no such tilt (small pools,
    slope fits are noise), so the fit is computed per mode and only the
    classic pipeline applies it.

    Slope < 1 means projections must shrink toward the position mean:
    corrected = slope * projected + intercept. Unlike --calibrate's
    additive per-position bias (near zero here), the shrinkage changes
    WHICH lineup is optimal — compressing the studs lets mid-priced value
    players compete for slots.

    Args:
        mode: 'classic' (the winner's-curse mode) — 'showdown' fits are
              returned too if requested, but nothing applies them
        min_n: Gate per (source, position); positions below it are
               excluded from the result

    Returns:
        {source: {position: {'slope', 'intercept', 'n'}}} — only gated
        positions, {} when there is no accuracy DB; fits are per mode
    """
    if not os.path.exists(db.DB_PATH):
        return {}
    conn = db.get_connection()
    try:
        rows = conn.execute("""
            SELECT sp.source, sp.position, COUNT(*) AS n,
                   AVG(sp.projected_fppg) AS mp,
                   AVG(sp.actual_fppg) AS ma,
                   AVG(sp.projected_fppg * sp.actual_fppg) AS mpa,
                   AVG(sp.projected_fppg * sp.projected_fppg) AS mpp
            FROM source_projections sp
            JOIN contests c ON c.contest_id = sp.contest_id
            WHERE c.mode = ?
              AND sp.matched = 1
              AND sp.actual_fppg IS NOT NULL
              AND sp.projected_fppg IS NOT NULL
              AND sp.position IS NOT NULL
            GROUP BY sp.source, sp.position
        """, (mode,)).fetchall()
    finally:
        conn.close()

    fits = {}
    for row in rows:
        if row['n'] < min_n:
            continue
        variance = row['mpp'] - row['mp'] * row['mp']
        if variance <= 0:
            continue
        slope = (row['mpa'] - row['mp'] * row['ma']) / variance
        fits.setdefault(row['source'], {})[row['position']] = {
            'slope': slope,
            'intercept': row['ma'] - slope * row['mp'],
            'n': row['n'],
        }
    return fits


def load_slope_corrections(mode='classic', min_n=SLOPE_MIN_N, verbose=True):
    """Trusted shrinkage fits for pool building, printed for inspection.

    Returns:
        {source: {position: (slope, intercept)}} — apply with
        apply_pool_shrinkage
    """
    fits = position_slopes(mode=mode, min_n=min_n)
    corrections = {source: {pos: (f['slope'], f['intercept'])
                            for pos, f in positions.items()}
                   for source, positions in fits.items()}

    if verbose:
        if not corrections:
            print(f"Shrinkage: no trusted {mode} slope fits yet "
                  f"(need n >= {min_n} graded matched rows per "
                  f"source-position) — projections unchanged")
        else:
            print(f"Classic shrinkage fits from accuracy DB "
                  f"(winner's-curse correction, n >= {min_n}):")
            for source, positions in sorted(corrections.items()):
                parts = [f"{pos} x{slope:.2f}{intercept:+.1f}"
                         for pos, (slope, intercept) in
                         sorted(positions.items())]
                print(f"  {source}: " + ', '.join(parts))
    return corrections


def apply_pool_shrinkage(pool, corrections):
    """Shrink pool projections toward each position's fitted mean.

    Mutates each pool entry's 'projection' in place where the entry's
    own (source, position) has a trusted fit: projection =
    slope * projection + intercept. Entries without a fit (kicker_model
    rows, csv rows, thin positions) are untouched.

    Args:
        pool: Player dicts from build_player_pool (position, source)
        corrections: {source: {position: (slope, intercept)}} from
                     load_slope_corrections

    Returns:
        Number of projections shrunk
    """
    shrunk = 0
    for entry in pool:
        fit = (corrections.get(entry.get('source'), {})
               .get(entry.get('position')))
        if fit is None:
            continue
        slope, intercept = fit
        entry['projection'] = round(slope * entry['projection'] + intercept, 2)
        shrunk += 1
    return shrunk