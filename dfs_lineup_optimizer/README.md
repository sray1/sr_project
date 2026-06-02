# DFS Lineup Optimizer

DraftKings daily fantasy sports lineup prediction and optimization scripts.

## Setup

No additional dependencies needed beyond the main project setup. The `draft-kings` package is already installed.

## Usage

### Fetch Contests

Fetch available contests for a sport:

```bash
python dfs_lineup_optimizer/fetch_contests.py
```

This will:
- Display the first 5 available NBA/WNBA contests
- Show contest details including type (CLASSIC/SHOWDOWN), ID, name, draft group ID, start time, and prize pool
- Fetch and display the first 10 draftable players for the top contest

### Comprehensive Analysis

Run full contest analysis with proper DK scoring rules:

```bash
python dfs_lineup_optimizer/comprehensive_analyzer.py
```

This provides:
- **Automatic contest type detection** (Classic vs Showdown)
- **Proper DK scoring rules** for NBA
- **Captain multiplier handling** for showdown contests (1.5x on points and salary)
- **Optimal lineup generation** for contest type
- **Player value rankings** with realistic projections

#### DK Scoring Rules Implemented:

**Base Scoring:**
- Points: +1.0
- Rebounds: +1.25
- Assists: +1.5
- Steals: +2.0
- Blocks: +2.0
- Turnovers: -0.5
- 3-Pointers Made: +0.5
- Double-Double: +1.5
- Triple-Double: +3.0

**Showdown-Specific:**
- 6-player roster (1 Captain + 5 UTIL)
- Captain: 1.5x multiplier on both points AND salary
- $50,000 salary cap

**Classic-Specific:**
- 8-player roster (PG/SG/SF/PF/C positions)
- $50,000 salary cap
- Standard position requirements

### Projections Integration

Work with projection data for lineup optimization:

```bash
python dfs_lineup_optimizer/projections.py
```

This demonstrates:
- Creating sample projections based on draft group data
- Merging projections with DraftKings player data
- Finding top value plays (best points per $1k salary)

#### Custom Projection Usage

```python
from dfs_lineup_optimizer.projections import ProjectionManager
from draft_kings import Sport

manager = ProjectionManager()

# Load projections from CSV
df = manager.load_from_csv('projections.csv')

# Load projections from dict/list
projections = [
    {'player_id': '123', 'player_name': 'John Doe', 'projected_points': 45.5}
]
manager.load_from_dict(projections)

# Get top value plays for a draft group
top_values = manager.get_top_values(draft_group_id=148453, top_n=10)
```

#### CSV Format

Projection CSV files should have these columns:
- `player_id` - DraftKings player ID
- `player_name` - Player full name
- `team` - Team abbreviation (optional)
- `position` - Player position (optional)
- `salary` - Player salary (optional, from DK data)
- `projected_points` - Expected fantasy points
- `projected_value` - Points per $1k salary (calculated if not provided)

See `sample_projections.csv` for an example format.

### Custom Scripts

You can create your own scripts using the draft-kings client:

```python
from draft_kings import Client, Sport

client = Client()

# Get all contests for a sport
contests = client.contests(Sport.NBA)

# Get draftable players for a specific draft group
draftables = client.draftables(draft_group_id=148453)

# Get player availability and scoring data
players = client.available_players(draft_group_id=148453)
```

## Available Sports

- `Sport.NBA` - Basketball
- `Sport.NFL` - Football
- `Sport.MLB` - Baseball
- `Sport.NHL` - Hockey
- `Sport.PGA` - Golf
- `Sport.TENN` - Tennis
- `Sport.MMA` - Mixed Martial Arts
- `Sport.NASCAR` - NASCAR
- `Sport.EPL` - English Premier League (Soccer)
- `Sport.SOC` - Soccer

## Key Concepts

- **Contest ID**: Unique identifier for each individual contest
- **Draft Group ID**: Shared identifier for contests with the same player pool/slate
- **Salary**: Each player has a salary cost for lineups
- **Positions**: Players have eligible positions for lineup construction
- **Contest Types**: Classic (8-player, standard positions) vs Showdown (6-player, captain multiplier)
- **DK Scoring Rules**: Comprehensive scoring system with bonuses and multipliers

## Scripts

- `fetch_contests.py` - Fetch contest and player data from DraftKings
- `list_contests.py` - List upcoming NBA contests with entry counts (top 100 by entries)
- `projections.py` - Projection data integration and value play analysis
- `contest_detector.py` - Contest type detection and rules (Classic vs Showdown)
- `draftkings_scoring.py` - DK scoring calculator with realistic stat lines
- `comprehensive_analyzer.py` - Full contest analysis with proper rules
- `showdown_analyzer.py` - Showdown-specific analysis with captain optimization
- `analyze_showdown.py` - Showdown-specific analysis
- `analyze_with_pydfs.py` - pydfs-lineup-optimizer integration
- `analyze_with_dff.py` - Daily Fantasy Fuel style analysis

### List Upcoming Contests

List all upcoming NBA contests with entry counts:

```bash
python dfs_lineup_optimizer/list_contests.py
```

This displays:
- Top 100 Showdown contests by entry count
- Top 100 Classic contests by entry count
- Contest details including ID, draft group, start time, entries, prize pool, and guarantee status

### Showdown Analysis

Analyze the most popular NBA Showdown contest with captain optimization:

```bash
python dfs_lineup_optimizer/showdown_analyzer.py
```

This provides:
- Automatic selection of the showdown contest with most entries
- Proper DK scoring rules for NBA
- Captain optimization limited to starting players (top 8 by salary)
- 5 optimal lineups with different captain choices
- Player value rankings with UTIL and Captain values
- Results saved to a temporary file (not committed to git)

## Notes

- DraftKings does not have an official public API
- Uses unofficial endpoints that may change without notice
- Contest data is only available for current/upcoming slates