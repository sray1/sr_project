"""
Kicker projection model: a fair flat prior for kicker DK points.

Why this exists: no projection board ranks kickers well. Across the 42
kicker-games recorded in nfl_accuracy.db (weeks 1-7, 2026, after the
3+ FGs-made bonus was removed from kicker scoring), DFF's own kicker
projections run +3.4 over actual with 5.2 MAE, and kickers no board
lists fall to the salary curve (a $4,600 kicker gets ~11.2 points —
the curve is built for skill positions). Kickers still earn
hindsight-optimal lineup spots (both kickers made the BAL @ ATL
hindsight optimum), so kickers deserve a fair projection instead of an
inflated salary-curve one.

Model (refit on the corrected 42-kicker-game history):
    kicker = 7.52, clamped [3, 14]

The original fit (14 kicker-games, weeks 1-2) found a game-environment
tilt — kicker = 7.36 + 0.016 * (team offense projected - 88.7), r =
+0.32 — but it did not survive more data: at n=42 the correlation
between a kicker's actual points and his team's projected offensive
total is r = -0.13 (raw OLS slope -0.05). The flat mean is also the
best predictor on the full sample (MAE 3.58 vs 3.59 for a tiny shrunk
tilt and 5.16 for DFF's board), so the tilt is gone and the model is
an honest flat prior. team_offense_points is still accepted (and still
computed per source in apply_kicker_model) so the interface is
unchanged; a future refit that finds a real signal can restore it.

Manual CSV projections always win: a hand-entered kicker beats the model.
"""

KICKER_BASE = 7.52            # mean kicker DK points, corrected history (n=42)
KICKER_ENV_SLOPE = 0.0         # refit found no environment signal (r=-0.13)
KICKER_ENV_CENTER = 100.3     # mean team offensive DK points (inert at slope 0)
KICKER_MIN = 3.0
KICKER_MAX = 14.0

OFFENSE_POSITIONS = {'QB', 'RB', 'WR', 'TE'}


def estimate_kicker_projection(team_offense_points):
    """Project one kicker's DK points from his team's offensive total.

    Args:
        team_offense_points: Sum of the kicker's team's projected DK points
            across QB/RB/WR/TE on the slate (0 when the slate lists none —
            unknown environment, slightly below the mean)

    Returns:
        float projected DK points, clamped to [KICKER_MIN, KICKER_MAX]
    """
    projection = (KICKER_BASE
                  + KICKER_ENV_SLOPE * (team_offense_points - KICKER_ENV_CENTER))
    return round(min(KICKER_MAX, max(KICKER_MIN, projection)), 2)


def apply_kicker_model(players, attached):
    """Replace kicker projections in an attached-projection dict.

    Overrides every kicker row EXCEPT one matched by the manual CSV (the
    priority override) — board and salary-curve kicker projections alike,
    since both were less accurate than this model on the corrected
    history. The environment is still computed from the same dict's
    offense projections for the kicker's own team (per-source runs keep
    their per-source environment for free), though the refit made the
    model flat — a future tilt-restoring refit changes nothing here.

    Args:
        players: Player dicts (player_id, position, team) — the same list
                  the attached dict was resolved from
        attached: {player_id: {'projection': float, 'source': str}} —
                  MUTATED in place; kicker rows are relabeled
                  source='kicker_model'

    Returns:
        Number of kicker projections replaced
    """
    team_offense = {}
    for player in players:
        if (player.get('position') or '') not in OFFENSE_POSITIONS:
            continue
        info = attached.get(player['player_id'])
        if info is not None:
            team = player.get('team')
            team_offense[team] = team_offense.get(team, 0.0) + info['projection']

    replaced = 0
    for player in players:
        if (player.get('position') or '') != 'K':
            continue
        info = attached.get(player['player_id'])
        if info is None or info.get('source') == 'csv':
            continue  # csv rows missing (shouldn't happen) or manual override
        info['projection'] = estimate_kicker_projection(
            team_offense.get(player.get('team'), 0.0))
        info['source'] = 'kicker_model'
        replaced += 1
    return replaced