"""
Consensus engine for Horse Race Predictor.

Aggregates per-source expert picks onto a race's entries and produces a ranked
consensus table plus a single best pick with a confidence measure.

Scoring:
  - Each source contributes rank-based points to its selections:
        1st choice = PTS_FIRST  (default 5)
        2nd choice = PTS_SECOND (default 3)
        3rd choice = PTS_THIRD  (default 1)
  - Points sum per horse across all sources that mentioned it.
  - Best pick = highest points; tiebreak by (a) # of sources naming it #1,
    then (b) lower morning-line odds.
  - Confidence = top_pick_votes / num_successful_sources, reported alongside
    the point margin to 2nd. Higher = broader agreement.

Picks are matched to entries by program_number first, falling back to a fuzzy
horse-name match (race.normalize_horse_name) so picks still resolve when a
source omits the program number or spells a name slightly differently.
"""

from collections import defaultdict

from race import Entry, normalize_horse_name

# Rank-based point weights (configurable)
PTS_FIRST = 5
PTS_SECOND = 3
PTS_THIRD = 1

RANK_POINTS = {1: PTS_FIRST, 2: PTS_SECOND, 3: PTS_THIRD}

# Entry field names, used to coerce DB rows (which carry extra id/race_id
# columns) into Entry instances without TypeError on unexpected kwargs.
_ENTRY_FIELDS = {f for f in Entry.__dataclass_fields__}


def _to_entry(e):
    """Coerce a dict or Entry into an Entry, ignoring unknown keys (e.g. DB row ids)."""
    if isinstance(e, Entry):
        return e
    return Entry(**{k: v for k, v in e.items() if k in _ENTRY_FIELDS})


def _entries_index(entries):
    """Build {program_number: Entry} and {normalized_name: Entry} indexes."""
    by_prog = {}
    by_name = {}
    for e in entries:
        e = _to_entry(e)
        if e.program_number:
            by_prog[e.program_number] = e
        if e.horse_name:
            by_name.setdefault(normalize_horse_name(e.horse_name), e)
    return by_prog, by_name


def _resolve_pick(pick, by_prog, by_name):
    """Resolve a pick dict to an Entry, or None if no match.

    Tries program_number first, then fuzzy horse-name.
    """
    prog = (pick.get("program_number") or "").strip()
    if prog and prog in by_prog:
        return by_prog[prog]
    name = pick.get("horse_name") or ""
    if name:
        norm = normalize_horse_name(name)
        if norm and norm in by_name:
            return by_name[norm]
    return None


def aggregate(entries, picks):
    """Aggregate picks onto entries -> consensus result.

    Args:
        entries: list of Entry objects or dicts (the race's horse list).
        picks:   list of normalized pick dicts from fetch_all_picks().

    Returns:
        dict with:
          - rows: list of consensus rows sorted by points desc, each:
              {program_number, horse_name, points, first_votes, second_votes,
               third_votes, morning_line_odds, per_source: {source: rank}}
          - best_pick: the top row (or None if no picks resolved)
          - confidence: float in [0,1] - share of sources whose #1 == best pick
          - margin: points gap between best and 2nd (0 if fewer than 2 rows)
          - num_sources: count of sources that produced >=1 resolved pick
          - unmatched_picks: picks that couldn't be matched to any entry
    """
    by_prog, by_name = _entries_index(entries)

    points = defaultdict(float)
    first_votes = defaultdict(int)
    second_votes = defaultdict(int)
    third_votes = defaultdict(int)
    per_source = defaultdict(dict)  # entry_key -> {source: rank}
    sources_seen = set()
    matched_keys = set()
    unmatched = []

    # Stable key for an entry: program_number if present else normalized name
    def _key(entry):
        if entry.program_number:
            return f"prog:{entry.program_number}"
        return f"name:{normalize_horse_name(entry.horse_name)}"

    key_to_entry = {}
    for e in entries:
        e = _to_entry(e)
        key_to_entry[_key(e)] = e

    for p in picks:
        entry = _resolve_pick(p, by_prog, by_name)
        if entry is None:
            unmatched.append(p)
            continue
        k = _key(entry)
        rank = p.get("rank")
        src = p.get("source", "?")
        sources_seen.add(src)
        matched_keys.add(k)

        pts = RANK_POINTS.get(rank)
        if pts is None:
            if rank is None:
                # Unranked mention (no rank at all) - score as a 1st-place vote.
                pts = PTS_FIRST
                rank = 1
            else:
                # Explicit rank beyond the point scheme (e.g. a 4th choice) -
                # zero points and no vote. It must NOT be promoted to a
                # first-place vote: a deep-ballot mention is a lukewarm
                # endorsement, not a win selection.
                pts = 0
        points[k] += pts
        if rank == 1:
            first_votes[k] += 1
        elif rank == 2:
            second_votes[k] += 1
        elif rank == 3:
            third_votes[k] += 1
        # Keep the best (lowest) rank a source assigned to this horse
        prev = per_source[k].get(src)
        if prev is None or rank < prev:
            per_source[k][src] = rank

    # Build rows for every entry, sorted by points then tiebreaks
    all_keys = [k for k in key_to_entry.keys()]
    keyed = []
    for k in all_keys:
        e = key_to_entry[k]
        keyed.append({
            "key": k,
            "program_number": e.program_number,
            "horse_name": e.horse_name,
            "points": points.get(k, 0),
            "first_votes": first_votes.get(k, 0),
            "second_votes": second_votes.get(k, 0),
            "third_votes": third_votes.get(k, 0),
            "morning_line_odds": e.morning_line_odds,
            "per_source": dict(per_source.get(k, {})),
        })

    keyed.sort(key=lambda r: (
        -r["points"],
        -r["first_votes"],
        (r["morning_line_odds"] if r["morning_line_odds"] is not None else 9999),
        r["program_number"],
    ))

    best = keyed[0] if keyed and keyed[0]["points"] > 0 else None
    num_sources = len(sources_seen)
    confidence = 0.0
    margin = 0.0
    if best and num_sources > 0:
        confidence = best["first_votes"] / num_sources
        if len(keyed) > 1:
            margin = best["points"] - keyed[1]["points"]

    # Strip internal "key" from rows for output cleanliness
    for r in keyed:
        r.pop("key", None)

    return {
        "rows": keyed,
        "best_pick": best,
        "confidence": confidence,
        "margin": margin,
        "num_sources": num_sources,
        "unmatched_picks": unmatched,
    }


def format_table(result, max_rows=None):
    """Render a consensus result as a console-friendly table string."""
    lines = []
    rows = result["rows"]
    if max_rows:
        rows = rows[:max_rows]

    header = f"{'#':>2} {'Prog':<5} {'Horse':<22} {'Pts':>4} {'#1':>3} {'#2':>3} {'#3':>3} {'MLO':>6}"
    lines.append(header)
    lines.append("-" * len(header))
    for i, r in enumerate(rows, 1):
        mlo = f"{r['morning_line_odds']:.1f}" if r["morning_line_odds"] is not None else "-"
        lines.append(
            f"{i:>2} {r['program_number'] or '-':<5} {r['horse_name'][:22]:<22} "
            f"{r['points']:>4} {r['first_votes']:>3} {r['second_votes']:>3} "
            f"{r['third_votes']:>3} {mlo:>6}"
        )
    return "\n".join(lines)