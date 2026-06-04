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
4. **nba_rotations.py** → Provides NBA depth chart data (starters vs bench) for rotation-aware lineup filtering
5. **comprehensive_analyzer.py** → Combines all modules to generate optimal lineups with:
   - Injured/unavailable player filtering
   - Player deduplication (utility salary preferred over captain salary)
   - Salary >= $3,000 minimum for lineups
   - Top 15 value rankings as captain candidates
   - Multi-strategy utility selection (value-first, fppg-first, budget-aware)
   - Captain 1.5x multiplier on both salary and fppg for showdown

## Usage

### 1. Fetch Contests

Fetch available NBA contests and draftable players:

```bash
python dfs_lineup_optimizer/fetch_contests.py
```

**Output:**
- Displays top 5 NBA/WNBA contests with type, ID, name, draft group, start time, and prize pool
- Fetches and displays the first 10 draftable players for the top contest
- Results automatically saved to a temp file (e.g., `C:\Users\...\AppData\Local\Temp\dk_fetch_xxx.txt`)

### 2. List Upcoming Contests

List all upcoming NBA contests ranked by entry count:

```bash
python dfs_lineup_optimizer/list_contests.py
```

**Output:**
- Top 100 Showdown contests by entry count
- Top 100 Classic contests by entry count
- Contest details: ID, draft group, start time, entries, prize pool, guarantee status

### 3. Comprehensive Analysis (Recommended)

Full contest analysis with proper DK scoring rules, rotation data, and lineup generation:

```bash
python dfs_lineup_optimizer/comprehensive_analyzer.py
```

**Output:**
- Automatically detects the next NBA contest type (Classic or Showdown)
- Displays DK scoring rules for the contest type
- Generates optimal lineups with captain optimization (for Showdown)
- Player value rankings (players under $3,000 excluded)
- Results saved to a temp file (not committed to git)

**Example output (Showdown):**
```
Lineup 1:
  Captain: Keldon Johnson    SAS  $3,600  (cap: $5,400)
    Projected: 15.2 fppg -> 22.9 fppg (with captain multiplier)

  Utility Players:
    Karl-Anthony Towns     NYK  $10,200   47.2 fppg
    Jalen Brunson           NYK  $10,600   47.2 fppg
    Josh Hart               NYK  $8,200    35.8 fppg
    De'Aaron Fox             SAS  $7,600    26.5 fppg
    OG Anunoby               NYK  $7,200    30.5 fppg

  Total: 210.1 fppg, $49,200 salary
```

### 4. Showdown Analyzer

Analyze the most popular NBA Showdown contest with captain optimization:

```bash
python dfs_lineup_optimizer/showdown_analyzer.py
```

**Output:**
- Selects the showdown contest with the most entries
- Generates 5 optimal lineups with captain optimization (starting players only)
- Player value rankings with UTIL and Captain values
- Results saved to a temp file

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
- Captain salary counts as 1.5x toward the cap

### Classic Rules

- **Roster**: 8 players (PG/SG/SF/PF/C positions)
- **Salary Cap**: $50,000
- Standard position requirements

## Lineup Generation Rules

The comprehensive analyzer applies these rules when generating lineups:

1. **Injured players filtered out** — players marked as disabled are excluded
2. **Player deduplication** — duplicate entries (captain vs utility salaries) are merged, keeping the lower utility salary
3. **Captain candidates** — top 15 players by value (fppg per $1k) plus any starters from rotation data
4. **Utility minimum salary** — players under $3,000 excluded from lineups
5. **Utility minimum fppg** — players under 5 fppg excluded from utility spots
6. **Multi-strategy selection** — uses 3 strategies (value-first, fppg-first, budget-aware) and picks the best result
7. **Rotation data** — starters prioritized over bench players via `nba_rotations.py`

## Prediction Tracking

After generating lineups with the comprehensive analyzer, use `prediction_tracker.py` to compare predictions against actual game results and track accuracy over time. All results are saved to a SQLite database (`dfs_results.db`) for historical tracking.

### Run Prediction Tracker

```bash
# Default: run tracker for NYK @ SAS game
python dfs_lineup_optimizer/prediction_tracker.py

# Custom game
python dfs_lineup_optimizer/prediction_tracker.py --away BOS --home MIA --date 2026-06-10

# View past game tracking history
python dfs_lineup_optimizer/prediction_tracker.py --history

# View accuracy summary across all tracked games
python dfs_lineup_optimizer/prediction_tracker.py --summary

# View a specific player's projection accuracy over time
python dfs_lineup_optimizer/prediction_tracker.py --player "Josh Hart"
```

### What Gets Tracked

- **Game results**: Date, teams, contest type
- **Player performances**: Projected fppg vs actual fppg for every player, with starter/bench designation
- **Predicted lineups**: All generated lineups with captain + utilities, projected and actual totals
- **Best possible lineups**: Theoretical optimal lineup within salary cap (for efficiency comparison)

### Database Schema

| Table | Purpose |
|-------|---------|
| `games` | One row per tracked contest (date, teams, contest type) |
| `player_performances` | Per-player projected vs actual fppg per game |
| `lineups` | Predicted and best-possible lineups with scores |

The database file (`dfs_results.db`) is stored in the `dfs_lineup_optimizer/` directory and excluded from git via `.gitignore`.

## Scripts

| Script                     | Description                                              |
|---------------------------|----------------------------------------------------------|
| `comprehensive_analyzer.py` | Full contest analysis with all rules and lineup generation |
| `prediction_tracker.py`     | Compare predictions to actual results, track accuracy over time |
| `showdown_analyzer.py`      | Showdown-specific analysis with captain optimization       |
| `fetch_contests.py`         | Fetch contest and player data from DraftKings             |
| `list_contests.py`          | List upcoming NBA contests ranked by entries               |
| `game_results.py`           | Fetch NBA box scores via nba_api and calculate actual DK points |
| `db.py`                     | SQLite database module for tracking results over time       |
| `contest_detector.py`       | Contest type detection and rules (Classic vs Showdown)     |
| `draftkings_scoring.py`     | DK scoring calculator with realistic stat lines            |
| `nba_rotations.py`          | NBA depth chart data (2025-2026 season)                    |
| `projections.py`            | Projection data integration and value play analysis         |

## Available Sports

| Sport       | Enum         |
|-------------|--------------|
| Basketball  | `Sport.NBA`  |
| Football    | `Sport.NFL`  |
| Baseball    | `Sport.MLB`  |
| Hockey      | `Sport.NHL`  |
| Golf        | `Sport.PGA`  |
| Tennis      | `Sport.TENN` |
| MMA         | `Sport.MMA`  |
| NASCAR      | `Sport.NASCAR` |
| EPL Soccer  | `Sport.EPL`  |
| Soccer      | `Sport.SOC`  |

## Key Concepts

- **Contest ID**: Unique identifier for each individual contest
- **Draft Group ID**: Shared identifier for contests with the same player pool/slate
- **Contest Types**: Classic (8-player, standard positions) vs Showdown (6-player, captain multiplier)
- **Captain Multiplier**: In showdown, the captain earns 1.5x fantasy points and costs 1.5x salary
- **Value (X)**: Fantasy points per $1,000 of salary — higher is better
- **fppg**: Fantasy points per game based on DK scoring rules

## Notes

- DraftKings does not have an official public API
- Uses unofficial endpoints that may change without notice
- Contest data is only available for current/upcoming slates
- NBA rotation data in `nba_rotations.py` is based on 2025-2026 season depth charts