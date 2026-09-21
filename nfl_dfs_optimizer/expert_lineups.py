"""
Expert lineup sources — externally published, fully-worked DFS lineups.

The projection-source comparison (comparison.py) runs every projection
board through our exact optimizer. These sources are a different
yardstick: they publish the finished lineup itself, so they get judged on
roster-construction judgment, not just projections.

Stokastic's weekly DK cheat sheet is the one free source publishing a
complete worked classic lineup:

    https://www.stokastic.com/articles/dfs-strategy/
    draftkings-nfl-dfs-cheat-sheet-week-{week}

The lineup sits in the "Worked Example" section: a numbered list whose
items mention each pick as `Name ($X,XXX)`. The analysis prose also
mentions NON-rostered players with salaries ("Tee Higgins ($6,300) is the
conventional second piece, and the reason he is out is price..."), so the
parser keeps only the contiguous run of mentions per list item — any
sentence-ending punctuation between two mentions ends the run (a period
followed by a digit is a decimal, not a sentence end). The extracted
lineup is then VALIDATED against the contest's own draftables: exactly 9
distinct slate players, each stated salary matches DK's, the roster is a
legal classic lineup, and the total matches the article's stated
"lands on $49,800". On any mismatch it returns None with a loud note —
never a fabricated lineup.
"""

import html as html_mod
import re
from datetime import date

import requests

from projections import normalize_dst_name, normalize_name

STOKASTIC_URL = ("https://www.stokastic.com/articles/dfs-strategy/"
                 "draftkings-nfl-dfs-cheat-sheet-week-{week}")

# NFL 2026 week 1 runs Tue Sep 8 – Mon Sep 14; weeks are 7 days from there.
SEASON_WEEK1_TUESDAY = date(2026, 9, 8)

CAP = 50000

# "Name ($6,900" — name starts with a capital letter; apostrophes (Ja'Marr),
# dots, hyphens, digits (III) and multi-token names are all allowed.
MENTION_RE = re.compile(
    r"([A-Z][A-Za-z0-9'.\-]*(?:\s+[A-Za-z0-9'.\-]+)*)\s*\(\$(\d[\d,]*)")

# Sentence enders between two mentions break the contiguous-pick run.
# A period directly followed by a digit is a decimal ("plus 2.1% leverage").
SENTENCE_BREAK_RE = re.compile(r"[.;!?](?!\d)")

STATED_TOTAL_RE = re.compile(r"lands on \$([\d,]+)")
STATED_PROJECTION_RE = re.compile(r"with a ([\d.]+) projection")


def derive_week(starts_at):
    """NFL week number from a contest start datetime (Tue–Mon weeks).

    Args:
        starts_at: datetime (naive or tz-aware) of the contest

    Returns:
        Week number (1 for the Sep 8-14, 2026 window), or None if no date
    """
    if starts_at is None:
        return None
    d = starts_at.date() if hasattr(starts_at, 'date') else None
    if d is None:
        return None
    days = (d - SEASON_WEEK1_TUESDAY).days
    if days < 0:
        return None
    return days // 7 + 1


def _strip_tags(fragment):
    """HTML fragment -> plain text (tags dropped, entities unescaped)."""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_worked_example_items(page_html):
    """The <li> texts of the numbered list in the Worked Example section.

    Args:
        page_html: Full served HTML of the cheat-sheet article

    Returns:
        List of plain-text list items (empty if the section/list is absent)
    """
    heading = re.search(r"Worked Example", page_html)
    if not heading:
        return []
    tail = page_html[heading.end():]
    ol = re.search(r"<ol>(.*?)</ol>", tail, re.IGNORECASE | re.DOTALL)
    if not ol:
        return []
    items = []
    for li in re.finditer(r"<li>(.*?)</li>", ol.group(1),
                           re.IGNORECASE | re.DOTALL):
        items.append(_strip_tags(li.group(1)))
    return items


def _picks_from_items(items):
    """Extract (name, salary) picks from the Worked Example list items.

    Within each item, mentions are kept left-to-right while the text
    between the previous kept mention and the next one contains no
    sentence-ending punctuation — the first prose sentence after the picks
    (which often salary-mentions players who did NOT make the lineup,
    e.g. "Tee Higgins ($6,300) ... the reason he is out is price") ends
    the run for that item.

    Returns:
        List of (raw name, salary int) mentions
    """
    picks = []
    for item in items:
        last_end = None
        for match in MENTION_RE.finditer(item):
            if last_end is not None and SENTENCE_BREAK_RE.search(
                    item[last_end:match.start()]):
                break  # prose took over — the rest of this item is analysis
            name = match.group(1).strip()
            name = re.sub(r"^(?:and|the)\s+", "", name, flags=re.IGNORECASE)
            picks.append((name, int(match.group(2).replace(',', ''))))
            last_end = match.end()
    return picks


def _draftable_index(draftables):
    """Lookup indexes over the contest's own draftables.

    DK slates double-list players (showdown CPT 1.5x rows; classic FLEX
    slot rows), so the lowest-salary entry per player_id is kept — the
    same rule as player_builder.build_player_pool.

    Returns:
        (by_norm_name, by_dst_name, by_last_token):
        by_norm_name: normalize_name -> [entries]
        by_dst_name: normalize_dst_name -> [entries] (DST rows only)
        by_last_token: last normalized name token -> [entries]
    """
    by_id = {}
    for d in draftables:
        if not d.get('name') or not d.get('player_id'):
            continue
        existing = by_id.get(d['player_id'])
        if existing is None or (d.get('salary') or 0) < existing['salary']:
            by_id[d['player_id']] = d

    by_norm, by_dst, by_last = {}, {}, {}
    for d in by_id.values():
        name = d['name']
        positions = d.get('positions') or []
        if any('DST' in p for p in positions) or 'DST' in (name or ''):
            by_dst.setdefault(normalize_dst_name(name), []).append(d)
            continue
        norm = normalize_name(name)
        if not norm:
            continue
        by_norm.setdefault(norm, []).append(d)
        by_last.setdefault(norm.split()[-1], []).append(d)
    return by_norm, by_dst, by_last


def _match_pick(name, salary, by_norm, by_dst, by_last):
    """Match one stated pick to a slate player.

    Order: exact normalized full name, DST team token, then (for
    single-token mentions like "Gibbs" or "Mayer") the unique slate player
    whose last name token matches. Ambiguous or absent matches fail; a
    matched player whose DK salary differs from the stated one fails.

    Returns:
        (draftable dict, None) or (None, failure reason)
    """
    norm = normalize_name(name)
    candidates = list(by_norm.get(norm, []))

    if not candidates:
        # "Falcons" -> the DST row (a plain player named like a mascot is
        # matched by the full-name layer above first)
        dst_norm = normalize_dst_name(name)
        if dst_norm:
            candidates = list(by_dst.get(dst_norm, []))

    if not candidates and ' ' not in norm:
        # Single-token mention ("Gibbs", "Mayer"): the article uses last
        # names for well-known players. Exactly one slate player with that
        # last name may match — ambiguity is a failure, never a guess.
        candidates = list(by_last.get(norm, []))
        if len(candidates) > 1:
            return None, (f"'{name}' is ambiguous on this slate "
                          f"({len(candidates)} players share the last name)")

    if not candidates:
        return None, f"'{name}' not found on the slate"
    if len(candidates) > 1:
        return None, f"'{name}' matches {len(candidates)} slate players"

    player = candidates[0]
    if player.get('salary') != salary:
        return None, (f"'{name}' salary mismatch: article says ${salary:,}, "
                       f"DK lists ${player.get('salary'):,}")
    return player, None


def _is_legal_classic(players):
    """Classic roster check: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 DST."""
    counts = {}
    for p in players:
        for pos in (p.get('positions') or []):
            counts[pos] = counts.get(pos, 0) + 1
    if counts.get('QB', 0) != 1 or counts.get('DST', 0) != 1:
        return False
    skill = counts.get('RB', 0) + counts.get('WR', 0) + counts.get('TE', 0)
    return (skill == 7 and counts.get('RB', 0) >= 2
            and counts.get('WR', 0) >= 3 and counts.get('TE', 0) >= 1)


def _opponent(entry):
    """Opponent team abbr from the 'game' string ("KC @ BAL" -> BAL for KC)."""
    game = entry.get('game') or ''
    teams = []
    if '@' in game:
        teams = [t.strip() for t in game.split('@', 1)]
    else:
        m = re.match(r"(.+?)\s+vs\.?\s+(.+)", game, re.IGNORECASE)
        if m:
            teams = [m.group(1).strip(), m.group(2).strip()]
    if len(teams) != 2 or not entry.get('team'):
        return None
    return teams[1] if entry['team'] == teams[0] else teams[0]


def _lineup_positions(players):
    """Assign DK lineup slots (the extra RB/WR/TE is the FLEX)."""
    slots = []
    used = {'QB': 0, 'RB': 0, 'WR': 0, 'TE': 0}
    limits = {'QB': 1, 'RB': 2, 'WR': 3, 'TE': 1}
    for p in players:
        pos = next((q for q in ('QB', 'RB', 'WR', 'TE')
                    if q in (p.get('positions') or [])), None)
        if pos is None:
            slots.append('DST' if any('DST' in x for x in
                                      (p.get('positions') or [])) else 'FLEX')
        else:
            used[pos] += 1
            slots.append(pos if used[pos] <= limits[pos] else 'FLEX')
    return slots


def parse_stokastic_lineup(page_html, draftables):
    """Parse + validate the cheat sheet's worked lineup against the slate.

    Args:
        page_html: Full served HTML of the weekly cheat-sheet article
        draftables: The contest's draftables (dk_client.fetch_draftables)

    Returns:
        Lineup dict in classic lineup_to_dict shape (players carry the
        slate's own names/salaries/teams; projection is None — Stokastic
        doesn't publish per-player projections, only the lineup total),
        or None if anything fails validation (a note says why)
    """
    items = _extract_worked_example_items(page_html)
    if not items:
        print("Stokastic: no 'Worked Example' numbered list found — skipped")
        return None

    picks = _picks_from_items(items)
    if len(picks) != 9:
        shown = ', '.join(f"{n} ${s:,}" for n, s in picks) or '(none)'
        print(f"Stokastic: extracted {len(picks)} picks, expected 9 "
              f"({shown}) — skipped")
        return None

    by_norm, by_dst, by_last = _draftable_index(draftables)
    players, failures = [], []
    for name, salary in picks:
        player, reason = _match_pick(name, salary, by_norm, by_dst, by_last)
        if player is None:
            failures.append(reason)
        else:
            players.append(player)
    if failures:
        for reason in failures:
            print(f"Stokastic: {reason}")
        print("Stokastic: lineup failed validation — skipped "
              "(nothing fabricated)")
        return None

    ids = [p['player_id'] for p in players]
    if len(set(ids)) != 9:
        print(f"Stokastic: picks resolve to {len(set(ids))} distinct "
              f"players, expected 9 — skipped")
        return None

    total_salary = sum(p['salary'] for p in players)
    if total_salary > CAP:
        print(f"Stokastic: lineup totals ${total_salary:,} — over the "
              f"${CAP:,} cap, skipped")
        return None

    section_text = _strip_tags(_worked_example_html(page_html) or '')
    stated_total = STATED_TOTAL_RE.search(section_text)
    if stated_total and int(stated_total.group(1).replace(',', '')) != \
            total_salary:
        print(f"Stokastic: article total ${stated_total.group(1)} != "
              f"extracted ${total_salary:,} — skipped")
        return None

    if not _is_legal_classic(players):
        positions = sorted({p['position'] for p in players})
        print(f"Stokastic: picks do not form a legal classic roster "
              f"({positions}) — skipped")
        return None

    proj_match = STATED_PROJECTION_RE.search(section_text)
    total_projection = (float(proj_match.group(1)) if proj_match else 0.0)

    slots = _lineup_positions(players)
    lineup_players = [{
        'name': p['name'],
        'lineup_position': slot,
        'positions': p.get('positions') or [],
        'team': p.get('team'),
        'salary': p['salary'],
        'projection': None,  # per-player projections not published
        'opponent': _opponent(p),
    } for p, slot in zip(players, slots)]

    print(f"Stokastic: expert lineup validated — 9/9 picks matched the "
          f"slate, ${total_salary:,}, {total_projection:.1f} projected")
    return {
        'players': lineup_players,
        'total_projection': total_projection,
        'total_salary': total_salary,
    }


def _worked_example_html(page_html):
    """The Worked Example section's raw HTML (heading through list)."""
    heading = re.search(r"Worked Example", page_html)
    if not heading:
        return None
    tail = page_html[heading.end():]
    ol = re.search(r"<ol>(.*?)</ol>", tail, re.IGNORECASE | re.DOTALL)
    if not ol:
        return tail[:2000]
    return tail[:ol.end()]


def fetch_stokastic_lineup(draftables, week=None, starts_at=None):
    """Fetch the weekly cheat sheet and parse its worked lineup.

    Args:
        draftables: The contest's draftables (validation + lineup fields)
        week: Explicit week number (overrides derivation)
        starts_at: Contest start datetime, for deriving the week

    Returns:
        Validated lineup dict (see parse_stokastic_lineup) or None — a
        failure always prints a note and never raises
    """
    week = week or derive_week(starts_at)
    if not week:
        print("Stokastic: cannot determine NFL week — skipped")
        return None

    url = STOKASTIC_URL.format(week=week)
    try:
        response = requests.get(url, timeout=20,
                                headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Stokastic: cheat sheet fetch failed ({e}) — skipped")
        return None

    return parse_stokastic_lineup(response.text, draftables)


def build_expert_lineup(draftables, picks, mode, source_label,
                        total_projection=0.0):
    """Build + validate a manually-transcribed expert lineup.

    For sources that publish a finished lineup but have no parser (SI.com,
    Sporting News, fantasyleagues.info, ...): the picks are transcribed by
    hand and validated here against the contest's own draftables — the
    same rules as the Stokastic parser (distinct players, stated salaries
    must match DK's, legal roster, under the cap). Any failure prints a
    note and returns None — never a fabricated lineup.

    Args:
        draftables: The contest's draftables (dk_client.fetch_draftables)
        picks: List of (name, stated_salary, is_captain) tuples. Showdown
               captains are stated at their 1.5x CPT-slot price (what the
               article prints); FLEX at the base DK salary.
        mode: 'showdown' or 'classic'
        source_label: Source name, for the printed notes only
        total_projection: The article's stated projection total, or 0.0
               when it publishes none (the sentinel that keeps the row
               out of lineup-level error stats in --summary)

    Returns:
        Lineup dict in the optimizer's storage shape — showdown captains
        carry their BASE salary with the 1.5x applied in total_salary,
        classic in the lineup_to_dict shape — or None on any failure.
    """
    by_norm, by_dst, by_last = _draftable_index(draftables)
    expected = 6 if mode == 'showdown' else 9
    if len(picks) != expected:
        print(f"{source_label}: {len(picks)} picks, expected {expected} "
              f"— skipped")
        return None

    captains = [p for p in picks if p[2]]
    if mode == 'showdown' and len(captains) != 1:
        print(f"{source_label}: {len(captains)} captains, expected 1 "
              f"— skipped")
        return None
    if mode == 'classic' and captains:
        print(f"{source_label}: classic lineups have no captain — skipped")
        return None

    players, failures = [], []
    for name, stated_salary, is_captain in picks:
        # Captains are stated at the 1.5x CPT-slot price; DK draftables
        # carry the base salary the slate is keyed on
        if is_captain:
            base_salary = stated_salary / 1.5
            if base_salary != int(base_salary):
                failures.append(f"'{name}' CPT price ${stated_salary:,} is "
                                f"not 1.5x a whole DK salary")
                continue
            base_salary = int(base_salary)
        else:
            base_salary = stated_salary
        player, reason = _match_pick(name, base_salary, by_norm, by_dst,
                                     by_last)
        if player is None:
            failures.append(reason)
        else:
            players.append((player, is_captain))

    if failures:
        for reason in failures:
            print(f"{source_label}: {reason}")
        print(f"{source_label}: lineup failed validation — skipped "
              f"(nothing fabricated)")
        return None

    ids = [p['player_id'] for p, _ in players]
    if len(set(ids)) != expected:
        print(f"{source_label}: picks resolve to {len(set(ids))} distinct "
              f"players, expected {expected} — skipped")
        return None

    if mode == 'showdown':
        team_counts = {}
        for p, _ in players:
            team_counts[p.get('team')] = team_counts.get(p.get('team'), 0) + 1
        if max(team_counts.values()) > 5:
            print(f"{source_label}: {max(team_counts.values())} players "
                  f"from one team (max 5) — skipped")
            return None
        # total_salary is the enterable total: captain at 1.5x
        total_salary = sum(p['salary'] * 1.5 if cpt else p['salary']
                           for p, cpt in players)
        total_salary = int(total_salary)
        captain, = (p for p, cpt in players if cpt)
        captain = {**captain, 'lineup_position': 'CPT',
                   'projection': None, 'opponent': _opponent(captain)}
        flex = [{**p, 'lineup_position': 'FLEX', 'projection': None,
                 'opponent': _opponent(p)}
                for p, cpt in players if not cpt]
        lineup = {'captain': captain, 'flex': flex,
                  'total_projection': total_projection,
                  'total_salary': total_salary}
    else:
        if not _is_legal_classic([p for p, _ in players]):
            positions = sorted({p['position'] for p, _ in players})
            print(f"{source_label}: picks do not form a legal classic "
                  f"roster ({positions}) — skipped")
            return None
        total_salary = sum(p['salary'] for p, _ in players)
        roster = [p for p, _ in players]
        slots = _lineup_positions(roster)
        lineup = {
            'players': [{'name': p['name'], 'lineup_position': slot,
                         'positions': p.get('positions') or [],
                         'team': p.get('team'), 'salary': p['salary'],
                         'projection': None, 'opponent': _opponent(p)}
                        for p, slot in zip(roster, slots)],
            'total_projection': total_projection,
            'total_salary': total_salary,
        }

    if total_salary > CAP:
        print(f"{source_label}: lineup totals ${total_salary:,} — over the "
              f"${CAP:,} cap, skipped")
        return None

    proj_note = (f", {total_projection:.1f} projected"
                 if total_projection else " (no stated projection)")
    print(f"{source_label}: expert lineup validated — {expected}/{expected} "
          f"picks matched the slate, ${total_salary:,}{proj_note}")
    return lineup


def print_expert_lineup(lineup, source='stokastic'):
    """Print an expert lineup in the comparison report's format."""
    print(f"\n--- {source} (expert published lineup) ---")
    proj_note = (f"{lineup['total_projection']:.2f} projected | "
                 if lineup['total_projection'] else "")
    print(f"  {proj_note}${lineup['total_salary']:,.0f} salary")
    if 'captain' in lineup:
        captain = lineup['captain']
        print(f"  {'CPT':<5} {captain['name']:<25} "
              f"{captain.get('team') or '':<5} "
              f"${captain['salary'] * 1.5:>8,.0f}")
        players = lineup['flex']
    else:
        players = lineup['players']
    for player in players:
        proj = (f"{player['projection']:.2f}" if player['projection']
                is not None else '  --')
        print(f"  {player['lineup_position']:<5} {player['name']:<25} "
              f"{player['team'] or '':<5} ${player['salary']:>8,.0f}  {proj}")