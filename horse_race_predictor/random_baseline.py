"""
Random baseline predictor (chance floor).

A third automated baseline that needs no external picks and no domain signal:
it picks a random permutation of the active field. This establishes the
chance-level win/place/show rate and ROI for the field - the floor against which
the MLO and post-position baselines are measured. If those baselines don't beat
random, they aren't carrying real signal.

Determinism: the permutation is seeded from the race's active entries (sorted by
(program_number, horse_name)) so re-runs reproduce the same picks. The seed does
NOT depend on outcomes, so there is no look-ahead. Different races have different
entry sets, so each race gets an independent seed.

Emits a synthetic pick list from source "random_baseline" and slots into the
existing accuracy machinery unchanged.
"""

import hashlib
import random

SOURCE_NAME = "random_baseline"


def predict(entries, top_n=3, seed_key=None):
    """Randomly rank the active field; return pick dicts for the top N.

    Args:
        entries: list of entry dicts (the ACTIVE field).
        top_n: number of ranked picks to emit (default 3).
        seed_key: optional explicit seed key (e.g. "{track}-{race}-{date}"). If
                  omitted, the seed is derived from the sorted entry names so
                  the result is still deterministic per race.

    Returns:
        list of pick dicts: [{source, horse_name, program_number, rank}, ...]
        for a random top_n of the field, rank 1 = predicted winner.
    """
    if seed_key is not None:
        key = str(seed_key)
    else:
        # Stable identity from the field itself: sort by (prog, name) so the
        # seed is independent of the input order.
        key = "|".join(
            f"{e.get('program_number') or ''}:{e.get('horse_name') or ''}"
            for e in sorted(entries, key=_field_sort_key)
        )
    seed = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    pool = list(entries)
    rng.shuffle(pool)
    picks = []
    for i, e in enumerate(pool[:top_n], 1):
        picks.append({
            "source": SOURCE_NAME,
            "horse_name": e.get("horse_name"),
            "program_number": e.get("program_number"),
            "rank": i,
            "comment": "random baseline",
        })
    return picks


def _field_sort_key(e):
    try:
        prog = int(str(e.get("program_number") or "9999").rstrip("Aa"))
    except (ValueError, TypeError):
        prog = 9999
    return (prog, e.get("horse_name") or "")


def best_pick(entries, seed_key=None):
    """Convenience: return the single predicted winner (random pick)."""
    picks = predict(entries, top_n=1, seed_key=seed_key)
    return picks[0] if picks else None