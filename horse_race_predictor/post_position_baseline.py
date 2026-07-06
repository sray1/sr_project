"""
Post-position baseline predictor.

A second automated baseline (compare-to-MLO) that needs no external expert
picks: it ranks the active field by post position ascending (inside post =
predicted winner). Post position is a real structural factor - inside posts win
more at some tracks / distances / surface configurations - so this gives the MLO
baseline a comparison point built on a different, non-odds signal.

Like mlo_baseline, it emits a synthetic pick list from a source named
"post_position_baseline" and slots into the existing accuracy machinery
unchanged (accuracy.run_accuracy_checks auto-discovers any source with stored
picks).

Tiebreaks when post positions tie: lower program number (stable, deterministic).
Horses with no post position rank last, also by program number.
"""

SOURCE_NAME = "post_position_baseline"
SOURCE_NAME_OUTSIDE = "post_position_outside"


def predict(entries, top_n=3):
    """Rank active entries by post position; return pick dicts for the top N.

    Args:
        entries: list of entry dicts (the ACTIVE field - callers should filter
                 scratches/MTO/AE first via race.filter_active).
        top_n: number of ranked picks to emit (default 3 -> win/place/show picks).

    Returns:
        list of pick dicts: [{source, horse_name, program_number, rank}, ...]
        for the top_n horses by post position, rank 1 = lowest post (predicted
        winner).
    """
    ranked = sorted(entries, key=_sort_key)
    picks = []
    for i, e in enumerate(ranked[:top_n], 1):
        picks.append({
            "source": SOURCE_NAME,
            "horse_name": e.get("horse_name"),
            "program_number": e.get("program_number"),
            "rank": i,
            "comment": "post-position baseline",
        })
    return picks


def predict_outside(entries, top_n=3):
    """Predict the OUTSIDE (highest) post position; rank 1 = highest post.

    A counter-signal to the inside-post baseline: if inside posts win more, do
    outside posts win less? Completes the post-position family so the comparison
    shows both ends.
    """
    ranked = sorted(entries, key=_outside_sort_key)
    picks = []
    for i, e in enumerate(ranked[:top_n], 1):
        picks.append({
            "source": SOURCE_NAME_OUTSIDE,
            "horse_name": e.get("horse_name"),
            "program_number": e.get("program_number"),
            "rank": i,
            "comment": "post-position outside (highest post)",
        })
    return picks


def _prog_num(e):
    try:
        return int(str(e.get("program_number") or "9999").rstrip("Aa"))
    except (ValueError, TypeError):
        return 9999


def _sort_key(e):
    """Sort key: (post_position, program_number). Missing PP sorts last (9999)."""
    pp = e.get("post_position")
    if pp is None:
        pp = 9999
    return (pp, _prog_num(e))


def _outside_sort_key(e):
    """Sort key: highest post first; missing PP sorts last (not 'highest')."""
    pp = e.get("post_position")
    has = 0 if pp is not None else 1
    return (has, -(pp or 0), _prog_num(e))


def best_pick(entries):
    """Convenience: return the single predicted winner (lowest post position)."""
    picks = predict(entries, top_n=1)
    return picks[0] if picks else None