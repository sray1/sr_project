"""
HTML accuracy report generator for the weekly backtest.

Queries the DB for scored races in a date range, aggregates win/place/show
accuracy and ROI for every stored predictor (the MLO baseline plus the
post-position and random comparison baselines, and the consensus blend), and
renders a standalone HTML report. The report is both written to a file and
persisted to the DB reports table.

The report is ASCII-safe inside <pre>/<td> cells for the few dynamic values that
could contain non-ASCII horse names; the file itself is UTF-8.
"""

import html as _html
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db

REPORT_KEY = "weekly_accuracy"
TRACK_NAMES = {
    # NYRA
    "AQU": "Aqueduct", "BEL": "Belmont Park", "SAR": "Saratoga",
    # Florida
    "GP": "Gulfstream Park", "TAM": "Tampa Bay Downs",
    # California
    "SA": "Santa Anita Park", "DMR": "Del Mar",
    # Kentucky
    "CD": "Churchill Downs", "KEE": "Keeneland", "ELP": "Ellis Park", "TP": "Turfway Park",
    # Arkansas
    "OP": "Oaklawn Park",
    # Louisiana
    "FG": "Fair Grounds", "EVD": "Evangeline Downs", "DTA": "Delta Downs",
    # New Jersey
    "MTH": "Monmouth Park",
    # Maryland
    "PIM": "Pimlico", "LRL": "Laurel Park",
    # Pennsylvania
    "PRX": "Parx Racing",
    # Virginia
    "COL": "Colonial Downs",
    # Delaware
    "DEL": "Delaware Park",
    # Minnesota
    "CBY": "Canterbury Park",
    # Illinois
    "HTH": "Hawthorne",
    # Indiana
    "IND": "Horseshoe Indianapolis",
    # Ohio
    "TDP": "Thistledown", "PID": "Presque Isle Downs",
    # West Virginia
    "MNR": "Mountaineer", "MVG": "Mahoning Valley",
    # New York (other)
    "FL": "Finger Lakes",
    # Texas
    "HOU": "Sam Houston", "LSA": "Lone Star", "RP": "Remington Park",
    # Canada
    "WO": "Woodbine",
}
# Display labels for each predictor source in the comparison table.
SOURCE_LABELS = {
    "mlo_baseline": "MLO favorite",
    "mlo_second": "MLO 2nd-choice",
    "mlo_third": "MLO 3rd-choice",
    "mlo_longshot": "MLO longshot",
    "post_position_baseline": "Post position (in)",
    "post_position_outside": "Post position (out)",
    "random_baseline": "Random",
    "leading_jockey": "Leading jockey",
    "leading_trainer": "Leading trainer",
    "consensus": "Consensus (blend)",
}
# The predictor the per-race detail table is built around.
PRIMARY_SOURCE = "mlo_baseline"
# A $2 win bet is the unit for ROI / per-race P/L.
BET_UNIT = 2.0


def generate(start_date, end_date, tracks, timings=None):
    """Build the HTML report string for scored races in [start, end] for `tracks`.

    `timings` (optional) is a dict of pipeline phase durations in seconds; if
    provided, a Timings section is rendered and a line is appended to
    reports/timings.log. Returns the HTML string.
    """
    db.init_db()
    tracks = [t.upper() for t in tracks]

    scored = [r for r in db.get_scored_races()
              if start_date <= r["race_date"] <= end_date
              and r["track_code"] in tracks]

    per_race = []
    # per_source: source -> aggregate counts + ROI accumulator.
    per_source = defaultdict(lambda: {
        "races": 0, "wins": 0, "places": 0, "shows": 0, "pl": 0.0, "wagered": 0.0,
    })
    roi_race_count = 0  # races with winner win_payoff available (ROI-eligible)

    for r in scored:
        race_id = r["id"]
        results = db.get_results(race_id)
        # Winner's $2 win payoff (only stored for Equibase-filled races; BloodHorse
        # top-3 finishers carry no mutuel payoffs).
        winner_payoff = None
        actual_winner = ""
        for res in results:
            if res.get("finish_position") == 1:
                winner_payoff = res.get("win_payoff")
                actual_winner = res.get("horse_name") or ""
                break

        snaps = {s["source"]: s for s in db.get_accuracy_snapshots(race_id)}
        # Accumulate every source's hit counts + ROI.
        for src, snap in snaps.items():
            a = per_source[src]
            a["races"] += 1
            a["wins"] += snap.get("hit_win") or 0
            a["places"] += snap.get("hit_place") or 0
            a["shows"] += snap.get("hit_show") or 0
            # ROI on a $2 win bet on this source's top pick:
            #   won  -> payoff - 2 (profit)
            #   lost -> -2 (lost the stake)
            # Only computable when the winner's win_payoff is stored.
            if winner_payoff is not None and snap.get("hit_win") is not None:
                a["wagered"] += BET_UNIT
                a["pl"] += (winner_payoff - BET_UNIT) if snap.get("hit_win") else -BET_UNIT

        if winner_payoff is not None:
            roi_race_count += 1

        # Per-race row is built around the primary predictor (MLO baseline).
        mlo = snaps.get(PRIMARY_SOURCE)
        if not mlo:
            continue
        # Predicted horse's MLO: look up the rank-1 mlo_baseline pick, then its entry.
        picks = db.get_picks(race_id, PRIMARY_SOURCE)
        top = min((p for p in picks if p.get("rank")), key=lambda p: p["rank"], default=None)
        mlo_val = None
        if top:
            for e in db.get_entries(race_id):
                if (e.get("program_number") and e.get("program_number") == top.get("program_number")) \
                        or (e.get("horse_name") and e.get("horse_name") == top.get("horse_name")):
                    mlo_val = e.get("morning_line_odds")
                    break
        per_race.append({
            "track": r["track_code"], "race_number": r["race_number"],
            "date": r["race_date"],
            "predicted": mlo.get("top_pick_horse") or "",
            "mlo": mlo_val,
            "actual_winner": actual_winner,
            "winner_payoff": winner_payoff,
            "finish": mlo.get("finish_position"),
            "hit_win": mlo.get("hit_win"), "hit_place": mlo.get("hit_place"),
            "hit_show": mlo.get("hit_show"),
        })
    per_race.sort(key=lambda x: (x["date"], x["track"], x["race_number"]))

    # Aggregates
    overall = _agg(per_race)
    by_track = {}
    for t in tracks:
        rows = [r for r in per_race if r["track"] == t]
        if rows:
            by_track[t] = _agg(rows)

    # Per-source rows sorted by win rate desc (then source name for stability).
    source_rows = []
    for src, a in per_source.items():
        n = a["races"] or 1
        roi = (a["pl"] / a["wagered"]) if a["wagered"] else None
        source_rows.append({
            "source": src, "races": a["races"],
            "wins": a["wins"], "places": a["places"], "shows": a["shows"],
            "win_rate": a["wins"] / n, "place_rate": a["places"] / n,
            "show_rate": a["shows"] / n, "roi": roi,
            "wagered": a["wagered"],
        })
    source_rows.sort(key=lambda r: (-r["win_rate"], -r["wins"], r["source"]))

    return _render(start_date, end_date, tracks, overall, by_track, per_race,
                   source_rows, roi_race_count, timings)


def generate_and_save(start_date, end_date, tracks, html_path=None, timings=None):
    """Generate the report, save to DB + file. Returns (html, path)."""
    html = generate(start_date, end_date, tracks, timings=timings)
    db.save_report(REPORT_KEY, html, period_start=start_date, period_end=end_date)
    if html_path is None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        os.makedirs(out_dir, exist_ok=True)
        html_path = os.path.join(out_dir, f"backtest_{start_date}_{end_date}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    _append_timings_log(start_date, end_date, html_path, timings)
    return html, html_path


def _append_timings_log(start_date, end_date, html_path, timings):
    """Append a one-line timing summary to reports/timings.log (cumulative)."""
    if not timings:
        return
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "timings.log")
    line = (
        f"{start_date}->{end_date} "
        f"e2e={timings.get('e2e', 0):.1f}s "
        f"P1={timings.get('phase1_predict', 0):.1f} "
        f"P2={timings.get('phase2_results', 0):.1f} "
        f"P2b={timings.get('phase2b_fallback', 0):.1f} "
        f"P2c={timings.get('phase2c_connections', 0):.1f} "
        f"P3={timings.get('phase3_report', 0):.1f} "
        f"predicted={timings.get('predicted', 0)} "
        f"scored={timings.get('scored', 0)} "
        f"fallback={timings.get('fallback_filled', 0)}\n"
    )
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


def _agg(rows):
    n = len(rows)
    w = sum(1 for r in rows if r["hit_win"])
    p = sum(1 for r in rows if r["hit_place"])
    s = sum(1 for r in rows if r["hit_show"])
    return {
        "races": n,
        "wins": w, "places": p, "shows": s,
        "win_rate": w / n if n else 0.0,
        "place_rate": p / n if n else 0.0,
        "show_rate": s / n if n else 0.0,
    }


def _pct(x):
    return f"{x*100:.1f}%"


def _roi(x):
    if x is None:
        return "n/a"
    return f"{x*100:+.1f}%"


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _timings_block(timings):
    """Render the Pipeline timings table, or an empty string if no timings."""
    if not timings:
        return ""
    phases = [
        ("Phase 1 - predict (entries + pure baselines)", "phase1_predict"),
        ("Phase 2 - results (HRN direct)", "phase2_results"),
        ("Phase 2b - parse.bot fallback", "phase2b_fallback"),
        ("Phase 2c - connection baselines (standings)", "phase2c_connections"),
        ("Phase 3 - report", "phase3_report"),
    ]
    rows = "\n".join(
        f"      <tr><td>{_esc(label)}</td><td class='num'>{timings.get(key, 0):.1f}s</td></tr>"
        for label, key in phases)
    e2e = timings.get("e2e", 0)
    return f"""<table>
  <tr><th>Step</th><th class='num'>Duration</th></tr>
{rows}
  <tr><td><strong>End-to-end total</strong></td><td class='num'><strong>{e2e:.1f}s</strong></td></tr>
</table>
<p class="meta">Races predicted: {timings.get('predicted', 0)} &middot; scored: {timings.get('scored', 0)} &middot; parse.bot fallback fills: {timings.get('fallback_filled', 0)}. Cumulative timings also appended to <code>reports/timings.log</code>.</p>"""


def _render(start, end, tracks, overall, by_track, per_race, source_rows, roi_race_count, timings=None):
    track_label = ", ".join(TRACK_NAMES.get(t, t) for t in tracks)
    # Per-race rows collapsed on (track, date): one rowspan "Card" cell per card,
    # with each race as a sub-row (R#, Predicted, MLO, Actual, Pay, $2 PL, W/P/S).
    # per_race is already sorted by (date, track, race_number), so same-card races
    # are contiguous.
    rows_html = []
    i = 0
    while i < len(per_race):
        r = per_race[i]
        j = i
        while j < len(per_race) and per_race[j]["date"] == r["date"] \
                and per_race[j]["track"] == r["track"]:
            j += 1
        n = j - i
        card_lbl = f"{TRACK_NAMES.get(r['track'], r['track'])} {r['date'][5:11]}"
        for k in range(i, j):
            rr = per_race[k]
            card_cell = (f"<td class='card' rowspan='{n}'>{_esc(card_lbl)}</td>"
                         if k == i else "")
            rows_html.append(f"      <tr>{card_cell}{_race_data_cells(rr)}</tr>")
        i = j
    rows_html = "\n".join(rows_html) or "      <tr><td colspan='8' class='empty'>No scored races.</td></tr>"

    by_track_rows = []
    for t in tracks:
        a = by_track.get(t)
        if not a:
            by_track_rows.append(
                f"      <tr><td>{_esc(TRACK_NAMES.get(t, t))}</td><td colspan='7' class='empty'>No racing in window.</td></tr>")
            continue
        by_track_rows.append(
            f"      <tr><td>{_esc(TRACK_NAMES.get(t, t))}</td><td class='num'>{a['races']}</td>"
            f"<td class='num'>{a['wins']}</td><td class='num'>{a['places']}</td><td class='num'>{a['shows']}</td>"
            f"<td class='num'>{_pct(a['win_rate'])}</td><td class='num'>{_pct(a['place_rate'])}</td>"
            f"<td class='num'>{_pct(a['show_rate'])}</td></tr>")
    by_track_html = "\n".join(by_track_rows)

    # Per-source comparison rows.
    source_html = []
    for s in source_rows:
        label = SOURCE_LABELS.get(s["source"], s["source"])
        source_html.append(
            f"      <tr><td>{_esc(label)}</td><td class='num'>{s['races']}</td>"
            f"<td class='num'>{s['wins']}</td><td class='num'>{s['places']}</td><td class='num'>{s['shows']}</td>"
            f"<td class='num'>{_pct(s['win_rate'])}</td><td class='num'>{_pct(s['place_rate'])}</td>"
            f"<td class='num'>{_pct(s['show_rate'])}</td>"
            f"<td class='num'>{_roi(s['roi'])}</td></tr>")
    source_html = "\n".join(source_html) or "      <tr><td colspan='9' class='empty'>No sources scored.</td></tr>"

    # Pipeline timings section (omitted if no timings were passed).
    timings_html = _timings_block(timings)

    if roi_race_count == len(per_race):
        roi_note = (f"ROI is computed on a $2 win bet per race, over all {roi_race_count} "
                    f"races (HRN payouts tables supply mutuel payoffs for every scored race).")
    else:
        roi_note = (f"ROI is computed on a $2 win bet per race, over the {roi_race_count} of "
                    f"{len(per_race)} races with mutuel payoff data (HRN payouts tables; "
                    f"parse.bot fallback races may lack payoffs). "
                    f"{len(per_race) - roi_race_count} races have no payoff data and are excluded from ROI.")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Horse Race Predictor - Weekly Accuracy Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; color: #222; }}
  h1, h2 {{ color: #1a3a5c; }}
  .meta {{ color: #555; margin-bottom: 16px; font-size: 14px; }}
  table {{ border-collapse: collapse; width: auto; table-layout: auto; margin: 12px 0 28px; font-size: 13px; }}
  th, td {{ border: 1px solid #ccc; padding: 3px 7px; text-align: left; white-space: nowrap; }}
  td.name, th.name {{ white-space: normal; max-width: 150px; overflow-wrap: anywhere; }}
  th {{ background: #1a3a5c; color: #fff; }}
  td.num, th.num {{ text-align: right; }}
  tr:nth-child(even) td {{ background: #f6f8fa; }}
  td.hit {{ background: #d4f4d4; font-weight: bold; text-align: center; }}
  td.place {{ background: #e6f2d4; font-weight: bold; text-align: center; }}
  td.show {{ background: #f4ecd0; font-weight: bold; text-align: center; }}
  td.miss {{ background: #f8d4d4; text-align: center; }}
  td.race {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
  td.card {{ white-space: nowrap; font-weight: bold; background: #eef2f7; vertical-align: top; }}
  td.empty {{ color: #888; font-style: italic; text-align: center; }}
  td.pos {{ color: #1a7a1a; font-weight: bold; }}
  td.neg {{ color: #a00; font-weight: bold; }}
  .methodology {{ background: #f6f8fa; border-left: 4px solid #1a3a5c; padding: 12px 16px; font-size: 13px; }}
  code {{ background: #eef; padding: 1px 4px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>Horse Race Predictor - Weekly Accuracy Report</h1>
<div class="meta">
  <strong>Window:</strong> {start} to {end} (inclusive)<br>
  <strong>Tracks:</strong> {track_label}<br>
  <strong>Predictors compared:</strong> MLO favorite / 2nd / 3rd / longshot, Post position (in/out), Leading jockey / trainer, Random, Consensus blend<br>
  <strong>Results source:</strong> HRN direct (full finish order + $2 WPS payoffs) + parse.bot fallback<br>
  <strong>Generated:</strong> {os.environ.get('HRP_REPORT_GENERATED_AT', 'see DB reports.generated_at')}
</div>

<h2>Predictor comparison</h2>
<p class="meta">{_esc(roi_note)}</p>
<table>
  <tr><th>Source</th><th class='num'>Races</th><th class='num'>Win</th><th class='num'>Plc</th><th class='num'>Shw</th><th class='num'>Win %</th><th class='num'>Plc %</th><th class='num'>Shw %</th><th class='num'>ROI %</th></tr>
{source_html}
</table>

<h2>Overall accuracy (MLO favorite)</h2>
<table>
  <tr><th>Races scored</th><th>Win hits</th><th>Place hits</th><th>Show hits</th><th>Win %</th><th>Place %</th><th>Show %</th></tr>
  <tr><td class='num'>{overall['races']}</td><td class='num'>{overall['wins']}</td><td class='num'>{overall['places']}</td>
      <td class='num'>{overall['shows']}</td><td class='num'>{_pct(overall['win_rate'])}</td>
      <td class='num'>{_pct(overall['place_rate'])}</td><td class='num'>{_pct(overall['show_rate'])}</td></tr>
</table>

<h2>Per-track breakdown (MLO favorite)</h2>
<table>
  <tr><th>Track</th><th class='num'>Races</th><th class='num'>Win</th><th class='num'>Plc</th><th class='num'>Shw</th><th class='num'>Win %</th><th class='num'>Plc %</th><th class='num'>Shw %</th></tr>
{by_track_html}
</table>

<h2>Per-race detail (MLO favorite)</h2>
<table>
  <tr><th>Card</th><th class='num'>R#</th><th>Predicted</th><th class='num'>MLO</th><th>Actual winner</th><th class='num'>Pay</th><th class='num'>$2 PL</th><th title='Win / Place / Show hits'>W/P/S</th></tr>
{rows_html}
</table>

<h2>Pipeline timings</h2>
{timings_html}
<h2>Methodology</h2>
<div class="methodology">
  <p><strong>Predictors compared.</strong> Ten predictors are scored side by side:
  <ul>
  <li><em>MLO favorite / 2nd / 3rd</em> - the 1st, 2nd, and 3rd-lowest morning-line odds
  horses. The favorite is the track handicapper's win-probability estimate; the 2nd/3rd
  choices probe whether the value lies just off the favorite.</li>
  <li><em>MLO longshot</em> - the <em>highest</em>-MLO horse (longest shot). Directly tests the
  favorite-longshot bias direction: does systematically backing longshots lose more or less
  than backing the favorite?</li>
  <li><em>Post position (in / out)</em> - the lowest (inside) and highest (outside) post. A
  real structural factor at some tracks / distances; the outside variant is the counter-signal.</li>
  <li><em>Leading jockey / trainer</em> - bet the horse ridden (or trained) by the meet's
  winningest jockey / trainer <em>as of this race date</em>, computed from stored results with
  race_date strictly before this race (no look-ahead, no external fetch). Classic "bet the meet
  leader" angles. These abstain (no pick, not scored) for races with no prior data at the track
  (e.g. the first day of the window) or when the leader has no mount, so their "Races" count is
  lower than the pure baselines'.</li>
  <li><em>Random</em> - a deterministic random pick (seeded per race). The chance floor: if
  the other baselines don't beat this, they aren't carrying real signal.</li>
  <li><em>Consensus (blend)</em> - the rank-point aggregate (1st=5, 2nd=3, 3rd=1) across all
  listed baselines. With no external expert picks, this blends naive signals (including
  contradictory ones like favorite vs. longshot), so it is increasingly diluted as predictors
  are added.</li>
  </ul>
  No external expert picks are used (free pick sources are bot-walled / JS-rendered; see the
  going-forward AI-Horse-Picks archiver plan for adding a real expert row in future windows).</p>
  <p><strong>Entries.</strong> Fetched from Horse Racing Nation
  (<code>entries.horseracingnation.com</code>), which is server-rendered HTML (not bot-walled)
  and also supplies morning-line odds, post position, and scratch / MTO / also-eligible status.</p>
  <p><strong>Results.</strong> Finish order and $2 win/place/show payoffs come from Horse
  Racing Nation's entries-results page (<code>entries.horseracingnation.com</code>), the same
  server-rendered source used for entries. Each race's <code>table-payouts</code> block lists the
  top-4 finishers in order with WPS mutuel payoffs - free, unlimited, one page per track/date.
  The parse.bot APIs (BloodHorse bulk top-3, Equibase per-race + payoffs) remain registered as a
  fallback for any race HRN hasn't populated.</p>
  <p><strong>Scoring.</strong> <em>Win</em> = predicted horse finished 1st. <em>Place</em> =
  finished 1st or 2nd. <em>Show</em> = finished 1st, 2nd, or 3rd. Rates are hits / races scored.
  Finish positions 1-4 are resolved from the payouts table; horses finishing &gt; 4 (also-rans)
  are not individually ordered but are not needed for win/place/show scoring.</p>
  <p><strong>ROI.</strong> ROI is the profit/loss on a $2 <em>win</em> bet on each predictor's
  top pick, summed over the races with mutuel payoff data, divided by the total wagered
  ($2 per race). A win pays <code>winner_payoff - 2</code>; a loss costs <code>-2</code>. Note
  that <em>hit rate and ROI tell different stories</em>: betting favorites (MLO) wins most often
  but each win pays little, while longshots win rarely but pay a lot - so a higher win rate does
  not imply a better ROI (the favorite-longshot bias).</p>
  <p><strong>Matching.</strong> HRN results carry the program number directly (the payouts-table
  image alt); scoring keys on program number with a normalized horse-name fallback.</p>
  <p><strong>Limitations.</strong> Tracks with no racing in the window are reported as such.
  HRN's payouts table resolves only the top-4; also-ran order is not captured. The sample is
  small (one week, a few tracks), so ROI figures are noisy and should not be read as stable
  long-run expectations.</p>
</div>
</body>
</html>
"""


def _yn(flag):
    if flag is None:
        return "?"
    return "Y" if flag else "-"


def _flag(flag):
    if flag is None:
        return "num"
    return "hit" if flag else "miss"


def _wps_class(win, place, show):
    """Color the combined W/P/S cell by the best hit: win > place > show > miss."""
    if win is None or place is None or show is None:
        return "num"
    if win:
        return "hit"
    if place:
        return "place"
    if show:
        return "show"
    return "miss"


def _race_data_cells(r):
    """The 7 per-race cells (R#, Predicted, MLO, Actual, Pay, $2 PL, W/P/S) for
    one race row. The Card cell is emitted separately with rowspan by the caller.
    """
    mlo = f"{r['mlo']:.1f}" if r["mlo"] is not None else "-"
    wp = f"{r['winner_payoff']:.2f}" if r["winner_payoff"] is not None else "-"
    if r["winner_payoff"] is None:
        pl_str, pl_cls = "-", "num"
    elif r["hit_win"]:
        pl_str = f"+{r['winner_payoff'] - BET_UNIT:.2f}"
        pl_cls = "num pos"
    else:
        pl_str = f"-{BET_UNIT:.2f}"
        pl_cls = "num neg"
    wps = f"{_yn(r['hit_win'])}/{_yn(r['hit_place'])}/{_yn(r['hit_show'])}"
    wps_cls = _wps_class(r["hit_win"], r["hit_place"], r["hit_show"])
    return (
        f"<td class='num'>{r['race_number']}</td>"
        f"<td class='name'>{_esc(r['predicted'])}</td>"
        f"<td class='num'>{mlo}</td>"
        f"<td class='name'>{_esc(r['actual_winner'])}</td>"
        f"<td class='num'>{wp}</td>"
        f"<td class='{pl_cls}'>{pl_str}</td>"
        f"<td class='{wps_cls}'>{wps}</td>"
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="report.py", description="Generate weekly accuracy HTML.")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--tracks", default="CD,BEL,SAR,GP,SA")
    ap.add_argument("--html", default=None)
    args = ap.parse_args()
    h, path = generate_and_save(args.start, args.end, args.tracks.split(","), args.html)
    print(f"Report saved to {path} and DB (key={REPORT_KEY}).")