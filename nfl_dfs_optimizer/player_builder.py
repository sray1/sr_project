"""
Player pool construction for the NFL DFS optimizer.

Adapted from dfs_lineup_optimizer/player_builder.py:
- Deduplicates CPT/UTIL entries (showdown slates list each player twice,
  CPT at 1.5x salary; we keep the base-salary entry)
- Skips disabled players and sub-minimum salaries
- Attaches projections resolved by projections.get_player_projections
- Builds pydfs Player objects for the classic optimizer, plain dicts for
  the showdown MILP
"""

from pydfs_lineup_optimizer.player import Player as PyDFSPlayer, GameInfo

MIN_SALARY = 300  # DK NFL minimum salary ($300)


def build_player_pool(draftables, player_projections, min_salary=MIN_SALARY):
    """Build the deduplicated, projected player pool.

    Args:
        draftables: Normalized player dicts from dk_client.fetch_draftables
        player_projections: {player_id: {'projection': float, 'source': str}}
            from projections.get_player_projections
        min_salary: Minimum salary to include (default $300, DK NFL minimum)

    Returns:
        List of player dicts with:
            player_id, name, position, positions, salary, team, game,
            game_start, projection, source
    """
    # Keep the lowest-salary entry per player_id (drops the 1.5x CPT variant)
    best_by_id = {}
    for player in draftables:
        if player['is_disabled']:
            continue
        if not player['salary'] or player['salary'] < min_salary:
            continue
        if not player['name'] or not player['player_id']:
            continue

        existing = best_by_id.get(player['player_id'])
        if existing is None or player['salary'] < existing['salary']:
            best_by_id[player['player_id']] = player

    pool = []
    for player_id, player in best_by_id.items():
        proj_info = player_projections.get(player_id, {})
        entry = dict(player)
        entry['projection'] = proj_info.get('projection', 0.0)
        entry['source'] = proj_info.get('source', 'fallback')
        pool.append(entry)

    return pool


def build_pydfs_players(pool):
    """Convert the player pool into pydfs Player objects for the classic optimizer.

    pydfs needs positions lists (e.g. ['WR'] or ['QB', 'FLEX'] as drafted),
    team abbreviations, and a GameInfo for stacking (GameStack needs
    home/away teams per player).

    Args:
        pool: Player pool from build_player_pool

    Returns:
        List of pydfs_lineup_optimizer.player.Player
    """
    players = []
    game_info_cache = {}  # pydfs's MinGamesRule groups by GameInfo identity:
    # every player in the same game must share ONE GameInfo instance
    for entry in pool:
        full_name = entry['name']
        # pydfs wants first/last split; DST entries are team names
        parts = full_name.split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''

        positions = [p for p in entry['positions'] if p]
        if not positions:
            continue

        # Parse game into home/away teams: "KC @ BAL" or "KC vs. BAL"
        home_team = None
        away_team = None
        game_name = entry.get('game') or ''
        if '@' in game_name:
            away_team, home_team = [t.strip() for t in game_name.split('@', 1)]
        elif ' vs' in game_name.lower():
            home_team, away_team = [t.strip() for t in
                                    game_name.lower().split(' vs', 1)]

        game_key = (home_team, away_team, entry.get('game_start'))
        if game_key not in game_info_cache:
            game_info_cache[game_key] = GameInfo(
                home_team=home_team,
                away_team=away_team,
                starts_at=entry.get('game_start'),
            )
        game_info = game_info_cache[game_key]

        players.append(PyDFSPlayer(
            player_id=str(entry['player_id']),
            first_name=first_name,
            last_name=last_name,
            positions=positions,
            team=entry['team'] or '',
            salary=float(entry['salary']),
            fppg=float(entry['projection']),
            game_info=game_info,
        ))

    return players