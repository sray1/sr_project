"""
MLO-family variant predictors (compare-to-MLO baselines).

Same data as mlo_baseline (morning-line odds) but different selection rules,
to probe the shape of the morning line vs. outcomes:
  - mlo_second   : 2nd-lowest MLO  ("beat the favorite" / value play)
  - mlo_third    : 3rd-lowest MLO
  - mlo_longshot : HIGHEST MLO (longest shot on the board) - directly tests the
    favorite-longshot bias direction: does systematically backing longshots lose
    more or less than backing the favorite?

Each predict_* emits picks under its own SOURCE_NAME so accuracy.run_accuracy_checks
auto-scores it as a separate predictor. Tiebreaks: lower program number (stable,
deterministic). Horses with no MLO sort last for the favorite variants and last
for the longshot variant (unknown odds aren't treated as "longest").
"""

SOURCE_2ND = "mlo_second"
SOURCE_3RD = "mlo_third"
SOURCE_LONGSHOT = "mlo_longshot"


def _prog_num(e):
    try:
        return int(str(e.get("program_number") or "9999").rstrip("Aa"))
    except (ValueError, TypeError):
        return 9999


def _mlo(e):
    mlo = e.get("morning_line_odds")
    return mlo if mlo is not None else 9999.0


def _ascending_key(e):
    """Lowest MLO first; no-MLO sorts last."""
    has = 0 if e.get("morning_line_odds") is not None else 1
    return (has, _mlo(e), _prog_num(e))


def _descending_key(e):
    """Highest MLO first (longshot); no-MLO sorts last (not treated as longest)."""
    has = 0 if e.get("morning_line_odds") is not None else 1
    return (has, -_mlo(e), _prog_num(e))


def _picks(order, source, top_n, comment):
    picks = []
    for i, e in enumerate(order[:top_n], 1):
        picks.append({
            "source": source,
            "horse_name": e.get("horse_name"),
            "program_number": e.get("program_number"),
            "rank": i,
            "comment": comment,
        })
    return picks


def predict_second(entries, top_n=3):
    """Predict the 2nd-lowest MLO horse (rank 1 = 2nd choice)."""
    order = sorted(entries, key=_ascending_key)
    return _picks(order[1:], SOURCE_2ND, top_n, "MLO 2nd-choice")


def predict_third(entries, top_n=3):
    """Predict the 3rd-lowest MLO horse (rank 1 = 3rd choice)."""
    order = sorted(entries, key=_ascending_key)
    return _picks(order[2:], SOURCE_3RD, top_n, "MLO 3rd-choice")


def predict_longshot(entries, top_n=3):
    """Predict the longest shot (highest MLO); rank 1 = longest shot."""
    order = sorted(entries, key=_descending_key)
    return _picks(order, SOURCE_LONGSHOT, top_n, "MLO longshot (highest odds)")