"""
Leading-jockey / leading-trainer predictors (Tier 2 compare-to-MLO baselines).

Bet the horse ridden (or trained) by the meet's winningest jockey / trainer AS
OF the race date - no look-ahead, no external fetch. These are the classic "bet
the meet leader" public handicapping angles, but evaluated rigorously: only
races with race_date strictly before this race contribute to the standings.

Two ways to supply the standings:
  - pass `wins=` (a precomputed {name: win_count} tally) for bulk runs - this is
    what save_picks_for_races does, maintaining a running tally per track (O(n));
  - or pass `track_code=` + `race_date=` and the predictor queries
    standings.meet_standings_as_of itself (O(prior); used by tests/backfill).

Abstention: if there is no prior data at the track (start of the window) or the
leader has no mount in this race, the predictor returns no picks and is not
scored for that race. The report's "Races" column for these predictors is
therefore the number of races where they actually had a basis to pick.

rank 1 = the leader's horse; ranks 2..top_n fill from the remaining field by
post position (consensus filler - only rank 1 drives the predictor's own
win/place/show accuracy).
"""

from collections import defaultdict

import standings

SOURCE_JOCKEY = "leading_jockey"
SOURCE_TRAINER = "leading_trainer"


def predict_leading_jockey(entries, track_code=None, race_date=None, top_n=3, wins=None):
    if wins is None:
        j, _ = standings.meet_standings_as_of(track_code, race_date)
        wins = j
    return _predict(entries, "jockey", SOURCE_JOCKEY, top_n, wins)


def predict_leading_trainer(entries, track_code=None, race_date=None, top_n=3, wins=None):
    if wins is None:
        _, t = standings.meet_standings_as_of(track_code, race_date)
        wins = t
    return _predict(entries, "trainer", SOURCE_TRAINER, top_n, wins)


def _predict(entries, role, source, top_n, wins):
    ld = standings.leader(wins)
    if not ld:
        return []  # abstain: no prior results at this track
    leader_name, leader_count = ld
    lname = leader_name.strip().lower()
    matches = [e for e in entries if (e.get(role) or "").strip().lower() == lname]
    if not matches:
        return []  # leader has no mount in this race -> abstain

    picks = [{
        "source": source,
        "horse_name": matches[0].get("horse_name"),
        "program_number": matches[0].get("program_number"),
        "rank": 1,
        "comment": f"leading {role}: {leader_name} ({leader_count} wins)",
    }]
    # Ranks 2..top_n: remaining entries by post position (consensus filler).
    match_progs = {m.get("program_number") for m in matches}
    others = [e for e in sorted(entries, key=lambda x: (x.get("post_position") or 9999))
              if e.get("program_number") not in match_progs]
    for i, e in enumerate(others[:max(0, top_n - 1)], 2):
        picks.append({
            "source": source,
            "horse_name": e.get("horse_name"),
            "program_number": e.get("program_number"),
            "rank": i,
            "comment": f"leading {role} undercard",
        })
    return picks


def save_picks_for_races(races):
    """Compute + save leading_jockey / leading_trainer picks for a list of race
    dicts {race_id, track, race_number, date} using a running standings tally per
    track - O(n) per track, no look-ahead, and same-date races are mutually
    excluded (a race doesn't see other races on its own date).

    Clears stale connection picks for races with no active field. Returns the
    count of non-empty pick lists saved.
    """
    import db
    from race import filter_active

    by_track = defaultdict(list)
    for r in races:
        by_track[r["track"]].append(r)
    saved = 0
    for track, trs in by_track.items():
        trs_sorted = sorted(trs, key=lambda x: (x["date"], x["race_number"]))
        first_date = trs_sorted[0]["date"]
        j_tally, t_tally = standings.seed_standings_before(track, first_date)
        by_date = defaultdict(list)
        for r in trs_sorted:
            by_date[r["date"]].append(r)
        for d in sorted(by_date):
            day = by_date[d]
            for r in day:
                entries = db.get_entries(r["race_id"])
                active, _ = filter_active(entries)
                if not active:
                    db.save_picks(r["race_id"], SOURCE_JOCKEY, [])
                    db.save_picks(r["race_id"], SOURCE_TRAINER, [])
                    continue
                for name, fn, tally in [(SOURCE_JOCKEY, predict_leading_jockey, j_tally),
                                         (SOURCE_TRAINER, predict_leading_trainer, t_tally)]:
                    picks = fn(active, wins=tally)
                    db.save_picks(r["race_id"], name, picks)
                    if picks:
                        saved += 1
            # After this date's races are picked, add its winners to the tallies
            # so later dates see them (same-date races never see each other).
            for r in day:
                wj, wt = standings.winner_connections(r["race_id"])
                if wj:
                    j_tally[wj] = j_tally.get(wj, 0) + 1
                if wt:
                    t_tally[wt] = t_tally.get(wt, 0) + 1
    return saved