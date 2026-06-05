"""
Fetch NBA game results and calculate actual DK fantasy points.

Uses nba_api (free, no API key) to fetch box scores, then calculates
DK scoring using the existing DKScoringCalculator.
"""

from draftkings_scoring import DKScoringCalculator, PlayerStats
from utils import SALARY_CAP
from itertools import combinations
from datetime import datetime, timezone
import time


# NBA team abbreviation mapping (DK abbreviations -> nba_api IDs)
NBA_TEAM_IDS = {
    "ATL": 1610612737, "BOS": 1610612738, "BKN": 1610612751, "CHA": 1610612766,
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

# Full name mapping: nba_api nameI format -> common full name
NBA_NAME_MAP = {
    "V. Wembanyama": "Victor Wembanyama",
    "S. Castle": "Stephon Castle",
    "D. Fox": "De'Aaron Fox",
    "D. Vassell": "Devin Vassell",
    "J. Champagnie": "Julian Champagnie",
    "D. Harper": "Dylan Harper",
    "K. Johnson": "Keldon Johnson",
    "L. Kornet": "Luke Kornet",
    "H. Barnes": "Harrison Barnes",
    "C. Bryant": "Carter Bryant",
    "J. Hart": "Josh Hart",
    "O. Anunoby": "OG Anunoby",
    "K. Towns": "Karl-Anthony Towns",
    "M. Bridges": "Mikal Bridges",
    "J. Brunson": "Jalen Brunson",
    "L. Shamet": "Landry Shamet",
    "M. McBride": "Miles McBride",
    "M. Robinson": "Mitchell Robinson",
    "J. Alvarado": "Jose Alvarado",
    "J. Clarkson": "Jordan Clarkson",
    "A. Hukporti": "Ariel Hukporti",
    "P. Dadiet": "Pacome Dadiet",
    "M. Diawara": "Mohamed Diawara",
    "T. Kolek": "Tyler Kolek",
    "J. Sochan": "Jeremy Sochan",
    "B. Biyombo": "Bismack Biyombo",
    "J. McLaughlin": "Jordan McLaughlin",
    "K. Olynyk": "Kelly Olynyk",
    "M. Plumlee": "Mason Plumlee",
}


def _normalize_player_name(short_name, team_abbr=None):
    """Normalize an NBA player name from nba_api format to full name.

    First checks the explicit NBA_NAME_MAP, then tries matching against
    NBA rotation data by first initial + last name.

    Args:
        short_name: Player name in nba_api format (e.g., "V. Wembanyama")
        team_abbr: Optional team abbreviation for rotation lookup

    Returns:
        Full player name if found, otherwise the original short_name
    """
    # Check explicit map first
    if short_name in NBA_NAME_MAP:
        return NBA_NAME_MAP[short_name]

    # Try matching against rotation data by initial + last name
    from nba_rotations import NBA_ROTATIONS

    if '.' in short_name:
        # Parse "V. Wembanyama" -> initial "V", last name "Wembanyama"
        parts = short_name.strip().split('.', 1)
        if len(parts) == 2:
            initial = parts[0].strip()
            last = parts[1].strip()
            # Search all team rotations
            teams_to_search = [NBA_ROTATIONS[team_abbr]] if team_abbr and team_abbr in NBA_ROTATIONS else NBA_ROTATIONS.values()
            for team_data in teams_to_search:
                for name in team_data.get("starting", []) + team_data.get("rotation", []):
                    name_parts = name.split()
                    if len(name_parts) >= 2:
                        # Match on first initial + last name
                        if name_parts[0][0] == initial and name_parts[-1].lower() == last.lower():
                            return name

    return short_name


def _find_game_id(date, home_team, away_team, max_retries=3):
    """Find NBA game ID from date and team abbreviations."""
    from nba_api.stats.library.parameters import LeagueID

    home_id = NBA_TEAM_IDS.get(home_team)
    away_id = NBA_TEAM_IDS.get(away_team)

    if home_id is None:
        nba_abbr = DK_TO_NBA_ABBR.get(home_team)
        if nba_abbr:
            home_id = NBA_TEAM_IDS.get(nba_abbr)
    if away_id is None:
        nba_abbr = DK_TO_NBA_ABBR.get(away_team)
        if nba_abbr:
            away_id = NBA_TEAM_IDS.get(nba_abbr)

    for attempt in range(max_retries):
        try:
            # Try V3 first (recommended for 2025-26+ seasons)
            try:
                from nba_api.stats.endpoints import scoreboardv3
                sb = scoreboardv3.ScoreboardV3(
                    game_date=date,
                    league_id=LeagueID.nba
                )
                games_df = sb.get_data_frames()[0]

                # V3 returns game info in a different format - need to find game IDs
                # Get the scoreboard header which has game IDs
                # Fallback to V2 for game ID lookup
            except Exception:
                pass

            # Use V2 for game ID lookup (still works for this)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                from nba_api.stats.endpoints import scoreboardv2
                sb = scoreboardv2.ScoreboardV2(
                    game_date=date,
                    league_id=LeagueID.nba
                )
                games = sb.get_data_frames()[0]

                for _, game in games.iterrows():
                    game_home_id = game['HOME_TEAM_ID']
                    game_away_id = game['VISITOR_TEAM_ID']
                    if game_home_id == home_id or game_away_id == away_id:
                        return game['GAME_ID']

            raise ValueError(f"No game found for {away_team} @ {home_team} on {date}")

        except ValueError:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                raise

    return None


def fetch_box_score(game_id: str = None, date: str = None,
                   home_team: str = None, away_team: str = None,
                   max_retries: int = 3) -> dict:
    """
    Fetch NBA game box score and return player stats.

    Either provide a game_id directly, or provide date + team abbreviations
    to find the game. Returns a dict mapping player names to PlayerStats.

    Args:
        game_id: NBA game ID (e.g., '0042500401')
        date: Game date in 'YYYY-MM-DD' format
        home_team: Home team abbreviation (DK format, e.g., 'SAS')
        away_team: Away team abbreviation (DK format, e.g., 'NYK')
        max_retries: Number of retries on API failure

    Returns:
        dict: {player_name: {"stats": PlayerStats, "team": team_abbr, "minutes": float}}
    """
    if game_id is None and (date is None or home_team is None):
        raise ValueError("Must provide either game_id or (date + home_team)")

    # If no game_id, find it from date and teams
    if game_id is None:
        game_id = _find_game_id(date, home_team, away_team, max_retries)

    # Fetch the box score using V3 (V2 is deprecated for 2025-26 season)
    for attempt in range(max_retries):
        try:
            from nba_api.stats.endpoints import boxscoretraditionalv3
            box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
            player_stats_df = box.get_data_frames()[0]

            results = {}
            for _, row in player_stats_df.iterrows():
                # Skip players who didn't play
                minutes = row.get('minutes', '')
                if minutes is None or minutes == '' or str(row.get('comment', '')).startswith('DNP'):
                    continue

                # Parse minutes
                try:
                    minutes_str = str(minutes)
                    if ':' in minutes_str:
                        mins, secs = minutes_str.split(':')
                        minutes_float = float(mins) + float(secs) / 60
                    else:
                        minutes_float = float(minutes_str)
                except (ValueError, AttributeError):
                    minutes_float = 0.0

                # Get player name - use nameI from nba_api, map to full name
                short_name = row.get('nameI', '')
                player_name = _normalize_player_name(short_name, row.get('teamTricode', ''))
                team_abbr = row.get('teamTricode', '')

                # Parse stats
                try:
                    pts = float(row.get('points', 0) or 0)
                    reb = float(row.get('reboundsTotal', 0) or 0)
                    ast = float(row.get('assists', 0) or 0)
                    stl = float(row.get('steals', 0) or 0)
                    blk = float(row.get('blocks', 0) or 0)
                    tov = float(row.get('turnovers', 0) or 0)
                    fgm = float(row.get('fieldGoalsMade', 0) or 0)
                    fga = float(row.get('fieldGoalsAttempted', 0) or 0)
                    fg3m = float(row.get('threePointersMade', 0) or 0)
                    fg3a = float(row.get('threePointersAttempted', 0) or 0)
                    ftm = float(row.get('freeThrowsMade', 0) or 0)
                    fta = float(row.get('freeThrowsAttempted', 0) or 0)
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
                    "minutes": minutes_float,
                    "field_goals_made": fgm,
                    "field_goals_attempted": fga,
                    "three_pointers_made": fg3m,
                    "three_pointers_attempted": fg3a,
                    "free_throws_made": ftm,
                    "free_throws_attempted": fta,
                }

            return results

        except ImportError:
            # Fall back to V2 if V3 not available
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    from nba_api.stats.endpoints import boxscoretraditionalv2
                    box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
                    player_stats_df = box.get_data_frames()[0]

                    results = {}
                    for _, row in player_stats_df.iterrows():
                        if row.get('MIN') is None or row['MIN'] == '' or row['MIN'] == '0':
                            continue

                        player_name = row['PLAYER_NAME']
                        team_abbr = row['TEAM_ABBREVIATION']

                        try:
                            minutes_str = str(row['MIN'])
                            if ':' in minutes_str:
                                mins, secs = minutes_str.split(':')
                                minutes_float = float(mins) + float(secs) / 60
                            else:
                                minutes_float = float(minutes_str)
                        except (ValueError, AttributeError):
                            minutes_float = 0.0

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
                            points=pts, rebounds=reb, assists=ast,
                            steals=stl, blocks=blk, turnovers=tov,
                            three_pointers=fg3m,
                        )

                        results[player_name] = {
                            "stats": stats, "team": team_abbr,
                            "minutes": minutes_float,
                            "field_goals_made": fgm, "field_goals_attempted": fga,
                            "three_pointers_made": fg3m, "three_pointers_attempted": fg3a,
                            "free_throws_made": ftm, "free_throws_attempted": fta,
                        }
                    return results

            except Exception as e2:
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    raise ValueError(f"Failed to fetch box score after {max_retries} retries: {e2}")

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


def find_best_possible_lineup(player_scores: dict, salary_cap: float = SALARY_CAP,
                              min_salary: float = 3000, top_n: int = 5) -> list:
    """
    Find the best possible showdown lineup from actual game results.

    Uses exhaustive combinatorial enumeration to find the truly optimal lineups,
    not just a greedy approximation.

    Args:
        player_scores: Dict of {name: {"fppg": float, "salary": float, "team": str}}
        salary_cap: Maximum total salary (with captain 1.5x multiplier)
        min_salary: Minimum salary for utility players
        top_n: Number of best lineups to return

    Returns:
        List of lineup dicts sorted by total actual fppg (descending)
    """
    from lineup_optimizer import find_best_possible_showdown_lineup

    return find_best_possible_showdown_lineup(
        player_scores, salary_cap=salary_cap,
        min_salary=int(min_salary), top_n=top_n
    )


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