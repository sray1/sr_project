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

# Run tests
cd nfl_dfs_optimizer
python -m pytest tests/ -v
```

Output is printed and saved to `nfl_dfs_optimizer/output/` (gitignored).

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

## Projection sources (priority order)

Every player always ends up with a projection; the source is labeled per player:

1. **Manual CSV** (`--csv`) — columns `name`/`player` + `points`/`proj` (see `sample_projections.csv`). Optional `team` column sharpens matching.
2. **FantasyPros scrape** (best-effort) — static pages only serve ~10 rows per position (~50 top names total); JS rendering is Cloudflare-blocked. Trailing team abbreviations ("Jalen Hurts PHI") are stripped; suffixes/punctuation normalized for matching.
3. **numberFire scrape** (best-effort) — currently parses nothing (JS-rendered), kept as a registry slot.
4. **Salary-implied fallback** — crude per-position curve (`proj = salary × slope + floor`), labeled `fallback` and listed in output. Never silent.

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
├── projections.py           # Fetcher registry: CSV → FantasyPros → numberFire → salary fallback
├── player_builder.py        # Dedup CPT/UTIL, projections attach, pydfs Player construction
├── showdown_optimizer.py    # Pulp MILP: CPT + 5 FLEX, cap, team-max, top-N diversity
├── classic_optimizer.py     # pydfs DK Football + stacking rules, lineup validation
├── sample_projections.csv  # Example manual projection CSV
└── tests/                   # 67 tests incl. MILP-vs-brute-force cross-check
```

## Notes

- DraftKings has no official public API; the `draft_kings` library uses unofficial endpoints that may change without notice
- **pydfs `GameInfo` gotcha**: all players in the same game must share one `GameInfo` instance — pydfs's `MinGamesRule` groups by identity and names per-game variables by team pair; per-player instances produce duplicate MILP variables that crash CBC
- `pulp` upgraded to 3.3.2 in this venv (2.4 bundled with pydfs also crashes on some models); `pydfs_lineup_optimizer` deprecation warnings under pulp 3.x are harmless
- FantasyPros/numberFire are bot-walled for JS rendering (same pattern as the horse race predictor's sources); the manual CSV path is the reliable projection source
- Post-game accuracy tracking (like the NBA `prediction_tracker`) is a future phase