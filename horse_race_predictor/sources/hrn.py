"""
Horse Racing Nation (HRN) entries source.

Fetches the field for a race from HRN's free entries/results page, which is
server-rendered HTML (no bot wall, no JS shell) and is the most reliable free
entries target found. Unlike the bot-walled Equibase static pages, this one
yields real data via plain requests.

Each entry is returned with a `status` field so the predictor can account for
late scratches and conditional entries:
  - "in"        : in the body, expected to run on the scheduled surface.
  - "mto"       : Main Track Only - runs only if the race is moved off turf to dirt.
  - "ae"        : Also Eligible - draws in only if a body horse scratches.
  - "scratched" : declared out and will not run.

Scratch/MTO/AE are read from the dedicated `td.table-entries-scratch-col`
(data-label "Scratched?") and from MLO-cell markers like " MTO". A struck-through
horse link (<s>/<del>/line-through) is also treated as a scratch. Detection is
best-effort against HRN's current markup; failures return [] (never raise).

URL: https://entries.horseracingnation.com/entries-results/{track_slug}/{YYYY-MM-DD}
"""

import re

from utils import retry_with_backoff, rate_limit

# Equibase track code -> HRN URL slug. Extend as needed; unknown codes fall back
# to the lowercased code (works for many single-word track names).
TRACK_SLUGS = {
    "SAR": "saratoga",
    "BEL": "belmont-park",
    "AQU": "aqueduct",
    "SA": "santa-anita",
    "DMR": "del-mar",
    "GG": "golden-gate-fields",
    "CD": "churchill-downs",
    "KEE": "keeneland",
    "ELP": "ellis-park",
    "TP": "turfway-park",
    "GP": "gulfstream-park",
    "TAM": "tampa-bay-downs",
    "MTH": "monmouth-park",
    "PIM": "pimlico",
    "LRL": "laurel-park",
    "PRX": "parx-racing",
    "FG": "fair-grounds",
    "OP": "oaklawn-park",
    "EVD": "evangeline-downs",
    "DTA": "delta-downs",
    "WO": "woodbine",
    "HOU": "sam-houston-race-park",
    "LSA": "lone-star-park",
    "RP": "remington-park",
    "TDP": "thistledown",
    "MNR": "mountaineer",
    "PID": "presque-isle-downs",
    "MVG": "mahoning-valley-race-course",
    "FL": "finger-lakes",
    "IND": "horseshoe-indianapolis",
    "COL": "colonial-downs",
    "DEL": "delaware-park",
    "CBY": "canterbury-park",
    "HTH": "hawthorne",
}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _url(track_code, race_date):
    slug = TRACK_SLUGS.get(track_code.upper(), track_code.lower())
    return f"https://entries.horseracingnation.com/entries-results/{slug}/{race_date}"


def fetch_entries(race):
    """Fetch entries (with scratch/MTO/AE status) for a race from HRN.

    Returns a list of normalized entry dicts:
        [{program_number, horse_name, jockey, trainer, morning_line_odds,
          post_position, status, scratched}, ...]
    Empty list on any failure.
    """
    card = fetch_card(race.track_code, race.race_date)
    if not card:
        return []
    entries = card.get(race.race_number)
    if not entries:
        print(f"    [hrn] No entries parsed for {race.track_code} R{race.race_number} "
              f"on {race.race_date} (race may not exist or markup changed)")
    return entries or []


def fetch_card(track_code, race_date):
    """Fetch the full card (all races) for a track/date from HRN in one page load.

    Returns a dict {race_number: [entry_dicts]} for every race on the card, or
    {} on any failure (no page, no tables). This is the efficient entry point for
    the backtest runner, which needs every race on a card without re-fetching
    the page per race.
    """
    card, _ = fetch_card_and_results(track_code, race_date)
    return card


def fetch_card_and_results(track_code, race_date):
    """Fetch the HRN entries-results page once and parse BOTH entries and results.

    Returns (entries_card, results_card):
        entries_card  = {race_number: [entry_dicts]}   (table-entries tables)
        results_card  = {race_number: [result_dicts]}  (table-payouts tables)
    Same URL serves both; one page load covers the whole card for entries and,
    once races are official, finish order + payoffs. Use this instead of calling
    fetch_card + fetch_results_card separately (which would fetch the page twice).
    """
    url = _url(track_code, race_date)
    try:
        html = _fetch_html(url)
    except Exception as e:
        print(f"    [hrn] GET failed for {url}: {e}")
        return {}, {}
    if not html:
        return {}, {}
    return _parse_card_html(html), _parse_results_card_html(html)


def _parse_card_html(html):
    """Parse all race tables on an HRN entries page -> {race_number: [entries]}.

    Race numbers are assigned by table order (1-based). If the page is the
    generic day-summary fallback (no table-entries tables), returns {}.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", class_="table-entries")
    card = {}
    for i, table in enumerate(tables, 1):
        entries = []
        for tr in table.find_all("tr"):
            e = _parse_row(tr)
            if e:
                entries.append(e)
        if entries:
            card[i] = entries
    return card


def _fetch_html(url):
    import requests
    rate_limit("hrn", min_interval=1.0)

    def _get():
        resp = requests.get(url, headers={"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9"},
                            timeout=20)
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return resp.text

    return retry_with_backoff(_get, max_retries=2, base_delay=1.0)


def _parse_entries_html(html, race_number):
    """Parse HRN entries page -> list of entry dicts for the requested race."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", class_="table-entries")
    if race_number < 1 or race_number > len(tables):
        return []
    table = tables[race_number - 1]

    entries = []
    for tr in table.find_all("tr"):
        e = _parse_row(tr)
        if e:
            entries.append(e)
    return entries


# ── Results (finish order + payoffs) ──────────────────────────────────────
# The same entries-results URL also renders per-race `table-payouts` tables once
# a card has gone official. Each payouts table lists the finishers in finish
# order (winner first) with columns: Runner (name + program-number img), Win,
# Place, Show. The top-3 cells carry $2 WPS payoffs; 4th (the last superfecta
# leg) shows "-". This gives full top-4 finish order + mutuel payoffs in one
# page per track/date - free and unlimited, with no API budget - so it is the
# primary results source, parse.bot being the fallback for races HRN hasn't
# populated yet.

def fetch_results(race):
    """Fetch finish order + WPS payoffs for one race from HRN (per-card fetch).

    Returns a list of normalized result dicts:
        [{program_number, horse_name, finish_position, win_payoff,
          place_payoff, show_payoff}, ...]
    Empty list on any failure / race not found / not yet run.
    """
    card = fetch_results_card(race.track_code, race.race_date)
    return card.get(race.race_number) or []


def fetch_results_card(track_code, race_date):
    """Fetch finish order + WPS payoffs for every run race on a track/date.

    One page load covers the whole card (same URL as entries). Returns a dict
    {race_number: [result_dicts]} for every race with a payouts table, or {} on
    any failure (no page, dark day, or card not yet run / no payouts tables).
    """
    url = _url(track_code, race_date)
    try:
        html = _fetch_html(url)
    except Exception as e:
        print(f"    [hrn] results GET failed for {url}: {e}")
        return {}
    if not html:
        return {}
    return _parse_results_card_html(html)


def _parse_results_card_html(html):
    """Parse all `table-payouts` tables -> {race_number: [result_dicts]}.

    Race numbers are assigned by payouts-table order (1-based), which matches
    the entries-table order on the same page.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    card = {}
    for i, table in enumerate(soup.find_all("table", class_="table-payouts"), 1):
        body = table.find("tbody")
        if not body:
            continue
        results = []
        for fin, tr in enumerate(body.find_all("tr", recursive=False), 1):
            r = _parse_payouts_row(tr, fin)
            if r:
                results.append(r)
        if results:
            card[i] = results
    return card


def _parse_payouts_row(tr, finish_position):
    """Parse one <tr> of an HRN payouts table into a result dict (or None).

    Columns: [horse name (+speed fig), program-number img, Win, Place, Show].
    """
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 5:
        return None
    name = _strip_speed_fig(cells[0].get_text(" ", strip=True))
    if not name:
        return None
    img = cells[1].find("img")
    prog = img["alt"].strip() if img and img.get("alt") else cells[1].get_text(strip=True)
    return {
        "program_number": prog or None,
        "horse_name": name,
        "finish_position": finish_position,
        "win_payoff": _parse_money(cells[2].get_text(strip=True)),
        "place_payoff": _parse_money(cells[3].get_text(strip=True)),
        "show_payoff": _parse_money(cells[4].get_text(strip=True)),
    }


def _strip_speed_fig(text):
    """Drop the trailing HRN speed figure, e.g. 'War Warrior (92)' -> 'War Warrior'.

    Strips a parenthetical of pure digits (the speed fig), including HRN's
    asterisk annotation on the figure, e.g. 'High Lateen (105*)' -> 'High Lateen'.
    Country/breeding suffixes like '(IRE)' are left intact.
    """
    return re.sub(r"\s*\(\d+\*?\)\s*$", "", (text or "")).strip()


def _parse_money(s):
    """Parse a payoff cell like '$5.20' -> 5.20; '-' / '' / None -> None."""
    s = (s or "").strip()
    if not s or s == "-":
        return None
    m = re.search(r"\$?([\d,]+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_row(tr):
    """Parse one <tr> of an HRN entries table into an entry dict (or None)."""
    # Program number: in the first td's <img alt="N"> (or cell text)
    prog = None
    prog_cell = tr.find("td", attrs={"data-label": re.compile(r"Program Number")})
    if prog_cell:
        img = prog_cell.find("img")
        if img and img.get("alt"):
            prog = img["alt"].strip()
        if not prog:
            prog = prog_cell.get_text(strip=True)
    if not prog or not re.match(r"^\d{1,2}[A-Za-z]?$", prog):
        return None

    # Horse name from the horse-link anchor (clean, no sire/figure)
    name = ""
    name_cell = tr.find("td", attrs={"data-label": re.compile(r"Horse / Sire")})
    if name_cell:
        a = name_cell.find("a", class_="horse-link")
        if a:
            name = a.get_text(strip=True)
        else:
            name = name_cell.get_text(" ", strip=True)
    if not name:
        return None

    # Trainer / Jockey: two <p> elements
    trainer = jockey = ""
    tj_cell = tr.find("td", attrs={"data-label": re.compile(r"Trainer / Jockey")})
    if tj_cell:
        ps = [p.get_text(strip=True) for p in tj_cell.find_all("p")]
        if len(ps) >= 1:
            trainer = ps[0]
        if len(ps) >= 2:
            jockey = ps[1]

    # Post position
    pp = None
    pp_cell = tr.find("td", attrs={"data-label": "Post Position"})
    if pp_cell:
        m = re.search(r"(\d{1,2})", pp_cell.get_text(strip=True))
        if m:
            pp = int(m.group(1))

    # Morning-line odds + status from the scratch column and MLO cell
    mlo = None
    scratch_text = ""
    scratch_cell = tr.find("td", class_="table-entries-scratch-col")
    if scratch_cell:
        scratch_text = scratch_cell.get_text(" ", strip=True)
    mlo_cell = tr.find("td", attrs={"data-label": re.compile(r"Morning Line Odds")})
    mlo_text = mlo_cell.get_text(" ", strip=True) if mlo_cell else ""

    m_match = re.search(r"(\d{1,2}\s*[/\-]\s*\d{1,2}|\d+\.\d+)", mlo_text)
    if m_match:
        mlo = _parse_mlo(m_match.group(1))

    status = _classify_status(scratch_text, mlo_text, name_cell)

    return {
        "program_number": prog,
        "horse_name": name,
        "jockey": jockey,
        "trainer": trainer,
        "morning_line_odds": mlo,
        "post_position": pp,
        "status": status,
        "scratched": status == "scratched",
    }


def _parse_mlo(token):
    token = token.strip()
    m = re.match(r"^(\d+)\s*[/\-]\s*(\d+)$", token)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        return round(num / den, 2) if den else 0.0
    try:
        return float(token)
    except ValueError:
        return None


def _classify_status(scratch_text, mlo_text, name_cell):
    """Determine entry status: 'scratched', 'mto', 'ae', or 'in'."""
    s = (scratch_text + " " + mlo_text).lower()
    if "scratch" in s or re.search(r"\bscr\b", s) or "late scratch" in s:
        return "scratched"
    # Struck-through horse name also indicates a scratch
    if name_cell is not None:
        if name_cell.find(["s", "del"]) is not None:
            return "scratched"
        style = name_cell.get("style", "") + " " + " ".join(
            p.get("style", "") for p in name_cell.find_all("p"))
        if "line-through" in style.lower():
            return "scratched"
        a = name_cell.find("a")
        if a is not None and "line-through" in (a.get("style", "") or "").lower():
            return "scratched"
    if "main track only" in s or re.search(r"\bmto\b", s):
        return "mto"
    if "also eligible" in s or re.search(r"\bae\b", s) or "also-eligible" in s:
        return "ae"
    return "in"