"""
Expert-vs-baseline accuracy tracker — a running tally SEPARATE from the
weekly backtest report.

The backtest report (report.py) grades the naive baselines against each other
over a date window. This module instead grades the human-expert pick sources
(DRF/Ehlers, UltimateCapper, IrishRacing, BettingNews, etc.) against the
baselines on the set of races where at least one expert has stored picks — an
apples-to-apples comparison on an identical race set, accumulated over time as
more cards get predicted + scored.

It also backfills the canonical baseline slate onto any expert-scored race
missing it (recomputed from stored entries) so the baseline side is consistent
across every race in the tally, regardless of which session saved the experts.

Usage:
  python horse_race_predictor/expert_tracker.py            # print + write HTML
  python horse_race_predictor/expert_tracker.py --no-html   # console only
"""

import argparse
import html as _html
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import accuracy as accuracy_mod
import mlo_baseline
import mlo_variants
import post_position_baseline
import random_baseline
from race import filter_active

BET_UNIT = 2.0

# Canonical naive baselines (the same slate the backtest uses). These are
# recomputed from the stored entry sheet for any expert-scored race missing them.
CANONICAL_BASELINES = [
    ("mlo_baseline", mlo_baseline.predict, {}),
    ("mlo_second", mlo_variants.predict_second, {}),
    ("mlo_third", mlo_variants.predict_third, {}),
    ("mlo_longshot", mlo_variants.predict_longshot, {}),
    ("post_position_baseline", post_position_baseline.predict, {}),
    ("post_position_outside", post_position_baseline.predict_outside, {}),
    ("random_baseline", random_baseline.predict, {}),
]
# Legacy label a prior session used for the MLO favorite before the canonical
# `mlo_baseline` name. It's the same predictor — fold it into mlo_baseline in
# the tally rather than showing a duplicate row.
LEGACY_ALIASES = {"mlo_favorite": "mlo_baseline"}
# `consensus` is auto-scored by run_accuracy_checks from stored picks; treat it
# as a baseline (it's the rank-point blend, not a human expert).
CONSENSUS_SOURCE = "consensus"
# Connection baselines (leading jockey / trainer) are computed from meet
# standings, not human picks — also baselines, not experts.
CONNECTION_BASELINES = {"leading_jockey", "leading_trainer"}
BASELINE_DISPLAY = {b[0] for b in CANONICAL_BASELINES} | {CONSENSUS_SOURCE}


def _expert_sources():
    """All pick sources that are neither a canonical baseline, a connection
    baseline, consensus, nor a legacy alias — i.e. the human-expert / site pick
    sources."""
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT source FROM picks")
    all_src = {r["source"] for r in cur.fetchall()}
    conn.close()
    baseline_set = ({b[0] for b in CANONICAL_BASELINES} | set(LEGACY_ALIASES)
                    | {CONSENSUS_SOURCE} | CONNECTION_BASELINES)
    return sorted(all_src - baseline_set)


def _expert_races(expert_sources):
    """Races that have at least one expert pick (the comparison set)."""
    conn = db.get_connection()
    cur = conn.cursor()
    ph = ",".join("?" * len(expert_sources))
    cur.execute(
        f"SELECT DISTINCT r.id, r.track_code, r.race_date, r.race_number "
        f"FROM races r JOIN picks p ON p.race_id=r.id "
        f"WHERE p.source IN ({ph}) "
        f"ORDER BY r.race_date, r.track_code, r.race_number",
        tuple(expert_sources),
    )
    return cur.fetchall()


def ensure_baselines(race_id):
    """Recompute + save any canonical baseline missing on this race, then
    re-score (run_accuracy_checks also refreshes the consensus snapshot)."""
    entries = db.get_entries(race_id)
    active, _ = filter_active(entries)
    if not active:
        return
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT source FROM picks WHERE race_id=?", (race_id,))
    have = {r["source"] for r in cur.fetchall()}
    conn.close()
    race = db.get_race(race_id)
    seed = f"{race['track_code']}-{race['race_number']}-{race['race_date']}"
    for name, fn, kw in CANONICAL_BASELINES:
        if name in have:
            continue
        kw = dict(kw)
        if name == "random_baseline":
            kw["seed_key"] = seed
        db.save_picks(race_id, name, fn(active, **kw))
    accuracy_mod.run_accuracy_checks(race_id)


def _wps_payoff(winner_payoff, hit_win):
    """$2 win bet P/L on a top pick: win -> payoff - 2, loss -> -2."""
    if hit_win and winner_payoff is not None:
        return winner_payoff - BET_UNIT
    return -BET_UNIT


def tally(races):
    """Per-source W/P/S hits + $2 win ROI across the given race set.

    Returns (per_source, per_race) where per_source maps source -> aggregate
    dict and per_race is a list of {track, date, race_number, winner, pay,
    picks: {source: (top_pick, finish, hit_w, hit_p, hit_s, pl)}}.
    """
    per_source = defaultdict(lambda: {"races": 0, "wins": 0, "places": 0,
                                       "shows": 0, "wagered": 0.0, "pl": 0.0})
    per_race = []
    for row in races:
        race_id = row["id"]
        results = db.get_results(race_id)
        winner = next((r for r in results if r.get("finish_position") == 1), None)
        win_pay = winner.get("win_payoff") if winner else None
        order = {r.get("program_number"): r.get("finish_position") for r in results}
        snaps = {s["source"]: s for s in db.get_accuracy_snapshots(race_id)}
        # Canonicalize legacy aliases -> canonical baseline label.
        canon = {}
        for src, snap in snaps.items():
            canon[LEGACY_ALIASES.get(src, src)] = snap

        race_entry = {
            "track": row["track_code"], "date": row["race_date"],
            "race_number": row["race_number"],
            "winner": (winner["horse_name"], winner.get("program_number"), win_pay) if winner else None,
            "picks": {},
        }
        for src, snap in canon.items():
            hw, hp, hs = snap.get("hit_win") or 0, snap.get("hit_place") or 0, snap.get("hit_show") or 0
            pl = _wps_payoff(win_pay, hw)
            a = per_source[src]
            a["races"] += 1
            a["wins"] += hw
            a["places"] += hp
            a["shows"] += hs
            if win_pay is not None and snap.get("hit_win") is not None:
                a["wagered"] += BET_UNIT
                a["pl"] += pl
            race_entry["picks"][src] = {
                "top_pick": snap.get("top_pick_horse"), "finish": snap.get("finish_position"),
                "hit_win": hw, "hit_place": hp, "hit_show": hs, "pl": pl,
            }
        per_race.append(race_entry)
    return per_source, per_race


def print_report(per_source, per_race, expert_sources):
    n = len(per_race)
    print(f"\n=== Expert-vs-Baseline tracker ({n} races) ===\n")
    experts = [s for s in expert_sources if s in per_source]
    baselines = [s for s in (BASELINE_DISPLAY - {CONSENSUS_SOURCE}) if s in per_source] + (
        [CONSENSUS_SOURCE] if CONSENSUS_SOURCE in per_source else [])

    def row(src):
        a = per_source[src]
        roi = (a["pl"] / a["wagered"] * 100) if a["wagered"] else 0.0
        wr = (a["wins"] / a["races"] * 100) if a["races"] else 0.0
        return (src, a["races"], a["wins"], a["places"], a["shows"], a["pl"], roi, wr)

    def print_section(title, srcs):
        print(f"--- {title} ---")
        print(f"  {'source':<22} {'races':>5} {'W':>3} {'P':>3} {'S':>3} {'win%':>6} {'$2 PL':>9} {'ROI':>8}")
        for src, races, w, p, s, pl, roi, wr in sorted(srcs, key=lambda x: x[6], reverse=True):
            print(f"  {src:<22} {races:>5} {w:>3} {p:>3} {s:>3} {wr:>5.1f}% {pl:>+8.2f} {roi:>+7.1f}%")
        print()

    print_section("EXPERTS", [row(s) for s in experts])
    print_section("BASELINES", [row(s) for s in baselines])


def generate_html(per_source, per_race, expert_sources, out_path):
    """Write a standalone expert-vs-baseline HTML report (separate from the
    backtest report)."""
    experts = [s for s in expert_sources if s in per_source]
    baselines = [s for s in (BASELINE_DISPLAY - {CONSENSUS_SOURCE}) if s in per_source]
    if CONSENSUS_SOURCE in per_source:
        baselines.append(CONSENSUS_SOURCE)

    def esc(s):
        return _html.escape(str(s)) if s is not None else ""

    def source_rows(srcs):
        out = []
        for src in sorted(srcs, key=lambda s: per_source[s]["pl"], reverse=True):
            a = per_source[src]
            roi = (a["pl"] / a["wagered"] * 100) if a["wagered"] else 0.0
            wr = (a["wins"] / a["races"] * 100) if a["races"] else 0.0
            roi_cls = "pos" if roi > 0 else "neg"
            out.append(
                f"<tr><td>{esc(src)}</td><td class='num'>{a['races']}</td>"
                f"<td class='num'>{a['wins']}</td><td class='num'>{a['places']}</td>"
                f"<td class='num'>{a['shows']}</td><td class='num'>{wr:.1f}%</td>"
                f"<td class='num {roi_cls}'>{a['pl']:+.2f}</td>"
                f"<td class='num {roi_cls}'>{roi:+.1f}%</td></tr>"
            )
        return "\n".join(out)

    def wps_cls(w, p, s):
        if w:
            return "hit"
        if p:
            return "place"
        if s:
            return "show"
        return "miss"

    race_rows = []
    for r in per_race:
        w = r["winner"]
        if w:
            pay = f" ({w[2]:.2f})" if w[2] is not None else ""
            win_lbl = f"#{esc(w[1])} {esc(w[0])}{pay}"
        else:
            win_lbl = "—"
        cells = [f"<td class='card'>{esc(r['track'])} {r['date'][5:]} R{r['race_number']}</td>",
                 f"<td>{win_lbl}</td>"]
        # show each expert + baseline top pick -> finish
        all_src = experts + baselines
        for src in all_src:
            pk = r["picks"].get(src)
            if pk is None:
                cells.append("<td class='dim'>—</td>")
            else:
                fin = pk["finish"] if pk["finish"] is not None else "off"
                cls = wps_cls(pk["hit_win"], pk["hit_place"], pk["hit_show"])
                cells.append(f"<td class='{cls}'><b>{esc(pk['top_pick'])}</b> → {fin}</td>")
        race_rows.append("<tr>" + "".join(cells) + "</tr>")
    race_header = "<tr><th>Card</th><th>Winner ($2 win)</th>" + "".join(
        f"<th>{esc(s)}</th>" for s in experts + baselines) + "</tr>"

    doc = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Expert vs Baseline Tracker</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 20px; color: #1a1a1a; }}
 h1 {{ font-size: 1.3em; }} h2 {{ font-size: 1.05em; margin-top: 1.4em; border-bottom: 1px solid #ddd; }}
 table {{ border-collapse: collapse; margin: 8px 0; }}
 th, td {{ border: 1px solid #ddd; padding: 4px 8px; white-space: nowrap; }}
 th {{ background: #f4f6f8; text-align: left; }}
 td.num, th.num {{ text-align: right; }}
 td.card {{ font-weight: bold; background: #eef2f7; }}
 td.hit {{ background: #d4edda; }} td.place {{ background: #fff3cd; }}
 td.show {{ background: #e8f4fd; }} td.miss, td.dim {{ color: #999; }}
 .pos {{ color: #1a7f37; }} .neg {{ color: #cf222e; }}
 .dim {{ color: #999; font-size: 0.85em; }}
 .legend {{ font-size: 0.85em; color: #666; }}
</style></head><body>
<h1>Expert vs Baseline Tracker</h1>
<p class='legend'>Running tally of human-expert pick sources vs naive baselines on the set
of races where at least one expert has stored picks. Apples-to-apples on an identical
race set, accumulated as cards are predicted + scored. Separate from the weekly
backtest report. <b>Win/Place/Show</b> = top pick finished 1st / 1st-2nd / 1st-3rd.
<b>ROI</b> = $2 win bet on each source's top pick.</p>

<h2>Experts</h2>
<table><tr><th>source</th><th class='num'>races</th><th class='num'>W</th>
<th class='num'>P</th><th class='num'>S</th><th class='num'>win%</th>
<th class='num'>$2 PL</th><th class='num'>ROI</th></tr>
{source_rows(experts)}</table>

<h2>Baselines</h2>
<table><tr><th>source</th><th class='num'>races</th><th class='num'>W</th>
<th class='num'>P</th><th class='num'>S</th><th class='num'>win%</th>
<th class='num'>$2 PL</th><th class='num'>ROI</th></tr>
{source_rows(baselines)}</table>

<h2>Per-race picks → finish</h2>
<table>{race_header}
{chr(10).join(race_rows)}</table>
<p class='legend'>Cells: <b>top pick</b> → finish position (off = off the board).
Green = win, yellow = place, blue = show, grey = off board / no pick.</p>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


def main():
    ap = argparse.ArgumentParser(prog="expert_tracker.py",
                                  description="Expert-vs-baseline accuracy tally.")
    ap.add_argument("--no-html", action="store_true", help="Skip the HTML report.")
    ap.add_argument("--html", default=None,
                    help="HTML path (default: reports/expert_tracker.html).")
    args = ap.parse_args()

    db.init_db()
    expert_sources = _expert_sources()
    races = _expert_races(expert_sources)
    print(f"Expert-scored races: {len(races)} across "
          f"{len({r['track_code'] for r in races})} tracks, "
          f"{len({r['race_date'] for r in races})} dates.")
    print(f"Expert sources: {expert_sources}\n")

    # Backfill canonical baselines on any expert race missing them, re-score.
    filled = 0
    for row in races:
        before = len(db.get_picks(row["id"]))
        ensure_baselines(row["id"])
        after = len(db.get_picks(row["id"]))
        if after > before:
            filled += 1
    if filled:
        print(f"Backfilled canonical baselines on {filled} race(s).\n")

    per_source, per_race = tally(races)
    print_report(per_source, per_race, expert_sources)

    if not args.no_html:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        os.makedirs(out_dir, exist_ok=True)
        out_path = args.html or os.path.join(out_dir, "expert_tracker.html")
        generate_html(per_source, per_race, expert_sources, out_path)
        print(f"\nHTML report: {out_path}")


if __name__ == "__main__":
    main()