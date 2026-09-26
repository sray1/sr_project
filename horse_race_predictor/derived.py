"""
Derived pick sources for Horse Race Predictor.

Recomputes the sources derived from expert ballots for a stored race:
  - consensus: consensus.aggregate over expert picks only (baselines and
    other derived sources are excluded from the aggregation, matching the
    manual-flow convention: expert sentiment only).
  - common_underneath: the deepest horse common to all expert ballots
    (see common_underneath.py); saved only when it plays.

save_derived(race_id) is the single code path for both, so the manual
record flow and `predictor.py derive` stay in sync, and historical races
can be backfilled from picks that predate their results (no look-ahead).

Consensus is only re-saved when an expert best pick resolves (a race
whose expert picks all fail to match entries keeps whatever consensus
row is already on record rather than being wiped).
"""

import db
import common_underneath as cu
import consensus as consensus_mod


def save_derived(race_id):
    """Recompute + save consensus and common-underneath picks for a race.

    Returns a dict:
      {race_id, num_experts, consensus: best_pick row or None,
       common_underneath: pick list (0 or 1 picks)}
    """
    entries = db.get_entries(race_id)
    picks = db.get_picks(race_id)
    expert = [p for p in picks if p.get("source") not in cu.NON_EXPERT_SOURCES]
    result = {
        "race_id": race_id,
        "num_experts": len({p["source"] for p in expert}),
        "consensus": None,
        "common_underneath": [],
    }
    if not expert:
        return result

    agg = consensus_mod.aggregate(entries, expert)
    if agg["best_pick"]:
        b = agg["best_pick"]
        db.save_picks(race_id, "consensus", [{
            "source": "consensus",
            "horse_name": b["horse_name"],
            "program_number": b["program_number"],
            "rank": 1,
            "comment": "",
        }])
        result["consensus"] = b

    underneath = cu.predict(entries, picks)  # filters non-experts internally
    if underneath:
        db.save_picks(race_id, cu.SOURCE_NAME, underneath)
        result["common_underneath"] = underneath
    return result