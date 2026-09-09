# NFL DFS Optimizer

DraftKings **NFL-only** lineup optimizer. Two contest formats:

- **Showdown** (single game): 1 Captain (1.5x points & salary) + 5 FLEX, $50,000 cap, max 5 from one team — exact **pulp MILP**
- **Classic** (Sunday main slate): 1 QB / 2 RB / 3 WR / 1 TE / 1 FLEX / 1 DST, $50,000 cap — **pydfs_lineup_optimizer** with stacking rules

Both optimizers are exact (no greedy heuristics). The showdown MILP is cross-checked against brute-force enumeration in tests.

## Setup

```bash
uv venv
.venv\Scripts\activate
uv sync
```

## Usage

```powershell
# List upcoming DK NFL contests (showdown + classic, by entries)
python nfl_dfs_optimizer/dk_client.py

# Auto mode: classic on Sundays, showdown otherwise (top 5 lineups)
python nfl_dfs_optimizer/analyzer.py

# Showdown for tonight's game, single optimal lineup
python nfl_dfs_optimizer/analyzer.py --mode showdown --lineups 1

# Classic main slate with QB + 2 pass-catcher stack, top 3 lineups
python nfl_dfs_optimizer/analyzer.py --mode classic --stack qb2 --lineups 3

# Specific contest, manual projections CSV, skip scrapers
python nfl_dfs_optimizer/analyzer.py --contest-id 193391004 --csv my_projections.csv --no-scrape

# Compare one optimal lineup per projection source (pre-game)
python nfl_dfs_optimizer/analyzer.py --mode showdown --compare

# Save a pre-game snapshot of every source's projections + optimal lineup
python nfl_dfs_optimizer/prediction_tracker.py --save

# After the games finish: fill actuals, print per-source accuracy
python nfl_dfs_optimizer/prediction_tracker.py --score

# Saved contests / cumulative per-source accuracy
python nfl_dfs_optimizer/prediction_tracker.py --history
python nfl_dfs_optimizer/prediction_tracker.py --summary

# Run tests
cd nfl_dfs_optimizer
python -m pytest tests/ -v
```

Output is printed and saved to `nfl_dfs_optimizer/output/` (gitignored). The accuracy tracker stores snapshots in `nfl_dfs_optimizer/nfl_accuracy.db` (gitignored via `*.db`).

## CLI flags

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--mode` | auto / showdown / classic | auto | auto = classic on Sundays (ET), showdown otherwise |
| `--contest-id` | DK contest ID | auto-select | Skips contest selection (most-entries showdown or main-slate classic) |
| `--lineups` | N | 5 (showdown) / 1 (classic) | Top-N lineups; subsequent lineups differ by ≥2 players |
| `--stack` | none / qbwr / qb2 / team3 / bringback | qbwr | Classic stacking rule (below) |
| `--csv` | file path | — | Manual projections CSV (highest priority source) |
| `--week` | N | — | Week number passed to projection scrapers |
| `--no-scrape` | flag | — | Skip web scrapers (CSV + salary fallback only) |
| `--no-dst-captain` | flag | — | Showdown: forbid DST as captain |
| `--exclude` | comma-separated names | — | Drop specific players from ALL lineups (manual backup/depth/injury exclusions) |
| `--keep-backup-qbs` | flag | off | Opt OUT of the backup-QB filter. By default only each team's top-salaried QB stays in the pool (DK prices starters well above backups; draftables have no depth-chart flag) |
| `--compare` | flag | — | Build one optimal lineup per projection source and print side-by-side overlap/differences (see below) |

## Projection sources (priority order)

Every player always ends up with a projection; the source is labeled per player:

1. **Manual CSV** (`--csv`) — columns `name`/`player` + `points`/`proj` (see `sample_projections.csv`). Optional `team` column sharpens matching.
2. **DailyFantasyFuel scrape** — server-rendered projection table (`dailyfantasyfuel.com/nfl/projections/`), verified working (2026-09). Column indexes are read from the table header (tolerates reordering); injury tags ("Ja'Marr Chase Q") are stripped from names; `$8.0k` salaries and DST rows (mascot-only names, e.g. "Jaguars") are handled. Parser is frozen against `tests/fixtures/dff_projections.html`.
3. **BlueCollarDFS scrape** (best-effort stub) — the optimizer is JS-rendered behind a login and its projections/API are premium-gated, so the fetcher parses nothing today. It stays registered so it slots in automatically if a public endpoint appears.
4. **numberFire scrape** (best-effort) — currently parses nothing (JS-rendered), kept as a registry slot.
5. **FantasyPros scrape** (best-effort) — static pages only serve ~10 rows per position (~50 top names total); JS rendering is Cloudflare-blocked. Trailing team abbreviations ("Jalen Hurts PHI") are stripped; suffixes/punctuation normalized for matching.
6. **Salary-implied fallback** — crude per-position curve (`proj = salary × slope + floor`), labeled `fallback` and listed in output. Never silent.

## Comparison & accuracy tracking

Two ways external optimizers are used as yardsticks against the in-house projections:

**Pre-game side-by-side (`analyzer.py --compare`)** — builds one optimal lineup *per projection source* (CSV, DailyFantasyFuel, FantasyPros, salary fallback — every registered source runs through the exact same optimizer, not their finished lineups which aren't scrapeable), then prints each lineup with its projections, roster overlap % vs the baseline (default pipeline) lineup, unique picks, and captain agreement.

**Post-game accuracy tracking (`prediction_tracker.py`)** — mirrors the NBA `dfs_lineup_optimizer/prediction_tracker.py` pattern:

- `--save [--contest-id N]` — pre-game snapshot: per-source player projections + each source's optimal lineup into `nfl_accuracy.db` (idempotent per contest)
- `--score [--contest-id N | --date YYYY-MM-DD]` — fetch actual results via ESPN's hidden JSON API (`game_results.py`), fill actual DK points per player and lineup (showdown captains at 1.5x), print per-source accuracy. Games not yet final are skipped, never fabricated
- `--history` / `--summary` — saved contests, cumulative per-source accuracy (lineup-level MAE, player-level MAE + bias)

ESPN stat columns are verified against the label list before reading (a shifted layout skips the group loudly rather than misreading); blocked kicks, safeties and two-point conversions are known approximations (ESPN team totals lack them).

## Stacking rules (classic)

| Rule | Meaning |
|------|---------|
| `qbwr` | QB + ≥1 WR/TE teammate |
| `qb2` | QB + 2 WR/TE teammates |
| `team3` | Any 3 players from one team |
| `bringback` | 4 players from one game, ≥1 from each side |
| `none` | No stack constraints |

## Showdown rules enforced (MILP constraints + `validate_lineup`)

- Exactly 1 CPT + 5 FLEX, no player in both roles
- Captain: 1.5x points AND salary
- $50,000 cap
- Max 5 players from one team (captain counts)

## Project structure

```
nfl_dfs_optimizer/
├── analyzer.py              # Main CLI: mode selection, projections, optimization
├── dk_client.py             # DK NFL contests + draftables (best-effort fetch)
├── contest_detector.py      # Showdown/Classic detection + main-slate detection
├── nfl_scoring.py           # DK NFL scoring rules (offense + DST)
├── projections.py           # Fetcher registry: CSV → DailyFantasyFuel → BlueCollarDFS → numberFire → FantasyPros → salary fallback
├── player_builder.py        # Dedup CPT/UTIL, projections attach, pydfs Player construction
├── showdown_optimizer.py    # Pulp MILP: CPT + 5 FLEX, cap, team-max, top-N diversity
├── classic_optimizer.py     # pydfs DK Football + stacking rules, lineup validation
├── comparison.py            # --compare: one optimal lineup per source, overlap report
├── prediction_tracker.py    # Pre-game snapshot --save, post-game --score/--history/--summary
├── accuracy_db.py           # SQLite layer for accuracy tracking (nfl_accuracy.db)
├── game_results.py          # ESPN hidden API: actual NFL box scores → actual DK points
├── sample_projections.csv  # Example manual projection CSV
└── tests/                   # 134 tests incl. MILP-vs-brute-force cross-check
```

## Notes

- DraftKings has no official public API; the `draft_kings` library uses unofficial endpoints that may change without notice
- **pydfs `GameInfo` gotcha**: all players in the same game must share one `GameInfo` instance — pydfs's `MinGamesRule` groups by identity and names per-game variables by team pair; per-player instances produce duplicate MILP variables that crash CBC
- `pulp` upgraded to 3.3.2 in this venv (2.4 bundled with pydfs also crashes on some models); `pydfs_lineup_optimizer` deprecation warnings under pulp 3.x are harmless
- FantasyPros/numberFire/BlueCollarDFS are bot-walled or login-walled (same pattern as the horse race predictor's sources); DailyFantasyFuel is the one reliably working scrape, and the manual CSV path remains the priority override
- Post-game accuracy tracking is live: run `prediction_tracker.py --save` before a contest and `--score` after the games finish