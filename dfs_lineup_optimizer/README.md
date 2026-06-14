# DFS Lineup Optimizer

DraftKings daily fantasy sports lineup prediction and tracking system.

## Setup

```bash
uv venv
.venv\Scripts\activate
uv sync
```

## Project Structure

```
dfs_lineup_optimizer/
├── comprehensive_analyzer.py         # Main pre-game pipeline (Classic + Showdown)
├── showdown_analyzer.py              # Showdown-specific pre-game analysis
├── prediction_tracker.py             # Post-game: compare predictions to actual results
├── lineup_optimizer.py               # Combinatorial lineup search (showdown + classic)
├── player_builder.py                 # Build pydfs Player objects from DK draftables
├── draftkings_scoring.py             # DK NBA scoring rules & stat-line projections
├── nba_rotations.py                  # NBA depth charts, MPG data, role estimation
├── contest_detector.py               # Detect Classic vs Showdown contest type
├── game_results.py                   # Fetch/verify NBA box scores (nba_api + StatMuse)
├── db.py                             # SQLite tracking database (games, lineups, accuracy)
├── utils.py                          # Shared utilities (SALARY_CAP, MultiOutput, run_and_save)
├── projections.py                    # CSV/dict projection data loading
├── fetch_contests.py                 # Fetch DK contest info & draftable players
├── list_contests.py                  # List upcoming NBA contests by type
├── generate_diagram.py               # Generate code-flow PNG for this README
├── legacy/                            # Superseded analysis scripts
│   ├── analyze_with_pydfs.py         #   Original classic optimizer (replaced by lineup_optimizer)
│   ├── analyze_showdown.py           #   Original showdown optimizer (replaced)
│   └── analyze_with_dff.py           #   Daily Fantasy Fuel integration (replaced)
└── tests/
    ├── test_draftkings_scoring.py     # DK scoring calculation tests
    ├── test_contest_detector.py      # Classic/Showdown detection tests
    ├── test_game_results.py          # Box score fetch & parse tests
    └── test_prediction_tracker.py   # End-to-end tracking workflow tests
```

## Code Flow

![DFS Lineup Optimizer Code Flow](code_flow.png)

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

### Typical Workflow

```
1. List contests          → find the draft group / contest ID
2. Run showdown analyzer  → generate optimal lineups for the game
3. (After game) Run tracker → compare predictions vs actual results
```

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

## Scoring Rules

### DK NBA Base Scoring

| Stat                | Points  |
|---------------------|---------|
| Points              | +1.0    |
| Rebounds             | +1.25   |
| Assists              | +1.5    |
| Steals               | +2.0    |
| Blocks               | +2.0    |
| Turnovers            | -0.5    |
| 3-Pointers Made      | +0.5    |
| Double-Double        | +1.5    |
| Triple-Double        | +3.0    |

### Showdown Rules

- **Roster**: 6 players (1 Captain + 5 UTIL)
- **Captain**: 1.5x multiplier on BOTH points AND salary
- **Salary Cap**: $50,000

### Classic Rules

- **Roster**: 8 players (PG/SG/SF/PF/C positions)
- **Salary Cap**: $50,000

## Minutes & Value System

The showdown analyzer uses a **minutes-prioritized value system**:

- **Actual MPG**: Last-15-games minutes per game from Basketball-Reference/LandOfBasketball (for NYK/SAS players in tonight's game)
- **Role-based estimates**: For other teams, minutes are estimated from rotation role (starters ~33 min, rotation ~21 min, bench ~8 min) with salary-based adjustments for star starters
- **Adjusted Value** = `raw_value × (0.4 + 0.6 × minutes_weight)` — starters with high minutes get full weight, low-minute bench players get heavily discounted
- **Captain candidates**: Starters only (highest floor & ceiling for the captain 1.5x multiplier)

## Lineup Generation

The optimizer uses **exhaustive combinatorial enumeration**:

- For each captain candidate (~8-15 starters), enumerate all C(20, 5) = 15,504 utility combinations
- Filter by salary cap ($50,000)
- Return top-N lineups by total projected fppg
- Total search space: ~232,500 combinations — trivial for Python, guarantees optimal results

Previous greedy heuristics could miss better combinations that mix expensive + cheap players.

## Lineup Generation Rules

1. **CPT/UTIL deduplication** — DK lists each player twice (CPT at 1.5x salary, UTIL at base salary); keep the UTIL entry
2. **Injured players filtered out**
3. **Captain candidates** — starters only, sorted by adjusted value
4. **Deep bench excluded** — players with role='none' (no rotation role) are excluded from both captain and utility pools
5. **Minimum fppg threshold** — utility players must project ≥ 7.0 fppg; filters out low-production minimum-salary punt plays with artificially inflated value ratios
6. **Salary cap enforced** — all lineups validated to be under $50,000
7. **Rotation data** — starters prioritized via `nba_rotations.py`
8. **Dynamic projections** — when no stat-line match exists, uses rotation-aware salary-based projections instead of flat `salary/1000`

### Why Exclude Deep Bench & Low-fppg Players?

Minimum-salary players ($1,000) always show inflated raw value (e.g., 5 fppg ÷ $1k = 5.0X) because the denominator is tiny. A starter like Brunson at $10,600 with 47.2 fppg = 4.5X looks "worse" by raw value despite producing 9× more points. The minutes-weighted adjustment helps but doesn't fully solve it — `min_util_fppg=7.0` ensures only players with real DFS production make it into lineups.

## Prediction Tracking

After games are played, the prediction tracker compares projected vs actual performance:

- **Game confirmation** — verifies the game was actually played before fetching results (nba_api scoreboard → box score fallback → StatMuse)
- **Multi-source verification** — fetches box scores from nba_api (primary) and StatMuse (secondary), cross-verifies and merges data
- **Lineup-by-lineup comparison** — projected vs actual fppg for each captain/utility slot
- **Theoretical best lineup** — finds the optimal lineup from actual game results using combinatorial optimizer
- **Efficiency score** — ratio of our best predicted lineup to the theoretical best
- **SQLite database** — tracks results across multiple games for long-term accuracy analysis

## Historical Results

### All Tracked Games (NYK vs SAS 2026 NBA Finals)

| # | Date | Matchup | Best Predicted | Best Possible | Efficiency | Best Captain |
|---|------|---------|---------------|--------------|-----------|--------------|
| 1 | Jun 3 | NYK @ SAS | — | 249.8 | — | No predictions saved |
| 2 | Jun 5 | NYK @ SAS | 209.8 | 253.2 | 82.8% | — |
| 3 | Jun 8 | SAS @ NYK | 182.4 | 240.4 | 75.9% | — |
| 4 | Jun 10 | SAS @ NYK | 160.8 | 250.0 | 64.3% | — |
| 5 | Jun 13 | NYK @ SAS | 135.2 | 249.9 | 54.1% | Josh Hart (CPT) |

**Average across all tracked games: 69.3% efficiency**

### Game 5 Breakdown (NYK 94 @ SAS 90)

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

### Key Learnings from Tracking

- **OG Anunoby** was the Game 4 hero: projected 32.5, actual 46.5 (+14.0) — 7 threes including the game-winner
- **Jalen Brunson** was the Game 5 standout: projected 47.2, actual 57.8 (+10.6) — 45 PTS
- **Jordan Clarkson** has been a consistent bust: projected 15.2, actual 2.2 in Game 4 (-12.9), 3.2 in Game 5 (-12.0)
- **KAT** underperformed heavily in Game 5: projected 47.2, actual 21.5 (-25.7); as CPT, the 1.5x multiplier turned a bad game into a lineup-killer
- Efficiency has trended down across the series (82.8% → 75.9% → 64.3% → 54.1%), primarily driven by captain busts
- Starter captains outperform cheap captains in high-variance playoff games

## Tests

```bash
python -m pytest tests/ -v
```

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_draftkings_scoring.py` | 11 | DK scoring calculations, bonuses, edge cases |
| `tests/test_contest_detector.py` | 14 | Showdown/Classic detection, contest info |
| `tests/test_game_results.py` | 23 | Name normalization, StatMuse URL/parsing, cross-verification, HTML parsing |
| `tests/test_prediction_tracker.py` | 17 | Game confirmation, lineup comparison, best lineup, game info flow |

## Output

Results are saved to `dfs_lineup_optimizer/output/` with timestamps. Example filename:

```
nba_showdown_2026-06-10_004221.txt
```

## Scripts

| Script | Description |
|--------|-------------|
| `showdown_analyzer.py` | Main pipeline: finds highest-entry showdown contest, generates optimal lineups |
| `list_contests.py` | List upcoming NBA contests ranked by entries |
| `prediction_tracker.py` | Compare predictions to actual results; multi-source verification (nba_api + StatMuse) |
| `game_results.py` | Fetch NBA box scores via nba_api + StatMuse; game confirmation and cross-verification |
| `comprehensive_analyzer.py` | Alternative analysis with DB tracking for both contest types |
| `fetch_contests.py` | Low-level: fetch contest and player data from DraftKings API |
| `db.py` | SQLite database module for persistent tracking across games |
| `contest_detector.py` | Contest type detection and rules (Classic vs Showdown) |
| `draftkings_scoring.py` | DK scoring calculator, stat lines, and dynamic projections |
| `nba_rotations.py` | NBA depth charts + last-15-games MPG (2025-26 season) |
| `player_builder.py` | Unified player creation with dedup and rotation metadata |
| `lineup_optimizer.py` | Optimal lineup generation (combinatorial for showdown, pydfs for classic) |
| `utils.py` | Shared utilities: output, scoring rules, API client |
| `projections.py` | Projection data integration and value play analysis |

## Key Concepts

- **Contest ID**: Unique identifier for each individual contest
- **Draft Group ID**: Shared identifier for contests with the same player pool/slate
- **Contest Types**: Classic (8-player, standard positions) vs Showdown (6-player, captain multiplier)
- **Captain Multiplier**: In showdown, the captain earns 1.5x fantasy points and costs 1.5x salary
- **Value (X)**: Fantasy points per $1,000 of salary — higher is better
- **fppg**: Fantasy points per game based on DK scoring rules
- **Adjusted Value**: Minutes-weighted value that discounts low-minute players for DFS reliability
- **MPG**: Actual minutes per game from last 15 games (playoffs); `est` = role-based estimate for other teams
- **Optimal Lineup**: Guaranteed best lineup within the search space (exhaustive enumeration, not greedy)
- **Deep Bench**: Players with no rotation role (role='none') — excluded from lineups due to minimal playing time
- **Punt Play**: Minimum-salary player with inflated value ratio — filtered by `min_util_fppg` threshold
- **Multi-source Verification**: Cross-referencing box scores between nba_api and StatMuse to ensure accuracy

## Notes

- DraftKings does not have an official public API
- Uses unofficial endpoints that may change without notice
- Contest data is only available for current/upcoming slates
- API calls use retry logic via `get_draftkings_client()` (3 retries, 2s delay)
- NBA rotation data in `nba_rotations.py` is based on 2025-2026 season depth charts
- MPG data sourced from Basketball-Reference and LandOfBasketball
- Player name normalization in `game_results.py` cross-references `NBA_ROTATIONS` data for nba_api initial+lastname format
- Output files are saved to `dfs_lineup_optimizer/output/` and excluded from git
- Dynamic projections use position-aware salary scaling when no curated stat lines match a player
- BKN team ID fixed from 1610612741 (was colliding with CHI) to correct 1610612751
- Prediction tracker uses multi-source verification: confirms game was played before fetching results, cross-verifies nba_api and StatMuse data
- Tests are located in `dfs_lineup_optimizer/tests/` and can be run with `python -m pytest tests/ -v`