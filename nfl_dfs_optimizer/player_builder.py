"""
Player pool construction for the NFL DFS optimizer.

Adapted from dfs_lineup_optimizer/player_builder.py:
- Deduplicates CPT/UTIL entries (showdown slates list each player twice,
  CPT at 1.5x salary; we keep the base-salary entry)
- Skips disabled players, DK-status OUT/IR players, and sub-minimum
  salaries
- Drops backup QBs (DK draftables carry no depth-chart flag, so the salary
  gap within a team's QBs is the starter signal) and any manually excluded
  players
- Attaches projections resolved by projections.get_player_projections
- Builds pydfs Player objects for the classic optimizer, plain dicts for
  the showdown MILP
"""

from pydfs_lineup_optimizer.player import Player as PyDFSPlayer, GameInfo

from projections import (get_dff_out_names, get_dff_team_mismatches,
                         normalize_dst_name, normalize_name)

MIN_SALARY = 300  # DK NFL minimum salary ($300)


def filter_backup_qbs(pool):
    """Drop backup QBs: keep only each team's top-salaried QB (ties kept).

    DK draftables have no starter/backup flag, but DK prices the expected
    starter well above the backups (e.g. NE @ SEA: Maye $10,000 vs DeVito
    $8,000 / Morton $6,000). Showdown has no position constraints, so
    without this filter the MILP happily stacks a team's backup QBs.

    Returns:
        (kept, dropped) pool entries
    """
    max_qb_salary = {}
    for entry in pool:
        if 'QB' in (entry.get('positions') or []):
            team = entry.get('team')
            max_qb_salary[team] = max(max_qb_salary.get(team, 0),
                                      entry.get('salary') or 0)

    kept, dropped = [], []
    for entry in pool:
        is_backup_qb = ('QB' in (entry.get('positions') or [])
                        and (entry.get('salary') or 0)
                        < max_qb_salary.get(entry.get('team'), 0))
        (dropped if is_backup_qb else kept).append(entry)
    return kept, dropped


def filter_unprojected_wrs(pool):
    """Drop WRs no projection source lists (practice squad / WR4+ / injured).

    The main pipeline's fallback curve is a salary line — it happily gives
    an $8.30 projection to a practice-squad signing DK still lists on the
    slate (Kyrese Rowan, signed to the Saints' PS 9 days before week 1,
    was picked at $3,000 over real WRs). Projection boards (DFF, CSV,
    FantasyPros, BlueCollar) cover genuine WR3-or-better roles; injury
    risers get projected once injuries push them up the depth chart, so
    this filter keeps them. Applied to WRs only, and never to the
    per-source comparison builds (the fallback 'source' projects every
    WR by construction).

    Returns:
        (kept, dropped) pool entries
    """
    kept, dropped = [], []
    for entry in pool:
        is_deep_wr = ('WR' in (entry.get('positions') or [])
                      and entry.get('source') == 'fallback')
        (dropped if is_deep_wr else kept).append(entry)
    return kept, dropped


def _looks_like_dst(name):
    lowered = (name or '').lower()
    return 'dst' in lowered or 'defense' in lowered


def _exclusion_keys(name):
    """Normalization forms of a name for exclusion matching.

    Player names match on their full normalized form only; DST entries
    additionally match on the team-token form ('Patriots DST' ->
    'patriots'). Last-name tokenization is deliberately NOT used:
    league-wide injury reports contain e.g. 'Sincere Brown' (LAC), which
    must never exclude 'A.J. Brown' (NE) from a slate.
    """
    keys = {normalize_name(name)}
    if _looks_like_dst(name):
        keys.add(normalize_dst_name(name))
    return keys


def exclude_named_players(pool, exclude):
    """Drop players matching the exclusion list.

    Args:
        exclude: One of
            - a comma-separated string of names (manual --exclude):
              "Tommy DeVito, Seahawks DST"
            - a list of names
            - a list of (name, team) tuples — team-qualified entries from
              the DFF injury report: the name must match AND the team must
              match (players with the same name exist across teams)

    Returns:
        (kept, dropped) pool entries
    """
    if isinstance(exclude, str):
        exclude = [n.strip() for n in exclude.split(',') if n.strip()]

    entries = []
    for item in exclude or []:
        if isinstance(item, (tuple, list)) and len(item) == 2 and item[1]:
            entries.append((item[0], str(item[1]).strip().upper()))
        else:
            entries.append((item, None))

    kept, dropped = [], []
    for player in pool:
        player_keys = _exclusion_keys(player['name'])
        player_team = (player.get('team') or '').strip().upper()
        hit = False
        for name, team in entries:
            if not _exclusion_keys(name) & player_keys:
                continue
            if team and team != player_team:
                continue  # same name on a different team
            hit = True
            break
        (dropped if hit else kept).append(player)
    return kept, dropped


def merge_exclusions(manual_exclude, out_entries):
    """Combine manual --exclude names with auto-detected OUT players.

    Args:
        manual_exclude: --exclude value (comma-separated string or list)
            or None
        out_entries: (name, team) tuples from get_dff_out_names()

    Returns:
        List of exclusion entries — bare names and (name, team) tuples
    """
    merged = []
    if isinstance(manual_exclude, str):
        merged += [n.strip() for n in manual_exclude.split(',') if n.strip()]
    elif manual_exclude:
        merged += list(manual_exclude)
    for entry in out_entries or []:
        if entry not in merged:
            merged.append(entry)
    return merged


def build_auto_exclusions(players, manual_exclude=None, allow_scrape=True):
    """Combine every pool-level exclusion into one list.

    Layers (all flow through exclude_named_players, so OUT/mismatch entries
    are team-qualified while manual names match every team):
        1. manual --exclude names, if any
        2. DFF injury report: players listed OUT/IR/SUSP
        3. Traded players: DK slate entries DFF now lists under a different
           team (DK draftables lag roster moves — the player cannot appear
           in the slate's games)

    Args:
        players: Normalized DK draftable dicts (name, team, positions) — the
            team-mismatch layer needs the slate's own team assignments
        manual_exclude: --exclude value (comma-separated string or list)
        allow_scrape: Whether DFF may be scraped if no cache exists yet

    Returns:
        List of exclusion entries for build_player_pool(exclude=...)
    """
    merged = merge_exclusions(
        manual_exclude, get_dff_out_names(allow_scrape=allow_scrape))
    for entry in get_dff_team_mismatches(players, allow_scrape=allow_scrape):
        if entry not in merged:
            merged.append(entry)
    return merged


def build_player_pool(draftables, player_projections, min_salary=MIN_SALARY,
                     drop_backup_qbs=True, exclude=None, verbose=True,
                     drop_unprojected_wrs=False):
    """Build the deduplicated, projected player pool.

    Args:
        draftables: Normalized player dicts from dk_client.fetch_draftables
        player_projections: {player_id: {'projection': float, 'source': str}}
            from projections.get_player_projections
        min_salary: Minimum salary to include (default $300, DK NFL minimum)
        drop_backup_qbs: Keep only each team's top-salaried QB (default
            True — DK salaries flag the starter)
        exclude: Players to drop from every lineup — names (list or one
            comma-separated string) for manual exclusions, or (name, team)
            tuples for team-qualified injury exclusions (see
            exclude_named_players)
        verbose: Print what the filters dropped (disable for repeated
            per-source pool builds so the note prints once)
        drop_unprojected_wrs: Drop WRs whose projection is salary-fallback
            (no projection source lists them: practice squad / WR4+ / not
            on the fantasy radar). Default False — the per-source
            comparison builds must NOT use it (the fallback 'source'
            would drop every WR); the main pipeline passes True.

    Returns:
        List of player dicts with:
            player_id, name, position, positions, salary, team, game,
            game_start, projection, source
    """
    # Keep the lowest-salary entry per player_id (drops the 1.5x CPT variant)
    best_by_id = {}
    out_ir_names = []
    for player in draftables:
        if player.get('is_disabled'):
            continue
        # DK's own injury status ('OUT'/'IR'; 'Q'/'D' stay in) — flags
        # OUT/IR players days before is_disabled flips pre-lock
        if (player.get('status') or '').strip().upper() in ('OUT', 'IR'):
            out_ir_names.append(player['name'])
            continue
        if not player['salary'] or player['salary'] < min_salary:
            continue
        if not player['name'] or not player['player_id']:
            continue

        existing = best_by_id.get(player['player_id'])
        if existing is None or player['salary'] < existing['salary']:
            best_by_id[player['player_id']] = player

    if out_ir_names and verbose:
        unique = sorted({n for n in out_ir_names if n})
        shown = ', '.join(unique[:5]) + (', ...' if len(unique) > 5 else '')
        print(f"DK OUT/IR status filter: dropped {len(unique)} players "
              f"({shown})")

    pool = []
    for player_id, player in best_by_id.items():
        proj_info = player_projections.get(player_id, {})
        entry = dict(player)
        entry['projection'] = proj_info.get('projection', 0.0)
        entry['source'] = proj_info.get('source', 'fallback')
        pool.append(entry)

    if exclude:
        pool, excluded = exclude_named_players(pool, exclude)
        if excluded and verbose:
            names = ', '.join(p['name'] for p in excluded)
            print(f"Excluded {len(excluded)} players: {names}")

    if drop_backup_qbs:
        pool, backup_qbs = filter_backup_qbs(pool)
        if backup_qbs and verbose:
            names = ', '.join(f"{p['name']} ({p['team']})" for p in backup_qbs)
            print(f"Backup QB filter: dropped {len(backup_qbs)} "
                  f"({names}) - kept each team's top-salaried QB")

    if drop_unprojected_wrs:
        pool, deep_wrs = filter_unprojected_wrs(pool)
        if deep_wrs and verbose:
            print(f"Deep-WR filter: dropped {len(deep_wrs)} fallback-only WRs "
                  f"(practice squad / WR4+ / not projected by any source)")

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