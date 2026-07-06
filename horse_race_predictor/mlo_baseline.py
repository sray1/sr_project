"""
Morning-Line-Odds (MLO) baseline predictor.

A fully automated predictor that needs no external expert picks: it ranks the
active field by morning-line odds ascending (lowest MLO = predicted winner) and
emits a synthetic pick list from a source named "mlo_baseline". This slots into
the existing consensus + accuracy machinery unchanged, so the backtest can run
end-to-end without manual pick entry.

Rationale: morning-line odds are the track's handicapper's estimate of each
horse's win probability. Betting the lowest-MLO horse is a naive-but-real
baseline against which any expert-pick consensus should be measured. For a
backtest where expert picks can't be auto-fetched, the MLO baseline is the
prediction; accuracy vs actual winners is then a measure of how well the
morning line itself predicts outcomes.

Tiebreaks when MLOs tie: lower program number (stable, deterministic). Horses
with no MLO rank last, also by program number.
"""

SOURCE_NAME = "mlo_baseline"


def predict(entries, top_n=3):
    """Rank active entries by morning-line odds; return pick dicts for the top N.

    Args:
        entries: list of entry dicts (the ACTIVE field - callers should filter
                 scratches/MTO/AE first via race.filter_active).
        top_n: number of ranked picks to emit (default 3 -> win/place/show picks).

    Returns:
        list of pick dicts: [{source, horse_name, program_number, rank}, ...]
        for the top_n horses by MLO, rank 1 = lowest MLO (predicted winner).
    """
    ranked = sorted(entries, key=_sort_key)
    picks = []
    for i, e in enumerate(ranked[:top_n], 1):
        picks.append({
            "source": SOURCE_NAME,
            "horse_name": e.get("horse_name"),
            "program_number": e.get("program_number"),
            "rank": i,
            "comment": "MLO baseline",
        })
    return picks


def _sort_key(e):
    """Sort key: (mlo, program_number). Missing MLO sorts last (9999)."""
    mlo = e.get("morning_line_odds")
    if mlo is None:
        mlo = 9999.0
    try:
        # Numeric program number for stable ordering; non-numeric sorts last.
        prog = int(str(e.get("program_number") or "9999").rstrip("Aa"))
    except (ValueError, TypeError):
        prog = 9999
    return (mlo, prog)


def best_pick(entries):
    """Convenience: return the single predicted winner (lowest MLO) entry dict."""
    picks = predict(entries, top_n=1)
    return picks[0] if picks else None