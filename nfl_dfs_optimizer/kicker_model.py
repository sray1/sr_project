"""
Kicker projection model: game-environment-based kicker DK points.

Why this exists: no projection board ranks kickers well. Across the 14
kicker-games recorded in nfl_accuracy.db (weeks 1-2, 2026), DFF's own
kicker projections ran +2.4 over actual with 6.6 MAE, and kickers no board
lists fall to the salary curve (a $4,600 kicker gets ~11.2 points —
the curve is built for skill positions). Meanwhile a kicker was the
hindsight-optimal CAPTAIN in one of the first seven showdowns (Butker,
5 FGs) — kicker upside is a real part of the ceiling, so kickers deserve a
fair projection instead of an inflated salary-curve one.

Model (fitted on those 14 kicker-games):
    kicker = 7.36 + 0.016 * (team_offense_points - 88.7), clamped [3, 14]

team_offense_points is the sum of the kicker's OWN team's projected DK
points across QB/RB/WR/TE on the slate (the same projections the lineup
will be built from). The raw OLS fit was kicker = 2.91 + 0.050 * off_sum
with r = 0.315; because the environment signal is weak, the slope is
shrunk by the correlation (0.050 * 0.315 ~= 0.016) — the shrunken model
keeps most of the flat-mean's accuracy (MAE 3.82 vs 3.80 for a flat 7.36
and 3.98 for the raw fit, on the fit sample) while still tilting kickers
up in high-scoring environments and down in poor ones.

Manual CSV projections always win: a hand-entered kicker beats the model.
"""

KICKER_BASE = 7.36            # mean kicker DK points, fit sample (n=14)
KICKER_ENV_SLOPE = 0.016       # correlation-shrunk slope (0.0502 * r=0.315)
KICKER_ENV_CENTER = 88.7      # mean team offensive DK points, fit sample
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
    since both were less accurate than this model on the fit sample. The
    environment each kicker is projected from is the same dict's offense
    projections for his own team, so per-source runs (get_source_projections)
    get a per-source kicker model for free.

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