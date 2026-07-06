"""
Equibase results source via the parse.bot API (per-race fallback).

parse.bot wraps Equibase.com's official result charts in a JSON REST API. The
`get_race_results` endpoint returns the top-3 finishers (win/place/show) with
program number, horse name, and mutuel payoffs for a single past race - the
richest finish data of our result sources, but billed per race (one call per
race), so it is used as a FILL-IN for races the bulk BloodHorse source misses
rather than as the primary source.

Same parse.bot API key as BloodHorse (PARSE_API_KEY) - one key works across the
whole parse.bot marketplace. Free tier: 100 calls/month total across endpoints.

API spec:
  GET https://api.parse.bot/scraper/aade7361-953e-4363-895f-0a2b28352de5/get_race_results
      ?track=CD&date=MM/DD/YYYY&race_num=1
  Header: X-API-Key: <PARSE_API_KEY>
  Response: {data:{track,date,race_num,race_info,finishers:[{Pgm # - Horse,Win,
            Place,Show}], payoffs:[...]}, status}

The `Pgm # - Horse` field concatenates the program number and horse name, e.g.
"5War Warrior" (prog 5, "War Warrior") or "1AHorse Name" (coupled entry 1A).
_parse_finisher splits this with a heuristic that distinguishes a coupled-entry
letter suffix from the first letter of the horse's name.
"""

import re

from utils import get_env, rate_limit, retry_with_backoff

API_BASE = "https://api.parse.bot/scraper/aade7361-953e-4363-895f-0a2b28352de5"
ENDPOINT = f"{API_BASE}/get_race_results"


def fetch_results(race):
    """Fetch top-3 finishers (with payoffs) for a single race from Equibase.

    Args:
        race: Race with track_code, race_date (YYYY-MM-DD), race_number.

    Returns:
        list of result dicts [{program_number, horse_name, finish_position,
        win_payoff, place_payoff, show_payoff}, ...] (top 3), or [] on failure.
    """
    api_key = get_env("PARSE_API_KEY")
    if not api_key:
        print("    [equibase_parse] No PARSE_API_KEY env var set.")
        return []

    params = {
        "track": race.track_code.upper(),
        "date": _to_us_date(race.race_date),
        "race_num": str(race.race_number),
    }

    def _get():
        import requests
        # Free tier: 5 req/min shared across parse.bot endpoints.
        rate_limit("bloodhorse_api", min_interval=13.0)
        resp = requests.get(ENDPOINT, params=params,
                            headers={"X-API-Key": api_key,
                                     "Accept": "application/json"}, timeout=30)
        if resp.status_code != 200:
            print(f"    [equibase_parse] {race.track_code} R{race.race_number} "
                  f"{race.race_date} HTTP {resp.status_code}")
            return []
        data = resp.json()
        if data.get("status") != "success":
            print(f"    [equibase_parse] {race.track_code} R{race.race_number} "
                  f"non-success: {data}")
            return []
        return (data.get("data") or {}).get("finishers") or []

    try:
        finishers = retry_with_backoff(_get, max_retries=2, base_delay=2.0)
    except Exception as e:
        print(f"    [equibase_parse] {race.track_code} R{race.race_number} failed: {e}")
        return []

    out = []
    for i, f in enumerate(finishers[:3], 1):
        prog, name = _parse_finisher(f.get("Pgm # - Horse"))
        if not name:
            continue
        out.append({
            "program_number": prog,
            "horse_name": name,
            "finish_position": i,
            "win_payoff": _to_float(f.get("Win")),
            "place_payoff": _to_float(f.get("Place")),
            "show_payoff": _to_float(f.get("Show")),
        })
    return out


def _parse_finisher(field):
    """Split a 'Pgm # - Horse' string like '5War Warrior' or '1AHorse Name'.

    Heuristic: leading 1-2 digits are the program number; an uppercase letter
    immediately after is a coupled-entry suffix (1A) ONLY if the following char is
    also uppercase (the name starts uppercase) - otherwise that letter is the
    first letter of the horse's name.

    Returns (program_number, horse_name).
    """
    s = (field or "").strip()
    m = re.match(r"^(\d{1,2})([A-Za-z]?)(.*)$", s)
    if not m:
        return None, s
    digits, letter, rest = m.groups()
    rest = rest.strip()
    if letter and rest and rest[0].isupper():
        # Letter is a coupled-entry suffix (e.g. "1A" + "Horse"), not the name.
        return digits + letter, rest
    if letter and rest and not rest[0].isupper():
        # Letter is the first char of the name (e.g. "5" + "War Warrior").
        return digits, letter + rest
    return digits, rest


def _to_float(s):
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_us_date(iso_date):
    if "/" in iso_date:
        return iso_date
    y, m, d = iso_date.split("-")
    return f"{m}/{d}/{y}"