# Horse Race Predictor

Two modes:

1. **Manual-input consensus predictor** (`predictor.py`) — for a today's race,
   aggregate expert picks from multiple sources into a single consensus best pick
   plus a ranked table. Picks and entries are stored in SQLite so official results
   can later be reconciled and per-source accuracy tracked.
2. **Automated backtest pipeline** (`weekly_runner.py`) — run a window of dates
   across many tracks, score a slate of naive baselines (MLO favorite, post
   position, random, leading jockey/trainer, …) against HRN results, and emit an
   HTML accuracy report with per-source hit rates and ROI. No manual picks needed.

## How it works (manual-input predictor)

1. You identify a race: track, race number, date (default today).
2. You supply the **field** (entries) and **picks** from any sources you read
   (DRF, Brisnet, ABR, a tipster, your own handicapping). See *Data input* below.
3. The engine maps every source's selections onto the field, scores them
   (1st = 5 pts, 2nd = 3, 3rd = 1), and sums per horse.
4. **Best pick** = highest points; tiebreak by #1-vote count, then lower
   morning-line odds. **Confidence** = share of sources whose top pick is the
   best pick, plus the point margin to 2nd.
5. After the race goes official, you enter the finish order; the program scores
   each source's top pick (win/place/show) and the consensus pick, and reports
   hit rates across all tracked races.

## Quick start (manual-input predictor)

```powershell
# Install dependencies (from project root)
uv sync

# Predict a race: fetch the live field from HRN (auto-detects scratches/MTO),
# supply your own picks. No need to paste the entries.
python horse_race_predictor/predictor.py predict `
  --track SAR --race 3 `
  --picks "shapiro:7,6 | hrn_power:11 | betting_news:7 | vsin:10"

# Or supply both field and picks manually (works offline)
python horse_race_predictor/predictor.py predict `
  --track SAR --race 1 `
  --field "1:Speed Star:5/2, 2:Lazy Day:3/1, 3:Midnight Run:2/1, 4:Long Shot Lou:20/1" `
  --picks "drf_free:1,3,2 | abr:3,1,2 | my_tip:Speed Star,2"

# Mark extra late scratches and re-run
python horse_race_predictor/predictor.py predict --track SAR --race 1 --scratch "3,7" --picks "..."

# Enter official results and score accuracy
python horse_race_predictor/predictor.py results --track SAR --race 1 --finish "3,1,2"

# Per-source + consensus accuracy across all scored races
python horse_race_predictor/predictor.py summary

# Inspect a stored race (entries, per-source picks, result, accuracy)
python horse_race_predictor/predictor.py detail 1

# List configured sources
python horse_race_predictor/predictor.py sources
```

## Scratches, MTO, and also-eligible

The HRN entries source tags every horse with a status and the predictor accounts
for it before building the consensus:

- **scratched** — always excluded from the consensus. Picks targeting a scratched
  horse are **voided** and reported (they don't count against a source's confidence).
- **mto** (Main Track Only) — excluded by default (only run if the race moves off
  turf to dirt). Include with `--include-mto` for an off-turf scenario.
- **ae** (Also Eligible) — excluded by default (draw in only if a body horse
  scratches). Include with `--include-ae`.
- **in** — in the active field.

`--scratch "3,7"` marks additional program numbers as scratched (manual late-scratch
override, e.g. a scratch not yet reflected on the source page).

The predict output prints the full field with status flags, the excluded horses
and why, and any voided picks, so the consensus is fully transparent about what
was included.

## Data input

Entries come from the web by default (HRN, which is server-rendered and not
bot-walled) or manually via `--field`/`--input`. Picks come from manual `--picks`
or `--input` (the web pick sources are best-effort stubs — see *Web sources*).
You can mix: web entries + manual picks is the typical flow.

Free pick sources (Equibase picks pages, DRF) are bot-walled or JavaScript-rendered,
so plain HTTP scraping rarely returns live data. **Manual input is the primary
path** and works reliably; the web fetchers remain as best-effort add-ons.

### Inline (quick)

- `--field "1:Speed Star:5/2, 2:Lazy Day:3/1, ..."`
  `program_number:horse_name:morning_line_odds`. MLO accepts `5/2`, `5-2`, or
  `5.0`; omit it for unknown odds.
- `--picks "source:tok1,tok2,tok3 | source:..."`
  Rank by order (1st, 2nd, 3rd). Each token matches an entry by **program
  number first, then horse name** (fuzzy). Use any source label you like
  (`drf_free`, `abr`, `my_tip`, etc.).

### Structured JSON file (`--input FILE`)

```json
{
  "track": "SAR", "race": 1, "date": "2026-07-04",
  "entries": [
    {"program_number": "1", "horse_name": "Speed Star", "morning_line_odds": 5.0,
     "jockey": "J. Ortiz", "trainer": "T. Pletcher", "post_position": 1}
  ],
  "picks": [
    {"source": "drf_free", "picks": [
        {"program_number": "1", "rank": 1},
        {"program_number": "3", "rank": 2}
    ]}
  ],
  "results": ["3", "1", "2"]
}
```

The `results` section is optional and accepts either an ordered list of program
numbers (finish order) or full result dicts with `finish_position`/payoffs. Use
this file with both `predict` (entries+picks) and `results` (results section).

### Finish order (`results` command)

`--finish "3,1,2"` - program numbers in finish order. Horse names are resolved
from the stored entries.

## Commands

| Command | Purpose |
|---|---|
| `predict` | Build the consensus for a race and persist it. `--track/--race` required (or `--input`). |
| `results` | Enter/fetch official finish order, score source + consensus accuracy. |
| `accuracy` | Recompute accuracy snapshots for all stored races with results. |
| `summary` | Console table of per-source and consensus win/place/show hit rates. |
| `detail <race_id>` | Full race card: entries, per-source picks, result, accuracy. |
| `sources` | List configured entry/pick/result sources. |

## Storage

SQLite at `horse_race_predictor/horse_tracker.db`. Override with the
`HRP_DB_PATH` env var (used by the test suite to point at a throwaway DB).
Tables: `races`, `entries`, `picks`, `results`, `accuracy_snapshots`, `reports`.
The DB file is gitignored; `reports/sample_output.html` is the checked-in sample.

## Web sources

`sources/` contains swappable fetchers behind a registry:

- **`hrn` (entries + results)** — Horse Racing Nation entries/results page.
  Server-rendered HTML, not bot-walled, so this is the **primary live entries
  source** (detects scratches / MTO / also-eligible) and the **primary results
  source** for the backtest (top-4 finish order + $2 WPS payoffs from the payouts
  tables, free and unlimited). `fetch_card_and_results` parses both off one fetch.
  Used by default for `predict` when `--field`/`--input` are not supplied.
- `equibase` (entries) and `equibase_results` (results) — best-effort fallbacks;
  Equibase is bot-walled, so these usually return `[]`.
- `drf_free` / `brisnet_free` / `abr` (picks) — best-effort stubs; free pick
  pages are JS-rendered/bot-walled, so live pick fetch usually returns nothing.
  Supply picks manually via `--picks` instead.

Each source degrades gracefully (returns `[]`, never raises). To make live pick
fetch work, wire a headless browser (Playwright) or a paid API key slot - the
registry makes either a drop-in change. Add new tracks' HRN URL slugs in
`sources/hrn.py` (`TRACK_SLUGS`).

## Automated backtest pipeline

`weekly_runner.py` runs a fully automated predict → results → score → report
backtest across a date window and a set of tracks, using only naive baselines
(no manual picks). It's the way to measure how a set of generic handicapping
rules performs at scale.

### Predictors compared

All computed from the entry sheet (Phase 1) or from meet standings-as-of (Phase 2c):

| Source | Rule |
|---|---|
| `mlo_baseline` | Morning-line favorite (lowest MLO). |
| `mlo_second` / `mlo_third` | 2nd / 3rd choice by MLO. |
| `mlo_longshot` | Highest MLO (longshot). |
| `post_position_baseline` / `post_position_outside` | Lowest (inside) / highest (outside) post. |
| `random_baseline` | Seeded random pick (deterministic per race identity). |
| `leading_jockey` / `leading_trainer` | Track's win-leader jockey/trainer as of the race date (prior results only — no look-ahead, computed from our own DB via an O(n) running tally). |
| `consensus` | Rank-point blend across the baselines. |

### Running a backtest

```powershell
# Auto-select tracks whose 2026 meets overlap the window (recommended)
python horse_race_predictor/weekly_runner.py `
  --start 2026-06-20 --end 2026-07-03 --auto-tracks

# Or name tracks explicitly
python horse_race_predictor/weekly_runner.py `
  --start 2026-06-27 --end 2026-07-03 --tracks CD,SAR,GP

# Skip the slow per-race parse.bot fallback (BloodHorse/Equibase) for gaps HRN missed
python horse_race_predictor/weekly_runner.py --start ... --end ... --auto-tracks --skip-equibase-fill
```

Phases:
1. **Predict** — one HRN fetch per (track, date) yields the card's entries + MLO +
   post position + scratch status; pure baselines are saved for every race. The
   same page's payouts tables are cached for Phase 2.
2. **Results** — HRN's payouts tables give full top-4 finish order + $2 WPS payoffs
   for every run race (free, unlimited, no API budget). Scored immediately.
3. **2b** *(optional)* — parse.bot fallback for any race HRN didn't populate.
4. **2c** — leading-jockey/trainer picks from meet standings-as-of (running tally,
   same-date races mutually excluded so no look-ahead), then re-score.
5. **Report** — HTML accuracy report saved to `reports/backtest_<start>_<end>.html`
   and the DB `reports` table (key `weekly_accuracy`).

Per-phase timings are appended to `reports/timings.log` and rendered in the report.

### Track schedule (`schedule.py`)

Encodes 2026 meet date ranges for 33 major US/CA thoroughbred tracks (NYRA, KHRC,
track press releases). `--auto-tracks` uses `active_tracks(start, end)` so only
tracks in session during the window are fetched — no wasted dark-day fetches.
Golden Gate is omitted (closed 2024); Belmont is closed through Sept 2026
(renovation), with Aqueduct running NYRA spring/summer as "Belmont at the Big A".

### Report output

`report.py` generates the HTML report: a per-source comparison table (win/place/show
hit rates + ROI on a $2 win bet), a per-track breakdown, and a per-race detail table
that collapses on **track + date** (one rowspan "Card" cell per card, ~92 cards vs
796 repeated labels). A sample is checked in at
[`reports/sample_output.html`](reports/sample_output.html) (generated from
2026-07-03, GP + SAR). The real per-window reports are gitignored — reproducible
from the DB + `weekly_runner.py` anytime.

Helper scripts:
- `backfill_baselines.py` — recompute all baseline picks + re-score (no re-fetch).
- `refill_results_hrn.py` — re-fetch results from HRN into the existing DB.
- `finish_autotracks.py` — one-off finisher that completes a stopped run from DB state.

### Key finding (2-week 2026-06-20→07-03, 796 scored races across 16 tracks)

MLO favorite is the best win-rate predictor at scale (28% win); MLO 2nd-choice is
the best ROI (-22.8% — the value sweet spot). The favorite-longshot bias holds:
longshots are overbet (MLO longshot -35.1% ROI). High win rate ≠ good ROI.

## Tests

```powershell
python -m pytest horse_race_predictor/tests/ -q
```

Covers DB round-trips, consensus scoring/tiebreaks/fuzzy matching, accuracy
reconciliation, the synthetic baselines (MLO variants, post-position, random,
connections/standings), the HRN results parser, and the track schedule. The test
suite uses an isolated temp DB via `HRP_DB_PATH`.

## Notes

- US thoroughbred only. Track codes follow Equibase (SAR, SA, BEL, MTH, GP, ...);
  full names like `Saratoga` are accepted via an alias map in `race.py`.
- Scoring weights (`PTS_FIRST=5`, `PTS_SECOND=3`, `PTS_THIRD=1`) are configurable
  constants in `consensus.py`.