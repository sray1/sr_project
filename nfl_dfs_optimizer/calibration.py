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