# sr_project

Shonket's project

## Setup

This project uses [uv](https://github.com/astral-sh/uv) for fast Python package management.

### Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) installed

### Installation

1. Install uv (if not already installed):
   ```powershell
   pip install uv
   ```

2. Create and activate the virtual environment:
   ```powershell
   uv venv
   .venv\Scripts\activate
   ```

3. Install dependencies:
   ```powershell
   uv sync
   ```

### Adding Dependencies

To add a new package:
```powershell
uv add <package-name>
```

To add a development dependency:
```powershell
uv add --dev <package-name>
```

### Running Scripts

Run Python scripts normally:
```powershell
python fibonacci.py
python test_numpy_pandas.py
```

## Project Structure

```
sr_project/
├── fibonacci.py                          # Fibonacci number implementation
├── test_numpy_pandas.py                  # NumPy/Pandas test script
├── pyproject.toml                        # Dependencies (Python 3.11+, draft-kings, nba-api, pydfs, pandas, numpy)
├── dfs_lineup_optimizer/
│   ├── comprehensive_analyzer.py         # Main pre-game pipeline (Classic + Showdown)
│   ├── showdown_analyzer.py              # Showdown-specific pre-game analysis
│   ├── prediction_tracker.py             # Post-game: compare predictions to actual results
│   ├── lineup_optimizer.py               # Combinatorial lineup search (showdown + classic)
│   ├── player_builder.py                 # Build pydfs Player objects from DK draftables
│   ├── draftkings_scoring.py             # DK NBA scoring rules & stat-line projections
│   ├── nba_rotations.py                  # NBA depth charts, MPG data, role estimation
│   ├── contest_detector.py               # Detect Classic vs Showdown contest type
│   ├── game_results.py                   # Fetch/verify NBA box scores (nba_api + StatMuse)
│   ├── db.py                             # SQLite tracking database (games, lineups, accuracy)
│   ├── utils.py                          # Shared utilities (SALARY_CAP, MultiOutput, run_and_save)
│   ├── projections.py                    # CSV/dict projection data loading
│   ├── fetch_contests.py                 # Fetch DK contest info & draftable players
│   ├── list_contests.py                  # List upcoming NBA contests by type
│   ├── generate_diagram.py               # Generate code-flow PNG for this README
│   ├── legacy/                            # Superseded analysis scripts
│   │   ├── analyze_with_pydfs.py         #   Original classic optimizer (replaced by lineup_optimizer)
│   │   ├── analyze_showdown.py           #   Original showdown optimizer (replaced)
│   │   └── analyze_with_dff.py           #   Daily Fantasy Fuel integration (replaced)
│   └── tests/
│       ├── test_draftkings_scoring.py     # DK scoring calculation tests
│       ├── test_contest_detector.py      # Classic/Showdown detection tests
│       ├── test_game_results.py          # Box score fetch & parse tests
│       └── test_prediction_tracker.py    # End-to-end tracking workflow tests
```

## Shared Modules

These modules are used by both the pre-game and post-game pipelines:

### `utils.py` — Shared utilities
- `SALARY_CAP = 50000` — Standard DK salary cap constant
- `MultiOutput` — Dual-stream writer (stdout + file) for saving script output
- `run_and_save(main_func, prefix)` — Boilerplate wrapper that runs a `main()` function while capturing output to a temp file
- `get_draftkings_client(max_retries=3)` — DK API client factory with retry logic
- `display_scoring_rules(contest_type)` — Print DK scoring rules for classic or showdown

### `draftkings_scoring.py` — DK NBA scoring & projections
- `DKScoringRules` — Dataclass with DK point values (1.0/PT, 1.25/REB, 1.5/AST, 2.0/STL, 2.0/BLK, -0.5/TO, 0.5/3PM, +1.5 DD, +3.0 TD)
- `PlayerStats` — Dataclass for a player stat line (points, rebounds, assists, steals, blocks, turnovers, three_pointers)
- `DKScoringCalculator.calculate_fantasy_points(stats)` — Core fppg calculator with milestone bonuses
- `REALISTIC_STAT_LINES` — Hardcoded stat-line projections keyed by player ID/name
- `generate_projections_from_salary(salary, positions)` — Fallback projection generator from salary + position
- `generate_projections_from_rotation(name, team, salary, positions)` — Rotation-aware projection generator using minutes and role
- `set_stat_lines()` / `get_active_stat_lines()` — Runtime override for custom stat lines

### `nba_rotations.py` — NBA depth charts & minutes
- `NBA_ROTATIONS` — Dict of all 30 NBA teams with `starting` (5) and `rotation` (8–11) lists
- `ACTUAL_MPG` — Last-15-games MPG data keyed by `(team_abbr, player_name)` from LandOfBasketball/ESPN/StatMuse
- `is_starter(name, team)` / `is_rotation_player(name, team)` — Check rotation role
- `get_rotation_status(name, team)` — Returns `'starter'`, `'rotation'`, or `'none'`
- `get_estimated_minutes(name, team, salary)` — Last-15 MPG if available, otherwise role-based estimate
- `get_minutes_weight(minutes)` — Convert MPG to a 0–1 reliability weight for DFS value adjustment

### `contest_detector.py` — Contest type detection
- `ContestType` enum — `CLASSIC` or `SHOWDOWN`
- `ContestInfo` dataclass — Contest ID, name, type, salary cap, roster spots, captain multiplier
- `detect_contest_type(name)` — Keyword-based detection (showdown/SGP/captain/MVP → SHOWDOWN, else CLASSIC)
- `get_contest_info(contest_id, name)` — Full contest metadata
- `display_contest_info(info)` — Pretty-print contest rules

### `player_builder.py` — Player object factory
- `create_pydfs_players_with_scoring(draftables, include_rotation_meta, stat_lines, min_salary)` — The central player creation function:
  1. Iterates DK draftables, skipping disabled and sub-minimum-salary players
  2. Matches each player to a stat line by ID, then by position + salary proximity
  3. Falls back to `generate_projections_from_rotation()` then `generate_projections_from_salary()`
  4. Deduplicates CPT/UTIL entries (keeps lower-salary UTIL version)
  5. Optionally attaches rotation metadata (`role`, `minutes`, `mpg_actual`)
  6. Returns `(players, player_meta)` or just `players`

### `lineup_optimizer.py` — Combinatorial lineup search
- `generate_optimal_showdown_lineups(players, player_meta, n_lineups, ...)` — Exhaustive enumeration of 1-CPT + 5-UTIL combos under salary cap, with role/fppg filters
- `generate_optimal_showdown_lineups_fast(...)` — Same result with upper-bound pruning for faster execution
- `generate_classic_lineups(players, n_lineups)` — Wraps `pydfs_lineup_optimizer` for 8-player classic contests
- `find_best_possible_showdown_lineup(player_scores, ...)` — Post-game only: find theoretical best lineup from actual fppg (used by `prediction_tracker`)

### `game_results.py` — Multi-source box score fetching
- `confirm_game_played(date, away, home)` — Checks if game was played (nba_api scoreboard → box score → StatMuse)
- `fetch_box_score(date, home_team, away_team)` — Primary: nba_api BoxScoreTraditionalV3 (or V2 fallback)
- `fetch_statmuse_box_score(date, away, home)` — Secondary: scrapes StatMuse game page via requests
- `verify_box_score(primary, secondary)` — Cross-verifies and merges data from both sources (prefers StatMuse on discrepancy)
- `find_best_possible_lineup(player_scores, ...)` — Delegates to `lineup_optimizer`
- `_normalize_player_name(short_name, team)` — Maps nba_api abbreviated names to full names using `NBA_NAME_MAP` and rotation data

### `db.py` — SQLite tracking database (`dfs_results.db`)
- **Tables:** `games` (date, teams, contest type), `player_performances` (projected vs actual fppg), `lineups` (predicted and best-possible lineups with totals)
- `save_game()` / `save_player_performance()` / `save_lineup()` — Write operations (all idempotent for games)
- `get_game_history(limit)` / `get_game_details(game_id)` / `get_accuracy_summary()` — Read operations for historical tracking
- `display_history()` / `display_accuracy_summary()` / `display_player_history(name)` — Pretty-printed console output
- `init_db()` — Creates tables if they don't exist (called automatically by scripts)

### `projections.py` — External projection loading
- `ProjectionManager` — Load projections from CSV or dict, merge with DK draftable players, get top value plays
- `demo_projection_integration()` — Interactive demo using live DK data

## Data Flow

### Pre-game pipeline (lineup generation)

```
DK API (contests + draftables)
  │
  ├── list_contests.py ─────▶ Print upcoming Classic/Showdown contests
  ├── fetch_contests.py ────▶ Print contest details + draftable players
  │
  ▼
comprehensive_analyzer.py (auto-detects type)  OR  showdown_analyzer.py (showdown-only)
  │
  ├── contest_detector.py ──▶ Classic or Showdown rules + salary cap
  │
  ├── player_builder.py ────▶ Creates pydfs Player objects
  │     ├── draftkings_scoring.py ──▶ fppg from stat lines (ID match → position/salary fallback → rotation fallback)
  │     └── nba_rotations.py ──────▶ role (starter/rotation/none), estimated minutes, minutes weight
  │
  ├── lineup_optimizer.py ──▶ Optimal 5 lineups
  │     ├── Showdown: exhaustive 1-CPT × C(5 from N) enumeration, $50k cap
  │     │     Filters: min_util_salary, min_util_fppg, exclude_roles
  │     └── Classic: pydfs_lineup_optimizer (8 spots, position requirements)
  │
  └── db.py ────────────────▶ Save game + player projections + predicted lineups
```

### Post-game pipeline (accuracy tracking)

```
prediction_tracker.py (--away NYK --home SAS --date 2026-06-08)
  │
  ├── game_results.py
  │     ├── confirm_game_played() ──▶ nba_api scoreboard → StatMuse → "played: true/false"
  │     ├── fetch_box_score() ──────▶ nba_api BoxScoreV3 (primary)
  │     ├── fetch_statmuse_box_score() ──▶ StatMuse HTML scrape (backup)
  │     └── verify_box_score() ──────▶ Cross-verify + merge (prefer StatMuse on conflict)
  │
  ├── draftkings_scoring.py ──▶ Calculate actual fppg from box score stats
  │
  ├── lineup_optimizer.py
  │     └── find_best_possible_showdown_lineup() ──▶ Exhaustive search for theoretical optimal
  │
  ├── Display: actual scores, predicted-vs-actual comparison, best-possible lineup, efficiency %
  │
  └── db.py ──▶ Save player actuals + predicted lineups (with actuals) + best-possible lineups + efficiency metrics
```

### Historical queries

```
prediction_tracker.py --history   ──▶ db.display_history()      (recent games + accuracy)
prediction_tracker.py --summary   ──▶ db.display_accuracy_summary() (aggregate stats)
prediction_tracker.py --player "Jalen Brunson" ──▶ db.display_player_history() (per-player projection accuracy)
```

## Usage

### Pre-game: generate lineups

```powershell
# Auto-detect contest type (Showdown or Classic) and run full analysis
python dfs_lineup_optimizer/comprehensive_analyzer.py

# Showdown-only analysis with rotation metadata and captain optimization
python dfs_lineup_optimizer/showdown_analyzer.py

# List upcoming NBA contests (type, entries, start time)
python dfs_lineup_optimizer/list_contests.py

# Fetch DK contest details and draftable players
python dfs_lineup_optimizer/fetch_contests.py

# Display DK scoring breakdown for a sample player
python dfs_lineup_optimizer/draftkings_scoring.py
```

### Post-game: track predictions vs actuals

```powershell
# Track a specific game (defaults: NYK @ SAS on 2026-06-04)
python dfs_lineup_optimizer/prediction_tracker.py

# Track a different game
python dfs_lineup_optimizer/prediction_tracker.py --away SAS --home NYK --date 2026-06-08

# View historical tracking data
python dfs_lineup_optimizer/prediction_tracker.py --history

# View aggregate accuracy summary across all tracked games
python dfs_lineup_optimizer/prediction_tracker.py --summary

# View per-player projection history
python dfs_lineup_optimizer/prediction_tracker.py --player "Jalen Brunson"
```

### Run tests

```powershell
cd dfs_lineup_optimizer
python -m pytest tests/ -v
```

### Historical Results

| # | Date | Matchup | Best Predicted | Best Possible | Efficiency | Best Captain |
|---|------|---------|---------------|--------------|-----------|--------------|
| 1 | Jun 3 | NYK @ SAS | — | 249.8 | — | No predictions saved |
| 2 | Jun 5 | NYK @ SAS | 209.8 | 253.2 | 82.8% | — |
| 3 | Jun 8 | SAS @ NYK | 182.4 | 240.4 | 75.9% | — |
| 4 | Jun 10 | SAS @ NYK | 160.8 | 250.0 | 64.3% | — |
| 5 | Jun 13 | NYK @ SAS | 135.2 | 249.9 | 54.1% | Josh Hart (CPT) |

**Average across all tracked games: 69.3% efficiency**

#### Game 5 Breakdown (NYK 94 @ SAS 90)

| Lineup | Captain | Projected | Actual | Diff | Efficiency | Grade |
|--------|---------|-----------|--------|------|-----------|-------|
| 1 | Karl-Anthony Towns | 223.9 | 135.2 | -88.7 | 54.1% | F |
| 2 | Karl-Anthony Towns | 222.6 | 183.9 | -38.6 | 73.6% | C |
| 3 | Stephon Castle | 222.2 | 146.6 | -75.6 | 58.7% | F |
| 4 | Josh Hart ⭐ | 222.0 | 195.5 | -26.5 | **78.2%** | C+ |
| 5 | Victor Wembanyama | 222.0 | 152.9 | -69.0 | 61.2% | D |

**Key bust:** KAT projected 70.8 CPT fppg → actual 32.2 (2 PTS, 10 REB, 5 TO). The 1.5x captain multiplier amplified this massively across Lineups 1–4.

**Key hit:** Jalen Brunson projected 47.2 → actual 57.8 (+10.6, 45 PTS).

Efficiency = (best predicted lineup actual fppg) / (theoretical best possible fppg) × 100
