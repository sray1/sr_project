"""Refresh the analyst-target report with portfolio/sample separation enforced.

Two modes (required):

  --mode portfolio   Runs against the MAIN database (stock_tracker.db) and the
                     portfolio whitelist (input/portfolio_whitelist.csv), and
                     writes output/latest.html. This is the only mode that
                     updates the main DB and latest.html.

  --mode sample      Runs against an ISOLATED sample database (sample_tracker.db
                     via STT_DB_PATH) and input/sample_symbols.csv, and writes
                     ONLY output/sample_output.html. It does NOT touch the main
                     database and does NOT write latest.html.

This enforces the rule: the main DB + latest.html are updated only for the
portfolio; sample runs are fully isolated (separate DB, separate output, no
leak into latest.html).

Usage:
    python stock_target_tracker/refresh.py --mode portfolio
    python stock_target_tracker/refresh.py --mode sample
    python stock_target_tracker/refresh.py --mode portfolio --full      # also oanor + MarketBeat
    python stock_target_tracker/refresh.py --mode sample --source marketbeat

--source defaults to yahoo_finance (fast). Use --full to additionally fetch
dated targets from oanor (reliable API) and MarketBeat (richer but bot-detected
scraping) which fill the accuracy/analyst sections of each card. The
fetch/prices/accuracy/report steps reuse tracker.py
and report.py via the normal CLI, with STT_DB_PATH set only for sample mode.
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKER = os.path.join(HERE, "tracker.py")
INPUT = os.path.join(HERE, "input")
OUTPUT = os.path.join(HERE, "output")
PORTFOLIO_CSV = os.path.join(INPUT, "portfolio_whitelist.csv")
SAMPLE_CSV = os.path.join(INPUT, "sample_symbols.csv")
SAMPLE_DB = os.path.join(HERE, "sample_tracker.db")
SAMPLE_OUTPUT = os.path.join(OUTPUT, "sample_output.html")
PY = sys.executable


# Per-step timings collected during a run, printed as a summary at the end.
STEP_TIMES = []


def run(cmd, env, label):
    print(f"\n=== {label} ===\n  $ {' '.join(cmd)}")
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True, env=env)
    elapsed = time.perf_counter() - t0
    STEP_TIMES.append((label, elapsed))
    print(f"  [{label}] done in {elapsed:.1f}s")


def report_cmd(env, output_path, write_latest):
    """Build the python -c invocation that generates the report."""
    here_repr = repr(HERE)
    if output_path:
        body = (f"import sys; sys.path.insert(0,{here_repr}); import report; "
                f"report.generate_report({output_path!r}, write_latest={write_latest})")
    else:
        body = (f"import sys; sys.path.insert(0,{here_repr}); import report; "
                f"report.generate_report()")
    return [PY, "-c", body]


def main():
    p = argparse.ArgumentParser(description="Refresh report (portfolio or sample mode).")
    p.add_argument("--mode", choices=["portfolio", "sample"], required=True)
    p.add_argument("--source", default="yahoo_finance",
                   help="Primary target source (default: yahoo_finance). Use 'marketbeat' for dated targets.")
    p.add_argument("--full", action="store_true",
                   help="Also fetch dated targets from oanor + MarketBeat (in addition to --source).")
    p.add_argument("--no-fetch", action="store_true",
                   help="Skip fetch; just refresh prices + accuracy + report.")
    args = p.parse_args()

    env = os.environ.copy()
    if args.mode == "sample":
        # Isolated DB: the main portfolio DB is never touched.
        env["STT_DB_PATH"] = SAMPLE_DB
        csv = SAMPLE_CSV
        report_output = SAMPLE_OUTPUT
        write_latest = False
        label_db = f"sample DB ({os.path.basename(SAMPLE_DB)})"
    else:
        # Portfolio: main DB. Make sure no stale STT_DB_PATH redirects it.
        env.pop("STT_DB_PATH", None)
        csv = PORTFOLIO_CSV
        report_output = None  # default -> output/latest.html (+ timestamped)
        write_latest = True
        label_db = "main DB (stock_tracker.db)"

    if not os.path.exists(csv):
        sys.exit(f"ERROR: whitelist CSV not found: {csv}")

    print(f"Mode: {args.mode} | {label_db} | whitelist: {os.path.basename(csv)}")
    print(f"Report output: {report_output or 'output/latest.html (default)'}")

    if not args.no_fetch:
        run([PY, TRACKER, "fetch", "--csv", csv, "--source", args.source], env,
            f"fetch targets ({args.source})")
        if args.full:
            # Dated-target sources (reliable first, fragile last).
            for dated in ("oanor", "marketbeat"):
                if args.source == dated:
                    continue  # already fetched as the primary source
                run([PY, TRACKER, "fetch", "--csv", csv, "--source", dated], env,
                    f"fetch targets ({dated}, dated)")

    run([PY, TRACKER, "prices", "--csv", csv], env, "prices")
    run([PY, TRACKER, "accuracy"], env, "accuracy")
    run(report_cmd(env, report_output, write_latest), env, "generate report")

    # ── Timing summary ──
    total = sum(s for _, s in STEP_TIMES)
    print("\n" + "=" * 60)
    print("STEP TIMING SUMMARY")
    print("=" * 60)
    for label, elapsed in STEP_TIMES:
        pct = (elapsed / total * 100) if total else 0
        print(f"  {label:<28} {elapsed:>7.1f}s  ({pct:5.1f}%)")
    print("-" * 60)
    print(f"  {'TOTAL (e2e)':<28} {total:>7.1f}s")
    print("=" * 60)

    if args.mode == "sample":
        print(f"\nDone. Sample report: {report_output}")
        print("(Main DB and output/latest.html were NOT touched.)")
        print("Publish to GitHub Pages with: python stock_target_tracker/deploy_gh_pages.py")
    else:
        print(f"\nDone. Portfolio report written to output/latest.html (main DB updated).")


if __name__ == "__main__":
    main()