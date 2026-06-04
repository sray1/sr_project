"""
Fetch NBA game results and calculate actual DK fantasy points.

Uses nba_api (free, no API key) to fetch box scores, then calculates
DK scoring using the existing DKScoringCalculator.
"""

from nba_api.stats.endpoints import boxscoretraditionalv2
from nba_api.stats.static import teams
from draftkings_scoring import DKScoringCalculator, PlayerStats
from itertools import combinations
from datetime import datetime, timezone
import time


# NBA team abbreviation mapping (DK abbreviations -> nba_api IDs)
NBA_TEAM_IDS = {
    "ATL": 1610612737, "BOS": 1610612738, "BKN": 1610612741, "CHA": 1610612766,
    "CHI": 1610612741, "CLE": 1610612739, "DAL": 1610612742, "DEN": 1610612743,
    "DET": 1610612765, "GSW": 1610612744, "HOU": 1610612745, "IND": 1610612754,
    "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763, "MIA": 1610612748,
    "MIL": 1610612749, "MIN": 1610612750, "NOP": 1610612740, "NYK": 1610612752,
    "OKC": 1610612760, "ORL": 1610612753, "PHI": 1610612755, "PHX": 1610612756,
    "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759, "TOR": 1610612761,
    "UTA": 1610612762, "WAS": 1610612764,
}

# Mapping of DK team abbreviations to nba_api abbreviations
# DK sometimes uses different abbreviations
DK_TO_NBA_ABBR = {
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA",
    "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
    "DET": "DET", "GSW": "GSW", "HOU": "HOU", "IND": "IND",
    "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX",
    "POR": "POR", "SAC": "SAC", "SAS": "SAS", "TOR": "TOR",
    "UTA": "UTA", "WAS": "WAS",
}


def fetch_box_score(game_id: str = None, date: str = None,
                   home_team: str = None, away_team: str = None,
                   max_retries: int = 3) -> dict:
    """
    Fetch NBA game box score and return player stats.

    Either provide a game_id directly, or provide date + team abbreviations
    to find the game. Returns a dict mapping player names to PlayerStats.

    Args:
        game_id: NBA game ID (e.g., '0022400001')
        date: Game date in 'YYYY-MM-DD' format
        home_team: Home team abbreviation (DK format, e.g., 'SAS')
        away_team: Away team abbreviation (DK format, e.g., 'NYK')
        max_retries: Number of retries on API failure

    Returns:
        dict: {player_name: {"stats": PlayerStats, "team": team_abbr, "minutes": float}}
    """
    from nba_api.stats.endpoints import scoreboardv2
    from nba_api.stats.library.parameters import LeagueID

    if game_id is None and (date is None or home_team is None):
        raise ValueError("Must provide either game_id or (date + home_team)")

    # If no game_id, find it from date and teams
    if game_id is None:
        home_id = NBA_TEAM_IDS.get(home_team)
        away_id = NBA_TEAM_IDS.get(away_team)

        if home_id is None:
            # Try alternate abbreviation mapping
            nba_abbr = DK_TO_NBA_ABBR.get(home_team)
            if nba_abbr:
                home_id = NBA_TEAM_IDS.get(nba_abbr)
        if away_id is None:
            nba_abbr = DK_TO_NBA_ABBR.get(away_team)
            if nba_abbr:
                away_id = NBA_TEAM_IDS.get(nba_abbr)

        # Get scoreboard for the date to find the game
        for attempt in range(max_retries):
            try:
                sb = scoreboardv2.ScoreboardV2(
                    game_date=date,
                    league_id=LeagueID.nba
                )
                games = sb.get_data_frames()[0]

                for _, game in games.iterrows():
                    game_home_id = game['HOME_TEAM_ID']
                    game_away_id = game['VISITOR_TEAM_ID']
                    if game_home_id == home_id or game_away_id == away_id:
                        game_id = game['GAME_ID']
                        break

                if game_id is None:
                    raise ValueError(f"No game found for {away_team} @ {home_team} on {date}")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    raise

    # Fetch the box score
    for attempt in range(max_retries):
        try:
            box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
            player_stats_df = box.get_data_frames()[0]  # Player stats

            results = {}
            for _, row in player_stats_df.iterrows():
                if row.get('MIN') is None or row['MIN'] == '' or row['MIN'] == '0':
                    continue

                player_name = row['PLAYER_NAME']
                team_abbr = row['TEAM_ABBREVIATION']

                # Parse minutes
                try:
                    minutes_str = str(row['MIN'])
                    if ':' in minutes_str:
                        mins, secs = minutes_str.split(':')
                        minutes = float(mins) + float(secs) / 60
                    else:
                        minutes = float(minutes_str)
                except (ValueError, AttributeError):
                    minutes = 0.0

                # Parse stats
                try:
                    pts = float(row.get('PTS', 0) or 0)
                    reb = float(row.get('REB', 0) or 0)
                    ast = float(row.get('AST', 0) or 0)
                    stl = float(row.get('STL', 0) or 0)
                    blk = float(row.get('BLK', 0) or 0)
                    tov = float(row.get('TOV', 0) or 0)
                    fgm = float(row.get('FGM', 0) or 0)
                    fga = float(row.get('FGA', 0) or 0)
                    fg3m = float(row.get('FG3M', 0) or 0)
                    fg3a = float(row.get('FG3A', 0) or 0)
                    ftm = float(row.get('FTM', 0) or 0)
                    fta = float(row.get('FTA', 0) or 0)
                except (ValueError, TypeError):
                    continue

                stats = PlayerStats(
                    points=pts,
                    rebounds=reb,
                    assists=ast,
                    steals=stl,
                    blocks=blk,
                    turnovers=tov,
                    three_pointers=fg3m,
                )

                results[player_name] = {
                    "stats": stats,
                    "team": team_abbr,
                    "minutes": minutes,
                    "field_goals_made": fgm,
                    "field_goals_attempted": fga,
                    "three_pointers_made": fg3m,
                    "three_pointers_attempted": fg3a,
                    "free_throws_made": ftm,
                    "free_throws_attempted": fta,
                }

            return results

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise ValueError(f"Failed to fetch box score after {max_retries} retries: {e}")

    return {}


def calculate_actual_dk_points(player_name: str, actual_data: dict,
                               is_captain: bool = False) -> float:
    """Calculate actual DK fantasy points from game stats."""
    calc = DKScoringCalculator()
    entry = actual_data.get(player_name)
    if entry is None:
        return 0.0

    base = calc.calculate_fantasy_points(entry["stats"])
    if is_captain:
        return base * 1.5
    return base


def find_best_possible_lineup(player_scores: dict, salary_cap: float = 50000,
                              min_salary: float = 3000, top_n: int = 5) -> list:
    """
    Find the best possible showdown lineup from actual game results.

    Searches all captain candidates (top players by actual fppg)
    and selects the 5 best utility players within the salary cap.

    Args:
        player_scores: Dict of {name: {"fppg": float, "salary": float, "team": str}}
        salary_cap: Maximum total salary (with captain 1.5x multiplier)
        min_salary: Minimum salary for utility players
        top_n: Number of best lineups to return

    Returns:
        List of lineup dicts sorted by total actual fppg (descending)
    """
    # Sort players by actual fppg
    sorted_players = sorted(
        [(name, data) for name, data in player_scores.items() if data["fppg"] > 0 and data["salary"] >= min_salary],
        key=lambda x: x[1]["fppg"] / (x[1]["salary"] / 1000),
        reverse=True
    )

    if len(sorted_players) < 6:
        return []

    best_lineups = []

    # Try each of the top 15 by value as captain
    captain_candidates = sorted_players[:15]

    for captain_name, captain_data in captain_candidates:
        captain_salary_cap = captain_data["salary"] * 1.5
        remaining_cap = salary_cap - captain_salary_cap
        captain_fppg = captain_data["fppg"] * 1.5  # captain multiplier

        # Get utility candidates (exclude captain, salary >= min_salary)
        util_candidates = [
            (name, data) for name, data in sorted_players
            if name != captain_name and data["salary"] >= min_salary
        ]

        # Greedy: pick best value utilities that fit
        selected = []
        used_salary = 0
        for name, data in util_candidates:
            if len(selected) >= 5:
                break
            if used_salary + data["salary"] <= remaining_cap:
                selected.append((name, data))
                used_salary += data["salary"]

        # If we didn't fill 5 spots, try cheapest first
        if len(selected) < 5:
            by_salary = sorted(util_candidates, key=lambda x: x[1]["salary"])
            for name, data in by_salary:
                if name in [s[0] for s in selected]:
                    continue
                if len(selected) >= 5:
                    break
                if used_salary + data["salary"] <= remaining_cap:
                    selected.append((name, data))
                    used_salary += data["salary"]

        if len(selected) == 5:
            total_fppg = captain_fppg + sum(d["fppg"] for _, d in selected)
            total_salary = captain_salary_cap + sum(d["salary"] for _, d in selected)

            lineup = {
                "captain": captain_name,
                "captain_salary": captain_data["salary"],
                "captain_fppg": captain_data["fppg"],
                "captain_actual_fppg": captain_data["fppg"],
                "utility": [(n, d) for n, d in selected],
                "total_fppg": total_fppg,
                "total_salary": total_salary,
            }
            best_lineups.append(lineup)

    # Sort by total actual fppg descending
    best_lineups.sort(key=lambda x: x["total_fppg"], reverse=True)
    return best_lineups[:top_n]


def get_game_id_for_contest(contest_name: str) -> tuple:
    """
    Parse team abbreviations from a DK contest name like
    'NBA Showdown $1M ... (NYK @ SAS)'.

    Returns (away_abbr, home_abbr) or raises ValueError.
    """
    import re
    match = re.search(r'\(([A-Z]{3})\s*@\s*([A-Z]{3})\)', contest_name)
    if match:
        return match.group(1), match.group(2)
    raise ValueError(f"Could not parse teams from contest name: {contest_name}")


def get_date_for_contest(starts_at) -> str:
    """Convert a datetime to YYYY-MM-DD string for API queries."""
    if hasattr(starts_at, 'strftime'):
        return starts_at.strftime('%Y-%m-%d')
    return str(starts_at)[:10]