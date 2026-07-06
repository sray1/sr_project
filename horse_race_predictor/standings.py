"""
Meet standings-as-of, computed from stored results (no look-ahead, no fetch).

Tally jockey/trainer wins at a track from every race in the DB with race_date
strictly BEFORE the as-of date. The winner (finish_position == 1) is matched to
its entry by normalized horse name to attribute the jockey/trainer.

Used by the leading_jockey / leading_trainer predictors (Tier 2). Because only
past races (by date) contribute, there is no look-ahead. Races early in the
window with no prior data at the track yield empty standings, in which case the
connection predictors abstain (return no picks) and are not scored for that race.
"""

from collections import defaultdict

import db
from race import normalize_horse_name


def meet_standings_as_of(track_code, as_of_date):
    """Return (jockey_wins, trainer_wins) dicts for races at `track_code` with
    race_date < as_of_date. Each maps name -> win count. O(prior races) - fine
    for one-off queries; for bulk use save_picks_for_races (running tally).
    """
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM races WHERE track_code = ? AND race_date < ? ORDER BY race_date",
        (track_code.upper(), as_of_date),
    )
    ids = [row["id"] for row in cur.fetchall()]
    conn.close()
    jockey_wins = defaultdict(int)
    trainer_wins = defaultdict(int)
    for rid in ids:
        wj, wt = winner_connections(rid)
        if wj:
            jockey_wins[wj] += 1
        if wt:
            trainer_wins[wt] += 1
    return dict(jockey_wins), dict(trainer_wins)


def seed_standings_before(track_code, before_date):
    """Same as meet_standings_as_of - (jockey_wins, trainer_wins) for races at
    `track_code` with race_date < before_date. Single pass over prior races;
    used to seed the running tally at the start of a track's window.
    """
    return meet_standings_as_of(track_code, before_date)


def winner_connections(race_id):
    """Return (winner_jockey, winner_trainer) for a race, or (None, None).

    Winner = finish_position == 1; matched to its entry by normalized horse name
    to attribute the jockey/trainer.
    """
    results = db.get_results(race_id)
    winner_name = None
    for r in results:
        if r.get("finish_position") == 1:
            winner_name = r.get("horse_name")
            break
    if not winner_name:
        return None, None
    wkey = normalize_horse_name(winner_name)
    for e in db.get_entries(race_id):
        if normalize_horse_name(e.get("horse_name") or "") == wkey:
            return (e.get("jockey") or None, e.get("trainer") or None)
    return None, None


def leader(wins):
    """Return (name, win_count) of the top by wins; tiebreak by name ascending.
    None if `wins` is empty.
    """
    if not wins:
        return None
    name, cnt = sorted(wins.items(), key=lambda kv: (-kv[1], (kv[0] or "").strip().lower()))[0]
    return name, cnt