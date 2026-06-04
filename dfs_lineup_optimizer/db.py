"""
SQLite database for tracking DFS prediction results over time.

Stores games, player performances (projected vs actual), and lineups
(predicted and best-possible) so accuracy can be measured across contests.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone

# Database file path (same directory as this module)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dfs_results.db")


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
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            away_team TEXT NOT NULL,
            home_team TEXT NOT NULL,
            contest_name TEXT,
            contest_type TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_performances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            team TEXT NOT NULL,
            salary INTEGER NOT NULL,
            projected_fppg REAL NOT NULL,
            actual_fppg REAL,
            is_starter INTEGER DEFAULT 0,
            stats_json TEXT,
            FOREIGN KEY (game_id) REFERENCES games(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lineups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            lineup_type TEXT NOT NULL,
            lineup_rank INTEGER NOT NULL,
            captain_name TEXT NOT NULL,
            captain_salary INTEGER NOT NULL,
            captain_projected REAL NOT NULL,
            captain_actual REAL,
            total_projected REAL NOT NULL,
            total_actual REAL,
            total_salary INTEGER NOT NULL,
            utility_json TEXT NOT NULL,
            FOREIGN KEY (game_id) REFERENCES games(id)
        )
    """)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def save_game(date, away_team, home_team, contest_name=None, contest_type=None):
    """
    Save a game record. Returns the game_id.

    If a game with the same date/teams already exists, returns that game's id
    instead of creating a duplicate.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Check for existing game
    cursor.execute(
        "SELECT id FROM games WHERE date = ? AND away_team = ? AND home_team = ?",
        (date, away_team, home_team)
    )
    row = cursor.fetchone()
    if row:
        game_id = row["id"]
        # Update contest info if provided
        if contest_name or contest_type:
            cursor.execute(
                "UPDATE games SET contest_name = COALESCE(?, contest_name), "
                "contest_type = COALESCE(?, contest_type) WHERE id = ?",
                (contest_name, contest_type, game_id)
            )
            conn.commit()
        conn.close()
        return game_id

    cursor.execute(
        "INSERT INTO games (date, away_team, home_team, contest_name, contest_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (date, away_team, home_team, contest_name, contest_type,
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    game_id = cursor.lastrowid
    conn.close()
    return game_id


def save_player_performance(game_id, player_name, team, salary, projected_fppg,
                           actual_fppg=None, is_starter=False, stats=None):
    """
    Save a player's performance for a game.

    If the player already exists for this game, updates their actual_fppg and stats.
    """
    conn = get_connection()
    cursor = conn.cursor()

    is_starter_int = 1 if is_starter else 0
    stats_json = json.dumps(stats) if stats else None

    # Check for existing record
    cursor.execute(
        "SELECT id FROM player_performances WHERE game_id = ? AND player_name = ?",
        (game_id, player_name)
    )
    row = cursor.fetchone()

    if row:
        # Update actual stats if provided
        if actual_fppg is not None:
            cursor.execute(
                "UPDATE player_performances SET actual_fppg = ?, stats_json = COALESCE(?, stats_json) "
                "WHERE id = ?",
                (actual_fppg, stats_json, row["id"])
            )
        conn.commit()
        result = row["id"]
    else:
        cursor.execute(
            "INSERT INTO player_performances "
            "(game_id, player_name, team, salary, projected_fppg, actual_fppg, is_starter, stats_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (game_id, player_name, team, salary, projected_fppg, actual_fppg,
             is_starter_int, stats_json)
        )
        conn.commit()
        result = cursor.lastrowid

    conn.close()
    return result


def save_lineup(game_id, lineup_type, rank, captain_name, captain_salary,
                captain_projected, captain_actual=None, total_projected=None,
                total_actual=None, total_salary=None, utility_players=None):
    """
    Save a lineup for a game.

    Args:
        game_id: Game ID from save_game()
        lineup_type: "predicted" or "best_possible"
        rank: Lineup rank order (1 = best)
        captain_name: Captain player name
        captain_salary: Captain base salary (before 1.5x multiplier)
        captain_projected: Captain projected fppg (with 1.5x)
        captain_actual: Captain actual fppg (with 1.5x), null if game not played
        total_projected: Total projected lineup fppg
        total_actual: Total actual lineup fppg, null if game not played
        total_salary: Total lineup salary (with captain 1.5x)
        utility_players: List of dicts [{name, salary, projected, actual}]
    """
    conn = get_connection()
    cursor = conn.cursor()

    utility_json = json.dumps(utility_players or [])

    cursor.execute(
        "INSERT INTO lineups "
        "(game_id, lineup_type, lineup_rank, captain_name, captain_salary, "
        "captain_projected, captain_actual, total_projected, total_actual, "
        "total_salary, utility_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (game_id, lineup_type, rank, captain_name, captain_salary,
         captain_projected, captain_actual, total_projected, total_actual,
         total_salary, utility_json)
    )
    conn.commit()
    lineup_id = cursor.lastrowid
    conn.close()
    return lineup_id


def get_game_history(limit=20):
    """
    Get recent games with summary stats.

    Returns list of dicts with game info, number of players tracked,
    and best lineup accuracy.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT g.id, g.date, g.away_team, g.home_team, g.contest_name, g.contest_type,
               COUNT(DISTINCT pp.id) as player_count,
               COUNT(DISTINCT l.id) as lineup_count
        FROM games g
        LEFT JOIN player_performances pp ON g.id = pp.game_id
        LEFT JOIN lineups l ON g.id = l.game_id
        GROUP BY g.id
        ORDER BY g.date DESC, g.created_at DESC
        LIMIT ?
    """, (limit,))

    games = []
    for row in cursor.fetchall():
        game = dict(row)

        # Get best predicted and best possible lineups
        cursor.execute(
            "SELECT total_projected, total_actual FROM lineups "
            "WHERE game_id = ? AND lineup_type = 'predicted' ORDER BY lineup_rank LIMIT 1",
            (game["id"],)
        )
        pred = cursor.fetchone()
        game["best_predicted_projected"] = pred["total_projected"] if pred else None
        game["best_predicted_actual"] = pred["total_actual"] if pred else None

        cursor.execute(
            "SELECT total_projected, total_actual FROM lineups "
            "WHERE game_id = ? AND lineup_type = 'best_possible' ORDER BY lineup_rank LIMIT 1",
            (game["id"],)
        )
        best = cursor.fetchone()
        game["best_possible_actual"] = best["total_actual"] if best else None

        # Calculate accuracy
        if game["best_predicted_actual"] and game["best_possible_actual"]:
            game["efficiency_pct"] = round(
                game["best_predicted_actual"] / game["best_possible_actual"] * 100, 1
            )
        else:
            game["efficiency_pct"] = None

        games.append(game)

    conn.close()
    return games


def get_game_details(game_id):
    """
    Get full details for a specific game: all player performances and lineups.

    Returns dict with game info, players list, and lineups list.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Game info
    cursor.execute("SELECT * FROM games WHERE id = ?", (game_id,))
    game = dict(cursor.fetchone())

    # Player performances
    cursor.execute(
        "SELECT * FROM player_performances WHERE game_id = ? ORDER BY projected_fppg DESC",
        (game_id,)
    )
    players = [dict(row) for row in cursor.fetchall()]

    # Parse stats_json for each player
    for p in players:
        if p["stats_json"]:
            p["stats"] = json.loads(p["stats_json"])
        else:
            p["stats"] = None

    # Lineups
    cursor.execute(
        "SELECT * FROM lineups WHERE game_id = ? ORDER BY lineup_type, lineup_rank",
        (game_id,)
    )
    lineups = []
    for row in cursor.fetchall():
        lineup = dict(row)
        lineup["utility"] = json.loads(lineup["utility_json"])
        del lineup["utility_json"]
        lineups.append(lineup)

    conn.close()
    return {"game": game, "players": players, "lineups": lineups}


def get_accuracy_summary():
    """
    Get aggregate accuracy stats across all tracked games.

    Returns dict with overall projection accuracy metrics.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Overall player projection accuracy
    cursor.execute("""
        SELECT COUNT(*) as count,
               AVG(actual_fppg - projected_fppg) as avg_diff,
               AVG(ABS(actual_fppg - projected_fppg)) as avg_abs_diff,
               AVG(ABS(actual_fppg - projected_fppg) / NULLIF(projected_fppg, 0) * 100) as avg_pct_error
        FROM player_performances
        WHERE actual_fppg IS NOT NULL AND projected_fppg > 0
    """)
    player_stats = dict(cursor.fetchone())

    # Lineup accuracy
    cursor.execute("""
        SELECT
            SUM(CASE WHEN lineup_type = 'predicted' THEN 1 ELSE 0 END) as predicted_count,
            SUM(CASE WHEN lineup_type = 'best_possible' THEN 1 ELSE 0 END) as best_possible_count,
            AVG(CASE WHEN lineup_type = 'predicted' AND total_actual IS NOT NULL
                THEN total_actual END) as avg_predicted_actual,
            AVG(CASE WHEN lineup_type = 'best_possible' AND total_actual IS NOT NULL
                THEN total_actual END) as avg_best_possible_actual
        FROM lineups
        WHERE total_actual IS NOT NULL
    """)
    lineup_stats = dict(cursor.fetchone())

    # Per-game efficiency
    cursor.execute("""
        SELECT g.id, g.date, g.away_team, g.home_team,
               pred.total_actual as predicted_actual,
               best.total_actual as best_possible_actual,
               ROUND(pred.total_actual * 100.0 / best.total_actual, 1) as efficiency_pct
        FROM games g
        JOIN lineups pred ON g.id = pred.game_id AND pred.lineup_type = 'predicted' AND pred.lineup_rank = 1
        JOIN lineups best ON g.id = best.game_id AND best.lineup_type = 'best_possible' AND best.lineup_rank = 1
        WHERE pred.total_actual IS NOT NULL AND best.total_actual IS NOT NULL
        ORDER BY g.date DESC
    """)
    game_efficiency = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {
        "player_stats": player_stats,
        "lineup_stats": lineup_stats,
        "game_efficiency": game_efficiency,
    }


def get_player_history(player_name):
    """
    Get projection accuracy history for a specific player across all games.

    Returns list of dicts with game date, projected fppg, actual fppg, and diff.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT pp.*, g.date, g.away_team, g.home_team
        FROM player_performances pp
        JOIN games g ON pp.game_id = g.id
        WHERE pp.player_name = ?
        ORDER BY g.date DESC
    """, (player_name,))

    results = []
    for row in cursor.fetchall():
        entry = dict(row)
        if entry["actual_fppg"] is not None and entry["projected_fppg"] > 0:
            entry["diff"] = entry["actual_fppg"] - entry["projected_fppg"]
            entry["pct_diff"] = round(entry["diff"] / entry["projected_fppg"] * 100, 1)
        else:
            entry["diff"] = None
            entry["pct_diff"] = None
        results.append(entry)

    conn.close()
    return results


def display_history(limit=20):
    """Display recent game tracking history."""
    games = get_game_history(limit)

    if not games:
        print("\nNo tracked games found. Run prediction_tracker.py after a game to start tracking.")
        return

    print("=" * 80)
    print("DFS PREDICTION TRACKING HISTORY")
    print("=" * 80)

    for game in games:
        date_str = game["date"]
        matchup = f"{game['away_team']} @ {game['home_team']}"
        contest_type = game.get("contest_type", "N/A")

        print(f"\n  {date_str}  {matchup}  ({contest_type})")
        print(f"    Players tracked: {game['player_count']}")
        print(f"    Lineups: {game['lineup_count']}")

        if game["best_predicted_actual"] is not None:
            print(f"    Best predicted lineup: {game['best_predicted_actual']:.1f} fppg (projected: {game['best_predicted_projected']:.1f})")

        if game["best_possible_actual"] is not None:
            print(f"    Best possible lineup: {game['best_possible_actual']:.1f} fppg")

        if game["efficiency_pct"] is not None:
            print(f"    Lineup efficiency: {game['efficiency_pct']}%")


def display_accuracy_summary():
    """Display aggregate accuracy summary across all tracked games."""
    summary = get_accuracy_summary()

    ps = summary["player_stats"]
    ls = summary["lineup_stats"]

    print("\n" + "=" * 80)
    print("PROJECTION ACCURACY SUMMARY")
    print("=" * 80)

    if ps["count"] and ps["count"] > 0:
        print(f"\n  Player Projections ({ps['count']} data points):")
        print(f"    Average diff (actual - projected): {ps['avg_diff']:+.1f} fppg")
        print(f"    Average absolute error: {ps['avg_abs_diff']:.1f} fppg")
        print(f"    Average % error: {ps['avg_pct_error']:.1f}%")
    else:
        print("\n  No player data with actual results yet.")

    if ls.get("predicted_count") and ls["predicted_count"] > 0:
        print(f"\n  Lineup Performance ({ls['predicted_count']} predicted lineups):")
        if ls["avg_predicted_actual"]:
            print(f"    Average predicted lineup actual: {ls['avg_predicted_actual']:.1f} fppg")
        if ls["avg_best_possible_actual"]:
            print(f"    Average best possible lineup: {ls['avg_best_possible_actual']:.1f} fppg")
    else:
        print("\n  No lineup data with actual results yet.")

    game_eff = summary["game_efficiency"]
    if game_eff:
        print(f"\n  Per-Game Efficiency:")
        for ge in game_eff:
            matchup = f"{ge['away_team']} @ {ge['home_team']}"
            print(f"    {ge['date']}  {matchup}: "
                  f"predicted {ge['predicted_actual']:.1f} / "
                  f"best {ge['best_possible_actual']:.1f} = "
                  f"{ge['efficiency_pct']}%")


def display_player_history(player_name):
    """Display projection accuracy history for a specific player."""
    history = get_player_history(player_name)

    if not history:
        print(f"\nNo tracking data found for '{player_name}'.")
        print("Player names are case-sensitive. Check spelling against tracked data.")
        return

    print(f"\n{'=' * 80}")
    print(f"PROJECTION HISTORY: {player_name}")
    print(f"{'=' * 80}")

    print(f"\n  {'Date':<12} {'Matchup':<15} {'Salary':>8} {'Projected':>10} {'Actual':>10} {'Diff':>8} {'%':>7}")
    print(f"  {'-'*12} {'-'*15} {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*7}")

    for entry in history:
        matchup = f"{entry['away_team']} @ {entry['home_team']}"
        proj = entry["projected_fppg"]
        actual = entry["actual_fppg"] if entry["actual_fppg"] is not None else 0
        diff = entry["diff"] if entry["diff"] is not None else 0
        pct = entry["pct_diff"] if entry["pct_diff"] is not None else 0

        actual_str = f"{actual:.1f}" if entry["actual_fppg"] is not None else "N/A"
        diff_str = f"{diff:+.1f}" if entry["diff"] is not None else "N/A"
        pct_str = f"{pct:+.1f}%" if entry["pct_diff"] is not None else "N/A"

        print(f"  {entry['date']:<12} {matchup:<15} ${entry['salary']:>7,} {proj:>10.1f} {actual_str:>10} {diff_str:>8} {pct_str:>7}")

    # Summary stats
    entries_with_actual = [e for e in history if e["actual_fppg"] is not None and e["projected_fppg"] > 0]
    if entries_with_actual:
        avg_diff = sum(e["diff"] for e in entries_with_actual) / len(entries_with_actual)
        avg_pct = sum(e["pct_diff"] for e in entries_with_actual) / len(entries_with_actual)
        print(f"\n  Average across {len(entries_with_actual)} games: {avg_diff:+.1f} fppg ({avg_pct:+.1f}%)")


if __name__ == "__main__":
    init_db()
    print("Database initialized. Run prediction_tracker.py to populate data.")