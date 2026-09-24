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
| `--exclude` | comma-separated names | — | Drop specific players from ALL lineups (manual backup/depth exclusions; DFF-listed OUT players are already auto-excluded) |
| `--keep-backup-qbs` | flag | off | Opt OUT of the backup-QB filter. By default only each team's top-salaried QB stays in the pool (DK prices starters well above backups; draftables have no depth-chart flag) |
| `--keep-deep-wrs` | flag | off | Opt OUT of the deep-WR filter. By default WRs no projection source lists (fallback-only) are dropped from the pool — practice squad / WR4+ players DK still shows on the slate get fantasy-relevant projections from real boards, and injury risers get projected once promoted, so unlisted = ghost |
| `--compare` | flag | — | Build one optimal lineup per projection source and print side-by-side overlap/differences (see below) |
| `--no-kicker-model` | flag | off | Opt OUT of the game-environment kicker model (see below). By default every kicker's projection is modeled from his team's offensive projections — both board and salary-curve kicker values proved less accurate than the model (DFF kicker MAE 6.6, +2.4 over actual; the salary curve hands a $4,600 kicker 11.2 points) |
| `--calibrate` | flag | — | Adjust projections by each source's per-position bias from the accuracy DB — mean(actual − projected) over matched rows, gated at n ≥ 30 per (source, position); small samples are skipped with a note |
| `--unique-captains` | flag | — | Showdown: every lineup gets a DIFFERENT captain (the optimal lineup per captain candidate) instead of the usual ≥2-player diversity. The hindsight-optimal captain is rarely the chalk QB (2 of the first 7 showdowns), so this surfaces each captain's best build |

## Projection sources (priority order)

Every player always ends up with a projection; the source is labeled per player:

1. **Manual CSV** (`--csv`) — columns `name`/`player` + `points`/`proj` (see `sample_projections.csv`). Optional `team` column sharpens matching.
2. **DailyFantasyFuel scrape** — server-rendered projection table (`dailyfantasyfuel.com/nfl/projections/`), verified working (2026-09). Column indexes are read from the table header (tolerates reordering); injury tags ("Ja'Marr Chase Q") are stripped from names; `$8.0k` salaries and DST rows (mascot-only names, e.g. "Jaguars") are handled. Parser is frozen against `tests/fixtures/dff_projections.html`.
3. **BlueCollarDFS** — the optimizer page is JS-rendered behind a login (anonymous fetch returns only the site shell), but the site documents a **developer API** (`/api/nfl_draftkings`, premium: key by emailing bluecollardfs@gmail.com, 200 requests/day). The fetcher calls the API when a key is in the `BLUECOLLAR_API_KEY` (or `BCDFS_API_KEY`) environment variable — it then becomes a real projection source and shows up in `--compare` and the accuracy tracker; without a key it degrades to the shell-scraping stub.
4. **numberFire scrape** (best-effort) — currently parses nothing (JS-rendered), kept as a registry slot.
5. **FantasyPros scrape** (best-effort) — static pages only serve ~10 rows per position (~50 top names total); JS rendering is Cloudflare-blocked. Trailing team abbreviations ("Jalen Hurts PHI") are stripped; suffixes/punctuation normalized for matching.
6. **Salary-implied fallback** — crude per-position curve (`proj = salary × slope + floor`), labeled `fallback` and listed in output. Never silent.

**Injury & roster handling:** players DFF lists as **OUT/IR/SUSP** (the board's `data-inj` designation) are excluded from projections *and* from every lineup — pool-level, so salary fallback can't resurrect them. Questionable players stay in. **Traded players** are caught the same way: when DFF's board lists a slate player under a different team than DK's draftables do (DK slates lag roster moves — e.g. after Kayshon Boutte's NE→HOU trade, DK's NE@SEA slate still listed him as a Patriot), the stale DK entry is excluded. Team abbreviations are normalized across sites so convention drift (WSH/WAS) never drops a healthy player. DK's own injury data is honored too: the raw draftables payload carries a `status` field (`OUT`/`IR`/`Q`/`D`) that flags OUT and IR players days before the `is_disabled` flag flips with official inactives (~90 min pre-lock) — every player DK itself marks **OUT or IR is always dropped** (Q/D stay in; DFF's injury board lags it, e.g. week 1 IR players Savion Williams and Jordyn Tyson had no DFF tag). **Deep-WR filter** (default on, `--keep-deep-wrs` to opt out): WRs whose projection is salary-fallback — no board (CSV/DFF/FantasyPros/BlueCollar) lists them — are dropped from the pool; DK slates still list practice-squad signings and the salary curve otherwise hands them fantasy-relevant projections (the Saints' Kyrese Rowan, a Sept 9 practice-squad signing, was being picked at $3,000 over real WRs). Injury risers stay, since boards project promoted players. Manual exclusions: `--exclude "Name, Name"`.

## Comparison & accuracy tracking

Two ways external optimizers are used as yardsticks against the in-house projections:

**Pre-game side-by-side (`analyzer.py --compare`)** — builds one optimal lineup *per projection source* (CSV, DailyFantasyFuel, FantasyPros, salary fallback — every registered source runs through the exact same optimizer, not their finished lineups which aren't scrapeable), then prints each lineup with its projections, roster overlap % vs the baseline (default pipeline) lineup, unique picks, and captain agreement.

**Expert published lineups (classic slates)** — Stokastic's weekly DK cheat sheet (`draftkings-nfl-dfs-cheat-sheet-week-{N}`) is the one free source publishing a complete worked classic lineup. It's parsed from the article's "Worked Example" numbered list (prose mentions of non-rostered players with salaries — e.g. "Tee Higgins ($6,300) ... the reason he is out is price" — are excluded by keeping only the contiguous pick run per list item) and **validated against the contest's own draftables** before use: exactly 9 distinct slate players, every stated salary matches DK's, the roster is a legal classic lineup, and the total matches the article's stated figure. Any failure skips the lineup with a note — nothing is ever fabricated. It appears in `--compare` output and is saved under source `stokastic` by `prediction_tracker.py --save` (classic contests), graded post-game like any other lineup. Frozen against `tests/fixtures/stokastic_week1.html`.

**Hand-transcribed expert lineups (`expert_import.py`)** — other free sources (SI.com showdown + classic, Sporting News, fantasyleagues.info) publish finished lineups with no parseable structure, so their picks are transcribed by hand and imported via the same validation gates (distinct players, stated salaries must match DK's — showdown captains are stated at the 1.5x CPT-slot price, legal roster, under the cap). Most of these articles publish no projection total, so the row is stored with `total_projection = 0.0` — a sentinel meaning "actual only": `--summary` reports it in a separate actual-only table instead of the error stats.

```bash
python nfl_dfs_optimizer/expert_import.py --contest-id N --source si --mode showdown \
    --picks "CPT Josh Allen $17100; Jahmyr Gibbs $12000; ..."
```

The import re-grades the contest from already-recorded actuals (no network); `prediction_tracker.py --rescore [--contest-id N]` does the same for all saved lineups.

**Post-game accuracy tracking (`prediction_tracker.py`)** — mirrors the NBA `dfs_lineup_optimizer/prediction_tracker.py` pattern:

- `--save [--contest-id N]` — pre-game snapshot: per-source player projections + each source's optimal lineup into `nfl_accuracy.db` (idempotent per contest). Kickers are modeled by default (`--no-kicker-model` opts out); `--calibrate` also snapshots calibrated projections + lineups as `{source}+cal`
- `--score [--contest-id N | --date YYYY-MM-DD]` — fetch actual results via ESPN's hidden JSON API (`game_results.py`), fill actual DK points per player and lineup (showdown captains at 1.5x), print per-source accuracy. Games not yet final are skipped, never fabricated
- `--rescore [--contest-id N]` — re-grade saved lineups from already-recorded actuals (no network), e.g. after an expert lineup was imported into a graded contest
- `--history` / `--summary` — saved contests, cumulative per-source accuracy (lineup-level MAE, player-level MAE + bias; expert lineups with the 0.0 sentinel in a separate actual-only table)

ESPN stat cells are read by label name (ESPN appends box-score columns after games go final — e.g. 'QBR' appeared in the passing row post-game — so positional indexing would silently zero whole groups; a group only skips when a required label vanishes). Kicker points are exact from the scoring-plays list, which carries every made field goal with its distance (the box-score kicking group only has aggregates). Kick/punt-return groups are parsed too: return TDs score 6 DK points (return yardage scores 0), and return-only players get an explicit 0-point row so "no recorded actual" means the player truly didn't appear in the box. Blocked kicks, safeties and two-point conversions remain known approximations (ESPN team totals lack them).

## Projection refinement: kicker model + calibration

Two refinements built from the accuracy DB's own history (both default where stated; both opt-out):

**Kicker model (`kicker_model.py`, default ON)** — no board ranks kickers well: across the first 9 graded contests, DFF's own kicker projections ran +2.4 over actual with 6.6 MAE, and unlisted kickers fall to the salary curve (11.2 points for any $4,600 kicker). Yet a kicker was the hindsight-optimal *captain* in one of the first seven showdowns (Butker, 5 FGs). Every kicker is instead projected from his team's offensive environment:

```
kicker = 7.36 + 0.016 * (own team's projected DK points at QB/RB/WR/TE - 88.7), clamped [3, 14]
```

fitted on the DB's 14 recorded kicker-games (the slope is correlation-shrunk; the model's MAE 3.82 beats DFF's 6.6 on the same sample). Per-source runs (`--compare`, `--save`) model each source's kickers from that source's own offensive projections. Manual CSV kicker projections are never overridden. `--no-kicker-model` opts out (analyzer + tracker).

**Calibration (`calibration.py`, `--calibrate`)** — reads each source's per-position bias from `nfl_accuracy.db` (mean(actual − projected) over *matched* rows, n ≥ 30 gate per source-position) and adds it to projections before optimizing. Current DFF corrections: TE +0.91, WR +0.20, QB +0.12, RB −0.08, DST −0.05; positions under the gate (e.g. K at n=6) are skipped with a printed note. In `analyzer.py`/`--compare` the corrections apply in place; in `prediction_tracker.py --save --calibrate` the calibrated snapshot is saved as `{source}+cal` (matched flags preserved), so `--summary` tracks raw and calibrated accuracy side by side — re-runs never overwrite the raw snapshot.

**Captain diversity (`--unique-captains`, showdown)** — generates the optimal lineup *per captain candidate* (each lineup's captain differs, descending by projection) instead of near-duplicate lineups; returns fewer when captain candidates run out.

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
- Max 1 kicker per team (house rule — DK lists every team's kicker and punter/backup at the same K salary, and the kicker model gives them near-identical projections, so the MILP was stacking two GB kickers; applies in `hindsight_showdown.py` too)

## Project structure

```
nfl_dfs_optimizer/
├── analyzer.py              # Main CLI: mode selection, projections, optimization
├── dk_client.py             # DK NFL contests + draftables (best-effort fetch)
├── contest_detector.py      # Showdown/Classic detection + main-slate detection
├── nfl_scoring.py           # DK NFL scoring rules (offense + DST)
├── projections.py           # Fetcher registry: CSV → DailyFantasyFuel → BlueCollarDFS → numberFire → FantasyPros → salary fallback
├── kicker_model.py          # Game-environment kicker projections (default on)
├── calibration.py           # DB-driven per-position projection corrections (--calibrate)
├── player_builder.py        # Dedup CPT/UTIL, projections attach, pydfs Player construction
├── showdown_optimizer.py    # Pulp MILP: CPT + 5 FLEX, cap, team-max, top-N diversity
├── classic_optimizer.py     # pydfs DK Football + stacking rules, lineup validation
├── comparison.py            # --compare: one optimal lineup per source, overlap report
├── expert_lineups.py        # Stokastic cheat-sheet parser + expert lineup validation/build helpers
├── expert_import.py         # Import + validate hand-transcribed expert lineups (SI, Sporting News, ...)
├── prediction_tracker.py    # Pre-game snapshot --save, post-game --score/--rescore/--history/--summary
├── accuracy_db.py           # SQLite layer for accuracy tracking (nfl_accuracy.db)
├── game_results.py          # ESPN hidden API: actual NFL box scores → actual DK points
├── hindsight_showdown.py    # Hindsight-optimal showdown benchmark (recorded actuals → exact MILP)
├── hindsight_classic.py     # Hindsight-optimal classic benchmark (recorded actuals → exact optimizer)
├── sample_projections.csv  # Example manual projection CSV
└── tests/                   # 294 tests incl. MILP-vs-brute-force cross-check
```

## Notes

- DraftKings has no official public API; the `draft_kings` library uses unofficial endpoints that may change without notice
- **pydfs `GameInfo` gotcha**: all players in the same game must share one `GameInfo` instance — pydfs's `MinGamesRule` groups by identity and names per-game variables by team pair; per-player instances produce duplicate MILP variables that crash CBC
- `pulp` upgraded to 3.3.2 in this venv (2.4 bundled with pydfs also crashes on some models); `pydfs_lineup_optimizer` deprecation warnings under pulp 3.x are harmless
- FantasyPros/numberFire/BlueCollarDFS are bot-walled or login-walled (same pattern as the horse race predictor's sources); DailyFantasyFuel is the one reliably working scrape, and the manual CSV path remains the priority override
- Post-game accuracy tracking is live: run `prediction_tracker.py --save` before a contest and `--score` after the games finish