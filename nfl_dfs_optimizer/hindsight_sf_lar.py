"""Hindsight-optimal showdown lineup for contest 195390889 (SF @ LAR).

Replaces every projection with the player's actual DK points (ESPN box
score) and re-runs the exact showdown MILP -> the best lineup that was
possible with perfect information.
"""
import dk_client
import game_results
from projections import normalize_dst_name, normalize_name
from showdown_optimizer import generate_showdown_lineups

DRAFT_GROUP_ID = 153072
SLATE_DATE = '20260910'

draftables = dk_client.fetch_draftables(DRAFT_GROUP_ID)
actuals = game_results.fetch_slate_results(SLATE_DATE, ['SF @ LAR'])
print(f"{len(draftables)} draftables, {len(actuals)} actual rows")

pool = []
missing = []
seen = {}  # player_id -> pool entry; DK lists each player twice (CPT row
# at 1.5x salary + UTIL row at base) — the MILP wants the base salary
for d in draftables:
    prev = seen.get(d['player_id'])
    if prev is not None:
        if d['salary'] < prev['salary']:
            pool.remove(prev)  # first row was the CPT duplicate (1.5x)
        else:
            continue  # already have the cheaper (base) row
    pos = (d.get('position') or '').upper()
    name = d.get('name') or ''
    key = normalize_dst_name(name) if pos == 'DST' else normalize_name(name)
    actual = actuals.get(key, {}).get('fppg')
    if actual is None:
        missing.append(f"{name} ({pos})")
        actual = 0.0
    entry = {
        'player_id': d['player_id'],
        'name': name,
        'team': d.get('team'),
        'position': pos,
        'positions': [pos],
        'salary': d['salary'],
        'projection': actual,
    }
    seen[d['player_id']] = entry
    pool.append(entry)

print(f"No actual (scratch/DNP): {len(missing)}")
for m in missing:
    print(f"  {m}")

lineup = generate_showdown_lineups(pool, n_lineups=1)[0]
cap = lineup['captain']
print()
print(f"HINDSIGHT OPTIMAL: {lineup['total_projection']:.2f} pts, "
      f"${lineup['total_salary']:,.0f} salary")
print(f"  CPT  {cap['name']:24s} {cap['projection']:5.2f} x1.5 = "
      f"{cap['projection'] * 1.5:5.2f}  (${cap['salary'] * 1.5:,.0f})")
for p in lineup['flex']:
    print(f"  FLEX {p['name']:24s} {p['projection']:5.2f}"
          f"          (${p['salary']:,.0f})")