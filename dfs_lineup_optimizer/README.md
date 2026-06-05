# DFS Lineup Optimizer

DraftKings daily fantasy sports lineup prediction and optimization scripts.

## Setup

```bash
uv venv
.venv\Scripts\activate
uv sync
```

## Code Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DraftKings API                                │
│                    (draft_kings package)                            │
└──────────┬──────────────┬──────────────────┬───────────────────────┘
           │              │                  │
           ▼              ▼                  ▼
  ┌─────────────┐ ┌─────────────┐  ┌──────────────────┐
  │fetch_contests│ │list_contests│  │  showdown_analyzer │
  │     .py      │ │    .py      │  │       .py          │
  └──────┬──────┘ └──────┬──────┘  └────────┬───────────┘
         │               │                   │
         ▼               ▼                   ▼
  ┌──────────────────────────────────────────────────────┐
  │              comprehensive_analyzer.py                │
  │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
  │  │  contest_    │  │ draftkings_  │  │    nba_    │ │
  │  │  detector.py │  │  scoring.py   │  │ rotations │ │
  │  └──────────────┘  └──────────────┘  └───────────┘ │
  └──────────────────────┬───────────────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────────────┐
  │               prediction_tracker.py                   │
  │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
  │  │ game_results │  │     db.py    │  │draftkings_ │ │
  │  │     .py      │  │  (SQLite)    │  │ scoring.py │ │
  │  └──────────────┘  └──────────────┘  └───────────┘ │
  └──────────────────────────────────────────────────────┘
```

### Data Flow

1. **DraftKings API** → Fetch contests, draftable players, and draft group data
2. **contest_detector.py** → Classifies each contest as CLASSIC or SHOWDOWN
3. **draftkings_scoring.py** → Calculates fantasy points using official DK scoring rules
4. **nba_rotations.py** → NBA depth charts + actual last-15-games MPG for rotation-aware analysis
5. **showdown_analyzer.py** → Main pipeline: finds highest-entry contest, deduplicates CPT/UTIL entries, generates lineups with:
   - Player deduplication (keeps base UTIL salary, drops CPT 1.5x duplicates)
   - Minutes-prioritized value rankings using last-15-games MPG
   - Starters-only captain selection
   - Salary cap enforcement ($50,000)
   - Role-separated rankings (Starters / Rotation / Deep Bench)
6. **comprehensive_analyzer.py** → Alternative analyzer with pydfs optimizer for classic contests

## Usage

### 1. Showdown Analyzer (Recommended)

Analyze the most popular NBA Showdown contest with captain optimization, minutes-prioritized rankings, and starter-only captains:

```bash
python dfs_lineup_optimizer/showdown_analyzer.py
```

**Output:**
- Selects the showdown contest with the most entries
- Deduplicates CPT/UTIL player entries (keeps base salary)
- Generates 5 optimal lineups with **starters-only** captains
- Player value rankings sorted by adjusted value (minutes-weighted)
- Role-separated views: Starters (30+ min), Rotation (18-25 min), Deep Bench (<10 min)
- Captain optimization analysis prioritizing starters by adjusted value
- Results saved to `dfs_lineup_optimizer/output/`

### 2. Fetch Contests

```bash
python dfs_lineup_optimizer/fetch_contests.py
```

### 3. List Upcoming Contests

```bash
python dfs_lineup_optimizer/list_contests.py
```

### 4. Comprehensive Analysis

```bash
python dfs_lineup_optimizer/comprehensive_analyzer.py
```

### 5. Prediction Tracking

```bash
# Default: run tracker for NYK @ SAS game
python dfs_lineup_optimizer/prediction_tracker.py

# Custom game
python dfs_lineup_optimizer/prediction_tracker.py --away BOS --home MIA --date 2026-06-10

# View history or summary
python dfs_lineup_optimizer/prediction_tracker.py --history
python dfs_lineup_optimizer/prediction_tracker.py --summary

# Specific player accuracy
python dfs_lineup_optimizer/prediction_tracker.py --player "Josh Hart"
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

## Lineup Generation Rules

1. **CPT/UTIL deduplication** — DK lists each player twice (CPT at 1.5x salary, UTIL at base salary); keep the UTIL entry
2. **Injured players filtered out**
3. **Captain candidates** — starters only, sorted by adjusted value
4. **Salary cap enforced** — all lineups validated to be under $50,000
5. **Rotation data** — starters prioritized via `nba_rotations.py`

## Output

Results are saved to `dfs_lineup_optimizer/output/` with timestamps. Example filename:

```
nba_showdown_2026-06-04_230752.txt
```

## Scripts

| Script                     | Description                                              |
|---------------------------|----------------------------------------------------------|
| `showdown_analyzer.py`      | Showdown analysis with captain optimization, minutes-prioritized rankings |
| `comprehensive_analyzer.py` | Full contest analysis with all rules and lineup generation |
| `prediction_tracker.py`     | Compare predictions to actual results, track accuracy over time |
| `fetch_contests.py`         | Fetch contest and player data from DraftKings             |
| `list_contests.py`          | List upcoming NBA contests ranked by entries               |
| `game_results.py`           | Fetch NBA box scores via nba_api and calculate actual DK points |
| `db.py`                     | SQLite database module for tracking results over time       |
| `contest_detector.py`       | Contest type detection and rules (Classic vs Showdown)     |
| `draftkings_scoring.py`     | DK scoring calculator with realistic stat lines            |
| `nba_rotations.py`          | NBA depth charts + last-15-games MPG (2025-26 season)      |
| `projections.py`            | Projection data integration and value play analysis         |

## Key Concepts

- **Contest ID**: Unique identifier for each individual contest
- **Draft Group ID**: Shared identifier for contests with the same player pool/slate
- **Contest Types**: Classic (8-player, standard positions) vs Showdown (6-player, captain multiplier)
- **Captain Multiplier**: In showdown, the captain earns 1.5x fantasy points and costs 1.5x salary
- **Value (X)**: Fantasy points per $1,000 of salary — higher is better
- **fppg**: Fantasy points per game based on DK scoring rules
- **Adjusted Value**: Minutes-weighted value that discounts low-minute players for DFS reliability
- **MPG**: Actual minutes per game from last 15 games (playoffs); `est` = role-based estimate for other teams

## Notes

- DraftKings does not have an official public API
- Uses unofficial endpoints that may change without notice
- Contest data is only available for current/upcoming slates
- NBA rotation data in `nba_rotations.py` is based on 2025-2026 season depth charts
- MPG data sourced from Basketball-Reference and LandOfBasketball
- Output files are saved to `dfs_lineup_optimizer/output/` and excluded from git