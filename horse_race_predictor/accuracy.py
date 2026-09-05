"""
Accuracy reconciliation for Horse Race Predictor.

Once official results are stored for a race, score each source's top pick
(win/place/show hit) and the consensus best pick, persisting snapshots to the
DB. Also provides a summary aggregation across all scored races.

A source's "top pick" is its lowest-rank pick (rank 1 preferred). The consensus
pick is recomputed from stored entries + picks via the consensus engine.
"""

from collections import defaultdict

import db
import consensus as consensus_mod
from race import normalize_horse_name


def _finish_lookup(results):
    """Build (by_prog, by_name) maps from program_number / horse_name -> finish_position."""
    by_prog = {}
    by_name = {}
    for r in results:
        fin = r.get("finish_position")
        if fin is None:
            continue
        if r.get("program_number"):
            by_prog[r["program_number"]] = fin
        if r.get("horse_name"):
            by_name.setdefault(normalize_horse_name(r["horse_name"]), fin)
    return by_prog, by_name


def _resolve_finish(pick, by_prog, by_name):
    """Look up a pick's finish position by program number, else fuzzy name."""
    prog = (pick.get("program_number") or "").strip()
    if prog and prog in by_prog:
        return by_prog[prog]
    name = pick.get("horse_name") or ""
    if name:
        fin = by_name.get(normalize_horse_name(name))
        if fin is not None:
            return fin
    return None


def _hit_flags(finish):
    """Return (hit_win, hit_place, hit_show) for a finish position (None-safe)."""
    if finish is None:
        return 0, 0, 0
    return (1 if finish == 1 else 0,
            1 if finish <= 2 else 0,
            1 if finish <= 3 else 0)


def run_accuracy_checks(race_id, conn=None):
    """Score every source's top pick + the consensus pick for one race.

    Reads entries/picks/results from the DB, writes accuracy_snapshots, and
    returns a list of snapshot dicts (one per source + one for "consensus").
    Requires results to be stored; returns [] otherwise. All snapshot upserts
    for the race go out in one batch commit; pass `conn=` to reuse a
    connection across many races in a loop.
    """
    with db.connect(conn) as c:
        entries = db.get_entries(race_id, conn=c)
        picks = db.get_picks(race_id, conn=c)
        results = db.get_results(race_id, conn=c)
        if not results:
            return []

        by_prog, by_name = _finish_lookup(results)
        snapshots = []
        snapshot_rows = []  # batched upsert rows (single commit at the end)

        # Per source: top pick = lowest rank (rank 1 first)
        by_src = defaultdict(list)
        for p in picks:
            by_src[p["source"]].append(p)

        for src, ps in by_src.items():
            top = min(ps, key=lambda p: (p["rank"] if p["rank"] is not None else 99))
            fin = _resolve_finish(top, by_prog, by_name)
            hw, hp, hs = _hit_flags(fin)
            snapshot_rows.append((race_id, src, top["horse_name"], fin, hw, hp, hs))
            snapshots.append({
                "source": src, "top_pick": top["horse_name"], "finish": fin,
                "hit_win": hw, "hit_place": hp, "hit_show": hs,
            })

        # Consensus pick
        result = consensus_mod.aggregate(entries, picks)
        if result["best_pick"]:
            bp = result["best_pick"]
            fin = _resolve_finish(bp, by_prog, by_name)
            hw, hp, hs = _hit_flags(fin)
            snapshot_rows.append((race_id, "consensus", bp["horse_name"], fin, hw, hp, hs))
            snapshots.append({
                "source": "consensus", "top_pick": bp["horse_name"], "finish": fin,
                "hit_win": hw, "hit_place": hp, "hit_show": hs,
            })

        if snapshot_rows:
            db.save_accuracy_snapshots(snapshot_rows, conn=c)

    return snapshots


def recompute_all():
    """Recompute accuracy snapshots for every stored race that has results."""
    scored = db.get_scored_races()
    total = 0
    for r in scored:
        snaps = run_accuracy_checks(r["id"])
        if snaps:
            total += 1
    return total


def summary():
    """Aggregate accuracy across all scored races, grouped by source.

    Returns a list of dicts: {source, races, wins, places, shows,
    win_rate, place_rate, show_rate} sorted by win_rate desc.
    """
    snaps = db.get_accuracy_snapshots()
    by_src = defaultdict(lambda: {"races": 0, "wins": 0, "places": 0, "shows": 0})
    for s in snaps:
        agg = by_src[s["source"]]
        agg["races"] += 1
        agg["wins"] += s["hit_win"]
        agg["places"] += s["hit_place"]
        agg["shows"] += s["hit_show"]

    rows = []
    for src, agg in by_src.items():
        n = agg["races"] or 1
        rows.append({
            "source": src,
            "races": agg["races"],
            "wins": agg["wins"],
            "places": agg["places"],
            "shows": agg["shows"],
            "win_rate": agg["wins"] / n,
            "place_rate": agg["places"] / n,
            "show_rate": agg["shows"] / n,
        })
    rows.sort(key=lambda r: (-r["win_rate"], -r["wins"], r["source"]))
    return rows


def format_summary(rows):
    """Render the summary rows as a console-friendly table string."""
    lines = []
    header = (f"{'Source':<16} {'Races':>5} {'Win':>5} {'Plc':>5} {'Shw':>5} "
              f"{'Win%':>6} {'Plc%':>6} {'Shw%':>6}")
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        lines.append(
            f"{r['source']:<16} {r['races']:>5} {r['wins']:>5} {r['places']:>5} "
            f"{r['shows']:>5} {r['win_rate']*100:>5.0f}% {r['place_rate']*100:>5.0f}% "
            f"{r['show_rate']*100:>5.0f}%"
        )
    return "\n".join(lines)