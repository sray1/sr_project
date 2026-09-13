"""Hindsight-optimal lineup for the week-1 classic contest (195580486).

Uses the actual DK points recorded during grading (source_projections.
actual_fppg, from ESPN box scores) as projections and runs the exact
classic optimizer with no stack rule — the highest-scoring legal $50,000
lineup possible with hindsight, to measure every saved prediction
against. Players with no recorded actual count 0.0 (scratch / no stats).

Run:  python hindsight_classic_week1.py
"""

from accuracy_db import get_connection
from classic_optimizer import generate_classic_lineups, lineup_to_dict
from dk_client import fetch_draftables
from player_builder import build_pydfs_players
from projections import normalize_dst_name, normalize_name

CONTEST_ID = 195580486
DRAFT_GROUP_ID = 151307
MIN_SALARY = 300


def _is_dst(entry):
    return ('DST' in (entry.get('positions') or [])
            or 'DST' in (entry.get('name') or ''))


def main():
    conn = get_connection()
    actuals = {r['norm_name']: r['actual_fppg'] for r in conn.execute(
        "SELECT norm_name, actual_fppg FROM source_projections "
        "WHERE contest_id = ? AND actual_fppg IS NOT NULL", (CONTEST_ID,))}
    conn.close()
    print(f"{len(actuals)} players/DSTs with recorded actuals")

    draftables = fetch_draftables(DRAFT_GROUP_ID)
    print(f"{len(draftables)} draftable entries")

    # Dedup: classic slates double-list players (FLEX slot rows) — keep the
    # lowest-salary entry per player_id (same rule as build_player_pool)
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

    pydfs_players = build_pydfs_players(pool)
    lineups = generate_classic_lineups(pydfs_players, n_lineups=1,
                                       stack_rule='none')
    if not lineups:
        print("No feasible lineup")
        return
    lineup = lineup_to_dict(lineups[0])

    print(f"\nHINDSIGHT-OPTIMAL CLASSIC LINEUP — contest {CONTEST_ID}")
    print(f"{'=' * 70}")
    print(f"{lineup['total_projection']:.2f} actual DK points | "
          f"${lineup['total_salary']:,.0f} salary\n")
    for p in lineup['players']:
        print(f"  {p['lineup_position']:<5} {p['name']:<25} {p['team']:<5} "
              f"${p['salary']:>8,.0f}  {p['projection']:>6.2f}")

    # Persist the benchmark (own table — a ceiling, not a prediction)
    from accuracy_db import init_db, save_hindsight_lineup
    init_db()
    save_hindsight_lineup(CONTEST_ID, 'classic', lineup,
                          lineup['total_projection'])
    print(f"\nSaved hindsight optimal to nfl_accuracy.db "
          f"(contest {CONTEST_ID})")

    return lineup


if __name__ == "__main__":
    main()