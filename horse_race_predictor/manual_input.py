"""
Manual input parsing for Horse Race Predictor.

Lets the user supply the race field (entries) and expert picks directly, sidestepping
the bot-walled / JS-rendered free racing sites. Two input styles:

1. Structured JSON file (--input FILE):
   {
     "track": "SAR",
     "race": 1,
     "date": "2026-07-04",
     "entries": [
       {"program_number": "1", "horse_name": "Speed Star", "morning_line_odds": 5.0,
        "jockey": "J. Ortiz", "trainer": "T. Pletcher", "post_position": 1},
       ...
     ],
     "picks": [
       {"source": "drf_free", "picks": [
           {"program_number": "1", "rank": 1},
           {"program_number": "3", "rank": 2},
           {"program_number": "2", "rank": 3}
       ]},
       {"source": "my_handicap", "picks": [
           {"horse_name": "Speed Star", "rank": 1}
       ]}
     ]
   }

2. Compact inline (--field "..." --picks "..."):
   --field "1:Speed Star:5/2, 2:Lazy Day:3/1, 3:Midnight Run:2/1"
       prog:name:mlo  (mlo optional; accepts "5/2", "5-2", or "5.0")
   --picks "drf_free:1,3,2 | abr:3,1,2 | my_tip:Speed Star,2"
       source:tok1,tok2,tok3  (rank by order; each tok matches a program
       number first, else a horse name)

Returns normalized entry/pick dicts compatible with the consensus engine and DB.
"""

import json
import re


# ── compact inline parsers ───────────────────────────────────────────────

def _parse_mlo(token):
    """Parse a morning-line odds token: '5/2', '5-2', or '5.0' -> float."""
    token = token.strip()
    if not token:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[/\-]\s*(\d+(?:\.\d+)?)$", token)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return round(num / den, 2) if den else 0.0
    try:
        return float(token)
    except ValueError:
        return None


def parse_field(s):
    """Parse a compact --field string into a list of entry dicts.

    Format: comma- or newline-separated `prog:name[:mlo]` tokens.
    """
    entries = []
    for raw in re.split(r"[,\n]", s):
        raw = raw.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(":", 2)]
        if len(parts) < 2:
            print(f"    [manual] Skipping malformed field token: {raw!r}")
            continue
        prog, name = parts[0], parts[1]
        mlo = _parse_mlo(parts[2]) if len(parts) > 2 else None
        post = None
        m = re.match(r"^(\d{1,2})", prog)
        if m:
            post = int(m.group(1))
        entries.append({
            "program_number": prog,
            "horse_name": name,
            "jockey": "",
            "trainer": "",
            "morning_line_odds": mlo,
            "post_position": post,
            "scratched": False,
        })
    return entries


def parse_picks(s, entries):
    """Parse a compact --picks string into a list of normalized pick dicts.

    Format: `source:tok1,tok2,tok3 | source:...`. Each token matches an entry
    by program number first, then by horse name (fuzzy). Rank is by order.
    """
    by_prog = {e["program_number"]: e for e in entries if e.get("program_number")}
    from race import normalize_horse_name
    by_name = {normalize_horse_name(e["horse_name"]): e
               for e in entries if e.get("horse_name")}

    picks = []
    for src_block in s.split("|"):
        src_block = src_block.strip()
        if not src_block or ":" not in src_block:
            continue
        source, rest = src_block.split(":", 1)
        source = source.strip()
        rank = 0
        for tok in [t.strip() for t in rest.split(",")]:
            if not tok:
                continue
            rank += 1
            if rank > 3:
                break
            entry = by_prog.get(tok)
            if entry is None:
                norm = normalize_horse_name(tok)
                entry = by_name.get(norm)
            if entry is None:
                print(f"    [manual] {source}: token {tok!r} matched no entry - skipping")
                continue
            picks.append({
                "source": source,
                "horse_name": entry["horse_name"],
                "program_number": entry.get("program_number", ""),
                "rank": rank,
                "comment": "",
                "raw_data": tok,
            })
    return picks


# ── structured JSON file ─────────────────────────────────────────────────

def load_input_file(path):
    """Load a structured JSON input file -> (race_meta, entries, picks, results).

    race_meta is a dict with optional track/race/date keys.
    picks is a flat list of normalized pick dicts (rank by order within each
    source's "picks" list if rank omitted).
    results is a list of normalized result dicts if a "results" key is present
    (finish order + optional payoffs), else [].
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    race_meta = {
        "track": data.get("track"),
        "race": data.get("race"),
        "date": data.get("date"),
    }

    entries = []
    for e in data.get("entries", []):
        entries.append({
            "program_number": str(e.get("program_number", "")),
            "horse_name": e.get("horse_name", ""),
            "jockey": e.get("jockey", ""),
            "trainer": e.get("trainer", ""),
            "morning_line_odds": e.get("morning_line_odds"),
            "post_position": e.get("post_position"),
            "scratched": bool(e.get("scratched", False)),
        })

    # Index entries so picks given by program_number alone can be back-filled
    # with horse_name (and vice versa). This keeps the DB's UNIQUE(race_id,
    # source, horse_name) constraint satisfiable when a source omits names.
    from race import normalize_horse_name
    by_prog = {e["program_number"]: e for e in entries if e.get("program_number")}
    by_name = {normalize_horse_name(e["horse_name"]): e
               for e in entries if e.get("horse_name")}

    picks = []
    for src in data.get("picks", []):
        source = src.get("source", "manual")
        rank = 0
        for p in src.get("picks", []):
            rank += 1
            if rank > 3:
                break
            prog = str(p.get("program_number", ""))
            name = p.get("horse_name", "")
            # Back-fill missing name/number from entries
            if not name and prog and prog in by_prog:
                name = by_prog[prog]["horse_name"]
            if not prog and name:
                ent = by_name.get(normalize_horse_name(name))
                if ent:
                    prog = ent["program_number"]
            picks.append({
                "source": source,
                "horse_name": name,
                "program_number": prog,
                "rank": p.get("rank", rank),
                "comment": p.get("comment", ""),
                "raw_data": name or prog,
            })

    # Optional results section: either an ordered list of program numbers
    # (finish order) or a list of result dicts with explicit finish_position.
    results = _parse_results_section(data.get("results", []), by_prog, by_name)
    return race_meta, entries, picks, results


def _parse_results_section(raw, by_prog, by_name):
    """Normalize a JSON "results" section into result dicts.

    Accepts either:
      - ["2", "1", "3"]  (program numbers in finish order; payoffs omitted), or
      - [{"program_number": "2", "finish_position": 1, "win_payoff": 6.0, ...}, ...]
    Horse names are back-filled from the entries index when only a number is given.
    """
    if not raw:
        return []
    results = []
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        for i, prog in enumerate(raw, 1):
            prog = str(prog).strip()
            ent = by_prog.get(prog)
            results.append({
                "program_number": prog,
                "horse_name": ent["horse_name"] if ent else "",
                "finish_position": i,
                "win_payoff": None, "place_payoff": None, "show_payoff": None,
            })
        return results
    for r in raw:
        prog = str(r.get("program_number", ""))
        name = r.get("horse_name", "")
        if not name and prog and prog in by_prog:
            name = by_prog[prog]["horse_name"]
        results.append({
            "program_number": prog,
            "horse_name": name,
            "finish_position": r.get("finish_position"),
            "win_payoff": r.get("win_payoff"),
            "place_payoff": r.get("place_payoff"),
            "show_payoff": r.get("show_payoff"),
        })
    return results


def parse_finish(s, entries):
    """Parse a compact --finish string into result dicts.

    Format: comma-separated program numbers in finish order, e.g. "2,1,3".
    Horse names are resolved from the entries list. Finish position is the
    1-based index in the string.
    """
    by_prog = {e["program_number"]: e for e in entries if e.get("program_number")}
    results = []
    for i, tok in enumerate([t.strip() for t in s.split(",")], 1):
        if not tok:
            continue
        ent = by_prog.get(tok)
        results.append({
            "program_number": tok,
            "horse_name": ent["horse_name"] if ent else "",
            "finish_position": i,
            "win_payoff": None, "place_payoff": None, "show_payoff": None,
        })
    return results