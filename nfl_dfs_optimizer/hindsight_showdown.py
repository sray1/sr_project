"""Hindsight-optimal showdown lineup for any graded contest.

Uses the actual DK points recorded during grading (source_projections.
actual_fppg) as projections and runs the exact showdown MILP (1 CPT at
1.5x points/salary + 5 FLEX, $50,000 cap, max 5 per team) — the
highest-scoring legal lineup possible with hindsight. Players with no
recorded actual count 0.0 (scratch / no stats). The result is saved to
the hindsight_optimals table (a benchmark, not a prediction).

Run:  python hindsight_showdown.py --contest-id N --draft-group N
"""

import argparse

from accuracy_db import (get_connection, init_db,
                         save_hindsight_lineup)
from dk_client import fetch_draftables
from projections import normalize_dst_name, normalize_name
from showdown_optimizer import generate_showdown_lineups

MIN_SALARY = 300


def _is_dst(entry):
    return ('DST' in (entry.get('positions') or [])
            or 'DST' in (entry.get('name') or ''))


def main(contest_id, draft_group_id):
    conn = get_connection()
    actuals = {r['norm_name']: r['actual_fppg'] for r in conn.execute(
        "SELECT norm_name, actual_fppg FROM source_projections "
        "WHERE contest_id = ? AND actual_fppg IS NOT NULL", (contest_id,))}
    conn.close()
    print(f"{len(actuals)} players/DSTs with recorded actuals")

    draftables = fetch_draftables(draft_group_id)
    print(f"{len(draftables)} draftable entries")

    # Dedup: showdown slates list each player twice (CPT row at 1.5x
    # salary + UTIL row at base) — the MILP wants the base salary
    best_by_id = {}
    for d in draftables:
        if not d.get('player_id') or not d.get('salary'):
            continue
        if d['salary'] < MIN_SALARY:
            continue
        existing = best_by_id.get(d['player_id'])
        if existing is None or d['salary'] < existing['salary']:
            best_by_id[d['player_id']] = d

    pool = []
    for d in best_by_id.values():
        key = (normalize_dst_name if _is_dst(d) else normalize_name)(d['name'])
        entry = dict(d)
        entry['projection'] = actuals.get(key, 0.0)
        pool.append(entry)

    lineups = generate_showdown_lineups(pool, n_lineups=1)
    if not lineups:
        print("No feasible lineup")
        return None
    lineup = lineups[0]

    print(f"\nHINDSIGHT-OPTIMAL SHOWDOWN LINEUP — contest {contest_id}")
    print(f"{'=' * 70}")
    print(f"{lineup['total_projection']:.2f} actual DK points | "
          f"${lineup['total_salary']:,.0f} salary\n")
    captain = lineup['captain']
    print(f"  CPT  {captain['name']:<25} {captain['team']:<5} "
          f"${captain['salary'] * 1.5:>8,.0f}  "
          f"{captain['projection'] * 1.5:>6.2f}")
    for p in lineup['flex']:
        print(f"  FLEX {p['name']:<25} {p['team']:<5} "
              f"${p['salary']:>8,.0f}  {p['projection']:>6.2f}")

    init_db()
    save_hindsight_lineup(contest_id, 'showdown', lineup,
                          lineup['total_projection'])
    print(f"\nSaved hindsight optimal to nfl_accuracy.db "
          f"(contest {contest_id})")

    return lineup


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--contest-id', type=int, required=True)
    parser.add_argument('--draft-group', type=int, required=True)
    args = parser.parse_args()
    main(args.contest_id, args.draft_group)