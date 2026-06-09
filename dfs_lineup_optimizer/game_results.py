"""
Fetch NBA game results and calculate actual DK fantasy points.

Uses nba_api as primary source and StatMuse as verification/backup.
Cross-references both sources to ensure accurate box score data.
Only proceeds if the game has been confirmed as played.
"""

from draftkings_scoring import DKScoringCalculator, PlayerStats
from utils import SALARY_CAP
from itertools import combinations
from datetime import datetime, timezone
import re
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


def confirm_game_played(date, away_team, home_team):
    """Check if a game has been played and get the final score.

    Tries multiple approaches:
    1. nba_api scoreboard for final scores
    2. nba_api box score (if we can fetch stats, game was played)
    3. StatMuse web scrape for scores

    Args:
        date: Game date in 'YYYY-MM-DD' format
        away_team: Away team abbreviation (DK format, e.g., 'SAS')
        home_team: Home team abbreviation (DK format, e.g., 'NYK')

    Returns:
        dict with keys:
            - "played": bool — whether the game has been played
            - "final_score": str — e.g., "SAS 115 - NYK 111" or None
            - "source": str — "nba_api" or "statmuse" or "box_score" or None
    """
    # Try nba_api scoreboard first
    try:
        from nba_api.stats.library.parameters import LeagueID
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from nba_api.stats.endpoints import scoreboardv2
            sb = scoreboardv2.ScoreboardV2(
                game_date=date,
                league_id=LeagueID.nba
            )
            games = sb.get_data_frames()[0]

            home_id = NBA_TEAM_IDS.get(home_team)
            away_id = NBA_TEAM_IDS.get(away_team)

            for _, game in games.iterrows():
                game_home_id = game['HOME_TEAM_ID']
                game_away_id = game['VISITOR_TEAM_ID']
                if game_home_id == home_id and game_away_id == away_id:
                    # Check various possible column names for scores
                    home_pts = (game.get('HOME_TEAM_PTS')
                                or game.get('HOME_TEAM_WINS_LOSSES')
                                or game.get('PTS_HOME'))
                    away_pts = (game.get('VISITOR_TEAM_PTS')
                                or game.get('VISITOR_TEAM_WINS_LOSSES')
                                or game.get('PTS_AWAY'))
                    if home_pts and away_pts:
                        try:
                            return {
                                "played": True,
                                "final_score": f"{away_team} {int(away_pts)} - {home_team} {int(home_pts)}",
                                "source": "nba_api"
                            }
                        except (ValueError, TypeError):
                            pass

    except Exception:
        pass  # Fall through to next method

    # Try fetching box score directly — if we get data, the game was played
    try:
        box_data = fetch_box_score(date=date, home_team=home_team, away_team=away_team)
        if box_data and len(box_data) > 0:
            # Calculate total points per team from box score
            away_pts = 0
            home_pts = 0
            for name, data in box_data.items():
                pts = data["stats"].points
                team = data.get("team", "")
                if team == away_team:
                    away_pts += pts
                elif team == home_team:
                    home_pts += pts

            # Verify both teams have players
            away_players = sum(1 for d in box_data.values() if d.get("team") == away_team)
            home_players = sum(1 for d in box_data.values() if d.get("team") == home_team)

            if away_players > 0 and home_players > 0:
                return {
                    "played": True,
                    "final_score": f"{away_team} {int(away_pts)} - {home_team} {int(home_pts)}",
                    "source": "box_score"
                }
    except Exception:
        pass

    # Try StatMuse web scrape
    try:
        result = _fetch_statmuse_game_result(date, away_team, home_team)
        if result:
            return result
    except Exception:
        pass

    return {"played": False, "final_score": None, "source": None}


def _fetch_statmuse_game_result(date, away_team, home_team):
    """Fetch game result from StatMuse using requests.

    Args:
        date: Game date in 'YYYY-MM-DD' format
        away_team: Away team abbreviation
        home_team: Home team abbreviation

    Returns:
        dict with "played", "final_score", "source" keys, or None if not found
    """
    import requests

    # Build the StatMuse URL from team abbreviations and date
    url = get_statmuse_url(date, away_team, home_team)

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None

        text = resp.text

        # Look for final score patterns in the page
        # Pattern 1: "SAS 115, NYK 111" or similar
        score_pattern = re.compile(
            rf'{away_team}\s*(\d{{2,3}})[,\s]+{home_team}\s*(\d{{2,3}})', re.IGNORECASE
        )
        m = score_pattern.search(text)
        if m:
            return {
                "played": True,
                "final_score": f"{away_team} {m.group(1)} - {home_team} {m.group(2)}",
                "source": "statmuse"
            }

        # Pattern 2: Try reversed order (home team first)
        score_pattern2 = re.compile(
            rf'{home_team}\s*(\d{{2,3}})[,\s]+{away_team}\s*(\d{{2,3}})', re.IGNORECASE
        )
        m2 = score_pattern2.search(text)
        if m2:
            return {
                "played": True,
                "final_score": f"{away_team} {m2.group(2)} - {home_team} {m2.group(1)}",
                "source": "statmuse"
            }

    except Exception:
        pass

    return None


def get_statmuse_url(date, away_team, home_team):
    """Get the StatMuse URL for a game's box score page.

    StatMuse URL format: https://www.statmuse.com/nba/game/{m}-{d}-{yyyy}-{away}-at-{home}
    No zero-padding on month/day, full 4-digit year, lowercase team abbreviations.
    Note: StatMuse also has numeric game IDs at the end, but the URL works without them.

    Args:
        date: Game date in 'YYYY-MM-DD' format
        away_team: Away team abbreviation (e.g., 'SAS')
        home_team: Home team abbreviation (e.g., 'NYK')

    Returns:
        str: StatMuse URL for the game page
    """
    month, day, year = date.split('-')
    date_str = f"{int(month)}-{int(day)}-{year}"
    away_lower = away_team.lower()
    home_lower = home_team.lower()
    return f"https://www.statmuse.com/nba/game/{date_str}-{away_lower}-at-{home_lower}"


def get_statmuse_prompt():
    """Get the prompt for extracting box score data from StatMuse.

    Returns:
        str: Prompt to pass to WebFetch for box score extraction
    """
    return """Extract the complete box score from this NBA game page.

For EACH player who played (skip DNP players), provide their stats in this exact format, one player per line:
PLAYER_NAME|TEAM|MINUTES|PTS|REB|AST|STL|BLK|TO|3PM

Use FULL player names (e.g., "Victor Wembanyama" not "V. Wembanyama").
Use team abbreviation (SAS or NYK).
If the game data is not available, return "NO_DATA"."""


def parse_statmuse_box_score(raw_text, away_team, home_team):
    """Parse the raw text output from StatMuse WebFetch into player stats.

    Args:
        raw_text: The text output from WebFetch containing the box score
        away_team: Away team abbreviation (e.g., 'SAS')
        home_team: Home team abbreviation (e.g., 'NYK')

    Returns:
        dict: {player_name: {"stats": PlayerStats, "team": str, "minutes": float}}
    """
    calc = DKScoringCalculator()
    results = {}

    for line in raw_text.strip().split('\n'):
        line = line.strip()
        if not line or '|' not in line:
            continue

        parts = line.split('|')
        if len(parts) < 10:
            continue

        try:
            name = parts[0].strip()
            team = parts[1].strip()
            minutes = float(parts[2]) if parts[2].strip() else 0.0
            pts = float(parts[3]) if parts[3].strip() else 0.0
            reb = float(parts[4]) if parts[4].strip() else 0.0
            ast = float(parts[5]) if parts[5].strip() else 0.0
            stl = float(parts[6]) if parts[6].strip() else 0.0
            blk = float(parts[7]) if parts[7].strip() else 0.0
            tov = float(parts[8]) if parts[8].strip() else 0.0
            tp_made = float(parts[9]) if parts[9].strip() else 0.0

            stats = PlayerStats(
                points=pts, rebounds=reb, assists=ast,
                steals=stl, blocks=blk, turnovers=tov,
                three_pointers=tp_made,
            )

            results[name] = {
                "stats": stats,
                "team": team,
                "minutes": minutes,
            }
        except (ValueError, IndexError):
            continue

    return results


def fetch_statmuse_box_score(date, away_team, home_team):
    """Fetch box score from StatMuse using requests.

    Constructs the StatMuse game URL, fetches the page, and parses
    the box score tables to extract player stats.

    Args:
        date: Game date in 'YYYY-MM-DD' format
        away_team: Away team abbreviation (e.g., 'SAS')
        home_team: Home team abbreviation (e.g., 'NYK')

    Returns:
        dict: {player_name: {"stats": PlayerStats, "team": str, "minutes": float}}
              or empty dict if fetch/parsing fails
    """
    import requests

    url = get_statmuse_url(date, away_team, home_team)
    prompt = get_statmuse_prompt()

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"  StatMuse returned status {resp.status_code}")
            return {}

        text = resp.text

        # Parse HTML tables for box score data
        # StatMuse pages contain tables with player stats
        results = _parse_statmuse_html(text, away_team, home_team)

        if results:
            print(f"  Parsed {len(results)} players from StatMuse HTML")
            return results

        # Fallback: try parsing markdown-style tables (for API responses)
        raw_text = _html_to_markdown_tables(text)
        if raw_text and "NO_DATA" not in raw_text:
            parsed = parse_statmuse_box_score(raw_text, away_team, home_team)
            if parsed:
                print(f"  Parsed {len(parsed)} players from StatMuse markdown")
                return parsed

        print("  Could not parse box score from StatMuse page")
        return {}

    except requests.exceptions.Timeout:
        print("  StatMuse request timed out")
        return {}
    except Exception as e:
        print(f"  StatMuse fetch error: {e}")
        return {}


def _parse_statmuse_html(html_text, away_team, home_team):
    """Parse StatMuse HTML page for box score data.

    Args:
        html_text: Raw HTML from StatMuse game page
        away_team: Away team abbreviation
        home_team: Home team abbreviation

    Returns:
        dict: {player_name: {"stats": PlayerStats, "team": str, "minutes": float}}
    """
    calc = DKScoringCalculator()
    results = {}

    # Find all table rows in the HTML
    # StatMuse uses <tr> rows with player data
    # Pattern: <td>Player Name</td><td>MIN</td><td>PTS</td><td>REB</td>...
    row_pattern = re.compile(
        r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE
    )
    cell_pattern = re.compile(
        r'<td[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE
    )

    # Clean HTML tags from cell content
    def clean_html(s):
        s = re.sub(r'<[^>]+>', '', s)
        s = s.replace('&nbsp;', ' ').replace('&amp;', '&')
        return s.strip()

    for row_match in row_pattern.finditer(html_text):
        row_html = row_match.group(1)
        cells = [clean_html(cell_match.group(1)) for cell_match in cell_pattern.finditer(row_html)]

        # Need at least: Name, MIN, PTS, REB, AST, STL, BLK, TO, 3PM
        # Typical StatMuse box score: Name | MIN | PTS | FGM-A | 3PM-A | FTM-A | REB | AST | STL | BLK | TO
        if len(cells) < 10:
            continue

        name = cells[0]
        if not name or name in ('Player', 'Name', 'TOTALS', 'Team', 'Totals'):
            continue

        # Try to identify team from context or assign based on position
        team = away_team  # Default, will be overridden if we can determine

        try:
            # Parse minutes (could be "32:30" or "32" or empty)
            minutes_str = cells[1] if len(cells) > 1 else '0'
            if ':' in minutes_str:
                parts = minutes_str.split(':')
                minutes = float(parts[0]) + float(parts[1]) / 60
            else:
                minutes = float(minutes_str) if minutes_str else 0.0

            # Skip players with 0 minutes (DNP)
            if minutes <= 0:
                continue

            # Parse stats - find PTS, REB, AST columns
            # StatMuse typical layout: Name | MIN | PTS | FGM | FGA | 3PM | 3PA | FTM | FTA | REB | AST | STL | BLK | TO
            # But layouts vary, so we try to extract numbers intelligently
            nums = []
            for c in cells[1:]:
                try:
                    nums.append(float(c))
                except (ValueError, TypeError):
                    nums.append(0.0)

            # Map based on common StatMuse layouts
            # Layout A: Name | MIN | PTS | REB | AST | STL | BLK | TO | 3PM
            # Layout B: Name | MIN | PTS | FGM | FGA | 3PM | 3PA | FTM | FTA | REB | AST | STL | BLK | TO
            if len(cells) >= 14:
                # Layout B - full stat line
                pts = float(cells[2]) if cells[2] else 0.0
                fg3m = float(cells[5]) if cells[5] else 0.0
                reb = float(cells[9]) if cells[9] else 0.0
                ast = float(cells[10]) if cells[10] else 0.0
                stl = float(cells[11]) if cells[11] else 0.0
                blk = float(cells[12]) if cells[12] else 0.0
                tov = float(cells[13]) if cells[13] else 0.0
            elif len(cells) >= 9:
                # Layout A - condensed
                pts = float(cells[2]) if cells[2] else 0.0
                reb = float(cells[3]) if cells[3] else 0.0
                ast = float(cells[4]) if cells[4] else 0.0
                stl = float(cells[5]) if cells[5] else 0.0
                blk = float(cells[6]) if cells[6] else 0.0
                tov = float(cells[7]) if cells[7] else 0.0
                fg3m = float(cells[8]) if cells[8] else 0.0
            else:
                continue

            # Skip if points and other stats are all 0 (likely a header or non-player)
            if pts == 0 and reb == 0 and ast == 0 and minutes < 5:
                continue

            stats = PlayerStats(
                points=pts, rebounds=reb, assists=ast,
                steals=stl, blocks=blk, turnovers=tov,
                three_pointers=fg3m,
            )

            results[name] = {
                "stats": stats,
                "team": team,
                "minutes": minutes,
            }

        except (ValueError, IndexError):
            continue

    return results


def _html_to_markdown_tables(html_text):
    """Convert HTML tables to pipe-delimited markdown for parsing.

    Args:
        html_text: Raw HTML containing tables

    Returns:
        str: Pipe-delimited rows of player stats
    """
    # Extract table content
    table_pattern = re.compile(r'<table[^>]*>(.*?)</table>', re.DOTALL | re.IGNORECASE)
    row_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', re.DOTALL | re.IGNORECASE)

    rows = []
    for table_match in table_pattern.finditer(html_text):
        table_html = table_match.group(1)
        for row_match in row_pattern.finditer(table_html):
            cells = []
            for cell_match in cell_pattern.finditer(row_match.group(1)):
                # Strip HTML tags
                content = re.sub(r'<[^>]+>', '', cell_match.group(1))
                content = content.replace('&nbsp;', ' ').replace('&amp;', '&').strip()
                cells.append(content)
            if cells:
                rows.append('|'.join(cells))

    return '\n'.join(rows)


def verify_box_score(primary_stats, secondary_stats):
    """Cross-verify box score stats between two sources.

    For each player found in both sources, compare PTS/REB/AST/STL/BLK/TO/3PM.
    Reports discrepancies and uses the secondary source when they differ.

    Args:
        primary_stats: Dict from nba_api (or first source)
        secondary_stats: Dict from StatMuse (or second source)

    Returns:
        dict: Merged/corrected stats with the best data per player
    """
    if not secondary_stats:
        return primary_stats

    merged = {}
    discrepancies = []

    # Start with primary stats
    for name, data in primary_stats.items():
        merged[name] = data.copy()

    # Cross-verify and fill in from secondary
    for name, sec_data in secondary_stats.items():
        if name in merged:
            pri = merged[name]
            sec = sec_data

            # Compare key stats
            stat_fields = [
                ('points', 'PTS'), ('rebounds', 'REB'), ('assists', 'AST'),
                ('steals', 'STL'), ('blocks', 'BLK'), ('turnovers', 'TO'),
                ('three_pointers', '3PM')
            ]

            pri_stats = pri.get("stats") if "stats" in pri else pri
            sec_stats = sec.get("stats") if "stats" in sec else sec

            for field, label in stat_fields:
                pri_val = getattr(pri_stats, field, None) if isinstance(pri_stats, PlayerStats) else pri_stats.get(field)
                sec_val = getattr(sec_stats, field, None) if isinstance(sec_stats, PlayerStats) else sec_stats.get(field)

                if pri_val is not None and sec_val is not None and pri_val != sec_val:
                    discrepancies.append(f"{name} {label}: nba_api={pri_val}, statmuse={sec_val}")

            # If discrepancies exist, prefer secondary (StatMuse) data
            # as it's more likely to be correct for recent games
            if discrepancies:
                # Use StatMuse data for this player
                if "stats" in sec_data:
                    merged[name] = sec_data.copy()
        else:
            # Player only in secondary source — add them
            merged[name] = sec_data.copy()

    if discrepancies:
        print(f"  Found {len(discrepancies)} stat discrepancies between sources (using StatMuse):")
        for d in discrepancies[:5]:
            print(f"    {d}")
        if len(discrepancies) > 5:
            print(f"    ... and {len(discrepancies) - 5} more")

    return merged


def _find_game_id(date, home_team, away_team, max_retries=3):
    """Find NBA game ID from date and team abbreviations."""
    from nba_api.stats.library.parameters import LeagueID

    home_id = NBA_TEAM_IDS.get(home_team)
    away_id = NBA_TEAM_IDS.get(away_team)

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