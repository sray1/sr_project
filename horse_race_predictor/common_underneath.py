"""
Common-underneath baseline for Horse Race Predictor.

Signal (observed Parx 2026-09-19 and Remington 2026-09-25): when the expert
panel agrees on the *identity* of the underneath horses, the deepest
common-ballot horse - the one every source names but nobody ranks on top -
wins at a surprising rate. All six winners on those two cards were rank-2/3
ballot horses, three of them unanimous rank-3s: the exact slot consensus
scores at zero points.

Definition, per race:
  - Expert ballots = stored picks from expert sources (baselines and
    consensus itself excluded).
  - Candidates = horses named by EVERY expert ballot in the race.
  - Pick = candidate with the deepest average rank (highest average =
    least loved by the panel as a whole).
  - Ties broken by higher morning-line odds (the price angle), then
    program number.
  - Abstains (returns no pick) when fewer than MIN_SOURCES expert ballots
    exist or no horse is common to all ballots. Abstention is honest: the
    signal is about shared sentiment, and ballots that don't overlap have
    none.

Rank normalization: ballot ranks 1-3 are used as stored; an explicit 4th+
choice keeps its depth; an unranked mention counts as 4 (a name on a
ballot at unknown depth is a lukewarm endorsement, mirroring the
deep-rank logic in consensus.aggregate).
"""

from statistics import mean

from race import normalize_horse_name
from consensus import _to_entry, _entries_index, _resolve_pick

SOURCE_NAME = "common_underneath"

# Pick sources that are not expert ballots. Baselines are mechanical, and
# consensus is derived from the same ballots this baseline reads, so it
# must be excluded to avoid feeding the signal back into itself.
NON_EXPERT_SOURCES = {
    "mlo_favorite", "mlo_baseline", "mlo_second", "mlo_third", "mlo_longshot",
    "post_position_baseline", "post_position_outside", "random_baseline",
    "leading_jockey", "leading_trainer",
    "consensus", SOURCE_NAME,
}

# Below this many ballots "common to all" degenerates (a single ballot's
# 3rd choice is just that expert's 3rd choice, not a shared afterthought).
MIN_SOURCES = 2


def predict(entries, picks):
    """Return the common-underneath pick for a race, or [] to abstain.

    Args:
        entries: list of Entry objects or dicts (the race's horse list).
        picks:   list of pick dicts - all stored picks for the race
                 (non-expert sources are filtered out here).

    Returns:
        A list of 0 or 1 pick dicts (SOURCE_NAME, rank 1) shaped like the
        other predictors' output, so db.save_picks can store it directly.
    """
    by_prog, by_name = _entries_index(entries)

    # source -> {prog_key: deepest rank that source gave the horse}
    ballots = {}
    key_to_entry = {}
    for e in entries:
        e = _to_entry(e)
        key = e.program_number if e.program_number else normalize_horse_name(e.horse_name)
        key_to_entry[key] = e
    for p in picks:
        src = p.get("source", "?")
        if src in NON_EXPERT_SOURCES:
            continue
        entry = _resolve_pick(p, by_prog, by_name)
        if entry is None:
            continue
        key = entry.program_number if entry.program_number else normalize_horse_name(entry.horse_name)
        rank = p.get("rank")
        if rank is None:
            rank = 4  # unranked mention = lukewarm, treat as deep
        # Keep the deepest (highest) rank a source assigned to this horse
        # only if the source mentioned it once; if mentioned twice, the
        # ballot slot that matters for "underneath" is the worst one.
        prev = ballots.get(src, {}).get(key)
        if prev is None or rank > prev:
            ballots.setdefault(src, {})[key] = rank

    n = len(ballots)
    if n < MIN_SOURCES:
        return []

    # Horses named on every ballot
    common = None
    for ranks in ballots.values():
        keys = set(ranks.keys())
        common = keys if common is None else (common & keys)
    if not common:
        return []

    def _score(key):
        entry = key_to_entry[key]
        avg_rank = mean(ballots[src][key] for src in ballots)
        mlo = entry.morning_line_odds if entry.morning_line_odds is not None else -1.0
        # Deepest average rank first; then price (higher MLO); then prog num
        return (-avg_rank, -mlo, entry.program_number or "")

    best_key = sorted(common, key=_score)[0]
    best = key_to_entry[best_key]
    return [{
        "source": SOURCE_NAME,
        "horse_name": best.horse_name,
        "program_number": best.program_number,
        "rank": 1,
        "comment": "",
    }]