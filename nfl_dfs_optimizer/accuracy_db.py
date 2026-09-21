"""
SQLite database for tracking NFL DFS projection-source accuracy over time.

Mirrors dfs_lineup_optimizer/db.py (the NBA tracker): stores contests, the
per-source player projections that were available pre-game (projected vs
actual DK points), and each source's optimal lineup — so accuracy can be
measured across contests AND across projection sources (DailyFantasyFuel,
BlueCollarDFS, CSV, salary fallback).

Schema:
- contests           one row per DK contest snapshot (unique on contest_id)
- source_projections one row per (contest, source, player): projected points
                    at save time, actual points filled after the game
- lineup_predictions one row per (contest, source): the optimal lineup built
                    from that source's projections, total filled post-game.
                    Expert lineups published without a projection total are
                    stored with total_projected = 0.0 — a sentinel meaning
                    "actual only"; summary error stats exclude them.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

from projections import normalize_name, normalize_dst_name

# Database file path (same directory as this module)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "nfl_accuracy.db")


def get_connection():
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create database tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contests (
            contest_id INTEGER PRIMARY KEY,
            draft_group_id INTEGER,
            name TEXT NOT NULL,
            mode TEXT NOT NULL,
            starts_at TEXT,
            slate_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS source_projections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contest_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            player_id TEXT NOT NULL,
            player_name TEXT NOT NULL,
            norm_name TEXT NOT NULL,
            team TEXT,
            position TEXT,
            salary INTEGER,
            projected_fppg REAL NOT NULL,
            matched INTEGER NOT NULL DEFAULT 0,
            actual_fppg REAL,
            stats_json TEXT,
            UNIQUE (contest_id, source, player_id),
            FOREIGN KEY (contest_id) REFERENCES contests(contest_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lineup_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contest_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            mode TEXT NOT NULL,
            lineup_rank INTEGER NOT NULL DEFAULT 1,
            captain_name TEXT,
            players_json TEXT NOT NULL,
            total_projected REAL NOT NULL,
            total_salary INTEGER NOT NULL,
            total_actual REAL,
            UNIQUE (contest_id, source, lineup_rank),
            FOREIGN KEY (contest_id) REFERENCES contests(contest_id)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_projections_name
            ON source_projections (contest_id, norm_name)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hindsight_optimals (
            contest_id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            players_json TEXT NOT NULL,
            total_actual REAL NOT NULL,
            total_salary INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_contest(contest_id, draft_group_id, name, mode, slate_games,
                 starts_at=None):
    """Insert or refresh a contest row. Returns nothing (idempotent).

    Args:
        slate_games: List of game strings ("NE @ SEA") from the draftables,
                     used by --score to fetch actual results per game.
        starts_at: Contest start time (ISO string or None) — used by --score
                   to find the games on the ESPN scoreboard for that date.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO contests (contest_id, draft_group_id, name, mode,
                              starts_at, slate_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(contest_id) DO UPDATE SET
            draft_group_id = excluded.draft_group_id,
            name = excluded.name,
            mode = excluded.mode,
            starts_at = excluded.starts_at,
            slate_json = excluded.slate_json
    """, (contest_id, draft_group_id, name, mode,
          (starts_at if isinstance(starts_at, str)
           else starts_at.isoformat()) if starts_at else None,
          json.dumps(sorted(slate_games)),
          datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def get_contest(contest_id):
    """Fetch one contest row, or None."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM contests WHERE contest_id = ?",
                       (contest_id,)).fetchone()
    conn.close()
    return row


def list_contests():
    """All saved contests, oldest first, with scoring status."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM source_projections sp
                WHERE sp.contest_id = c.contest_id) AS n_projections,
               (SELECT COUNT(*) FROM source_projections sp
                WHERE sp.contest_id = c.contest_id
                  AND sp.actual_fppg IS NOT NULL) AS n_scored
        FROM contests c ORDER BY c.created_at
    """).fetchall()
    conn.close()
    return rows


def save_source_projection(contest_id, source, player):
    """Insert or update one (contest, source, player) projection row.

    Args:
        player: Pool entry dict with player_id, name, team, position,
                salary, projection, source — where player['source'] is the
                matched-vs-fallback label ('matched' stored as 1 when it
                equals the source dict key).
    """
    # 'matched' = this source actually projected the player (as opposed to
    # the row being a salary-fallback value). The pure-fallback source never
    # matches anything.
    matched = 1 if (source != 'fallback' and player.get('source') == source) else 0
    if 'DST' in (player.get('positions') or []):
        norm_name = normalize_dst_name(player['name'])
    else:
        norm_name = normalize_name(player['name'])
    conn = get_connection()
    conn.execute("""
        INSERT INTO source_projections (contest_id, source, player_id,
            player_name, norm_name, team, position, salary, projected_fppg,
            matched)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(contest_id, source, player_id) DO UPDATE SET
            player_name = excluded.player_name,
            team = excluded.team,
            salary = excluded.salary,
            projected_fppg = excluded.projected_fppg,
            matched = excluded.matched
    """, (contest_id, source, str(player['player_id']), player['name'],
          norm_name, player.get('team'),
          player.get('position'), player.get('salary'),
          player['projection'], matched))
    conn.commit()
    conn.close()


def save_lineup_prediction(contest_id, source, mode, lineup, lineup_rank=1):
    """Insert or update one source's optimal lineup for a contest.

    Args:
        lineup: Showdown lineup dict (captain/flex) or classic lineup_to_dict
                output (players); converted to a storage-friendly players list
                with captain flags and player ids where available.
    """
    players = []
    if mode == 'showdown':
        captain = lineup['captain']
        players.append({'name': captain['name'],
                        'player_id': captain['player_id'],
                        'is_captain': True,
                        'salary': captain['salary']})
        for p in lineup['flex']:
            players.append({'name': p['name'], 'player_id': p['player_id'],
                            'is_captain': False, 'salary': p['salary']})
        captain_name = captain['name']
    else:
        for p in lineup['players']:
            players.append({'name': p['name'], 'player_id': None,
                            'is_captain': False, 'salary': p['salary']})
        captain_name = None

    conn = get_connection()
    conn.execute("""
        INSERT INTO lineup_predictions (contest_id, source, mode, lineup_rank,
            captain_name, players_json, total_projected, total_salary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(contest_id, source, lineup_rank) DO UPDATE SET
            captain_name = excluded.captain_name,
            players_json = excluded.players_json,
            total_projected = excluded.total_projected,
            total_salary = excluded.total_salary
    """, (contest_id, source, mode, lineup_rank, captain_name,
          json.dumps(players), lineup['total_projection'],
          lineup['total_salary']))
    conn.commit()
    conn.close()


def save_hindsight_lineup(contest_id, mode, lineup, total_actual):
    """Store the hindsight-optimal lineup for a contest (its benchmark).

    Kept in its own table, NOT lineup_predictions: it's what every
    prediction is measured against, not a prediction itself (adding it as
    a source would pollute --summary's per-source accuracy). Idempotent
    per contest — recomputation replaces the row.

    Args:
        contest_id: Contest the lineup was optimal for
        mode: 'showdown' or 'classic'
        lineup: lineup dict from the optimizer whose per-player
            'projection' values ARE the actual DK points — classic
            lineup_to_dict shape ('players') or showdown shape
            ('captain' + 'flex'; the captain's stored actual is its BASE
            points, the 1.5x is applied at read time like lineups)
        total_actual: The lineup's total actual points (== the optimizer
            objective when computed from graded actuals; includes the
            1.5x captain multiplier for showdown)
    """
    if 'captain' in lineup:
        captain = lineup['captain']
        players = [{'name': captain['name'], 'salary': captain['salary'],
                    'actual': captain.get('projection'), 'is_captain': True}]
        players += [{'name': p['name'], 'salary': p['salary'],
                     'actual': p.get('projection'), 'is_captain': False}
                    for p in lineup['flex']]
    else:
        players = [{'name': p['name'], 'salary': p['salary'],
                    'actual': p.get('projection'), 'is_captain': False}
                   for p in lineup['players']]
    computed_at = datetime.now().isoformat()
    conn = get_connection()
    conn.execute("""
        INSERT INTO hindsight_optimals (contest_id, mode, players_json,
            total_actual, total_salary, computed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(contest_id) DO UPDATE SET
            mode = excluded.mode,
            players_json = excluded.players_json,
            total_actual = excluded.total_actual,
            total_salary = excluded.total_salary,
            computed_at = excluded.computed_at
    """, (contest_id, mode, json.dumps(players), total_actual,
          lineup['total_salary'], computed_at))
    conn.commit()
    conn.close()


def get_hindsight_lineup(contest_id):
    """Fetch the stored hindsight-optimal lineup for a contest (or None)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM hindsight_optimals WHERE contest_id = ?",
        (contest_id,)).fetchone()
    conn.close()
    if not row:
        return None
    row = dict(row)
    row['players'] = json.loads(row.pop('players_json'))
    return row


def record_player_actuals(contest_id, player_scores):
    """Fill actual DK points for players, across every source's rows.

    Args:
        player_scores: Dict {norm_name: {'fppg': float, 'stats': dict}} —
                       keys are normalized names (from game_results.py)

    Returns:
        Number of source_projections rows updated.
    """
    conn = get_connection()
    cursor = conn.cursor()
    updated = 0
    for norm_name, data in player_scores.items():
        stats_json = json.dumps(data['stats']) if data.get('stats') else None
        cursor.execute("""
            UPDATE source_projections
            SET actual_fppg = ?, stats_json = COALESCE(?, stats_json)
            WHERE contest_id = ? AND norm_name = ?
        """, (data['fppg'], stats_json, contest_id, norm_name))
        updated += cursor.rowcount
    conn.commit()
    conn.close()
    return updated


def get_lineup_players(contest_id, source, lineup_rank=1):
    """Fetch the stored players list for one lineup prediction."""
    conn = get_connection()
    row = conn.execute("""
        SELECT players_json, mode FROM lineup_predictions
        WHERE contest_id = ? AND source = ? AND lineup_rank = ?
    """, (contest_id, source, lineup_rank)).fetchone()
    conn.close()
    if not row:
        return None, None
    return json.loads(row['players_json']), row['mode']


def update_lineup_actual(contest_id, source, total_actual, lineup_rank=1):
    """Store the actual DK points a lineup scored."""
    conn = get_connection()
    conn.execute("""
        UPDATE lineup_predictions SET total_actual = ?
        WHERE contest_id = ? AND source = ? AND lineup_rank = ?
    """, (total_actual, contest_id, source, lineup_rank))
    conn.commit()
    conn.close()


def contest_accuracy(contest_id):
    """Per-source accuracy for one scored contest.

    Returns:
        List of dicts {source, lineup_projected, lineup_actual,
        projected_known, player_mae, n_players} sorted by |error| ascending.
        projected_known is False for expert lineups stored with the
        total_projected=0.0 sentinel (no stated projection).
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT lp.source,
               lp.total_projected AS lineup_projected,
               lp.total_actual AS lineup_actual
        FROM lineup_predictions lp
        WHERE lp.contest_id = ? AND lp.total_actual IS NOT NULL
        ORDER BY lp.source
    """, (contest_id,)).fetchall()

    result = []
    for row in rows:
        mae_row = conn.execute("""
            SELECT AVG(ABS(projected_fppg - actual_fppg)) AS mae,
                   COUNT(*) AS n
            FROM source_projections
            WHERE contest_id = ? AND source = ?
              AND actual_fppg IS NOT NULL AND matched = 1
        """, (contest_id, row['source'])).fetchone()
        result.append({
            'source': row['source'],
            'lineup_projected': row['lineup_projected'],
            'lineup_actual': row['lineup_actual'],
            'projected_known': bool(row['lineup_projected']),
            'player_mae': mae_row['mae'],
            'n_players': mae_row['n'],
        })
    conn.close()
    return result


def display_history():
    """Print every saved contest with its scoring status."""
    contests = list_contests()
    if not contests:
        print("No contests saved yet — run prediction_tracker.py --save")
        return

    print(f"\n{'=' * 70}")
    print(f"SAVED CONTESTS ({len(contests)})")
    print(f"{'=' * 70}")
    for c in contests:
        scored = 'scored' if c['n_scored'] > 0 else 'NOT scored'
        print(f"  [{c['contest_id']}] {c['name']}")
        print(f"      mode={c['mode']}  projections={c['n_projections']} "
              f"({c['n_scored']} scored)  [{scored}]")


def display_accuracy_summary():
    """Print cumulative per-source accuracy across all scored contests."""
    conn = get_connection()

    # Lineup-level accuracy (how each source's optimal lineup projected vs scored).
    # The total_projected = 0.0 sentinel (expert lineups published without a
    # projection total) has no error to measure — they're reported separately.
    lineup_rows = conn.execute("""
        SELECT source,
               COUNT(*) AS n,
               AVG(total_projected) AS avg_proj,
               AVG(total_actual) AS avg_actual,
               AVG(ABS(total_projected - total_actual)) AS lineup_mae
        FROM lineup_predictions
        WHERE total_actual IS NOT NULL AND total_projected > 0
        GROUP BY source ORDER BY source
    """).fetchall()
    expert_rows = conn.execute("""
        SELECT source,
               COUNT(*) AS n,
               AVG(total_actual) AS avg_actual
        FROM lineup_predictions
        WHERE total_actual IS NOT NULL AND total_projected = 0
        GROUP BY source ORDER BY source
    """).fetchall()

    # Player-level accuracy (only players the source actually matched)
    player_rows = conn.execute("""
        SELECT source,
               COUNT(*) AS n,
               AVG(ABS(projected_fppg - actual_fppg)) AS player_mae,
               AVG(projected_fppg - actual_fppg) AS bias
        FROM source_projections
        WHERE actual_fppg IS NOT NULL AND matched = 1
        GROUP BY source ORDER BY source
    """).fetchall()
    conn.close()

    if not lineup_rows and not player_rows and not expert_rows:
        print("No scored contests yet — run prediction_tracker.py --score "
              "after games finish")
        return

    print(f"\n{'=' * 70}")
    print("ACCURACY SUMMARY (per projection source)")
    print(f"{'=' * 70}")

    if lineup_rows:
        print("\nLINEUP LEVEL (each source's optimal lineup, projected vs actual):")
        print(f"  {'source':<22} {'n':>4} {'avg proj':>9} {'avg act':>9} {'MAE':>7}")
        for r in lineup_rows:
            print(f"  {r['source']:<22} {r['n']:>4} {r['avg_proj']:>9.2f} "
                  f"{r['avg_actual']:>9.2f} {r['lineup_mae']:>7.2f}")

    if expert_rows:
        print("\nEXPERT LINEUPS (published lineups, actual only —")
        print("no stated projection, so no error to measure):")
        print(f"  {'source':<22} {'n':>4} {'avg actual':>11}")
        for r in expert_rows:
            print(f"  {r['source']:<22} {r['n']:>4} {r['avg_actual']:>11.2f}")

    if player_rows:
        print("\nPLAYER LEVEL (matched players only, projected vs actual):")
        print(f"  {'source':<22} {'n':>5} {'MAE':>7} {'bias':>7}")
        for r in player_rows:
            print(f"  {r['source']:<22} {r['n']:>5} {r['player_mae']:>7.2f} "
                  f"{r['bias']:>+7.2f}")
        print("  (bias = avg(projected - actual): positive = overconfident)")