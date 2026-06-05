"""
NBA Showdown analyzer with captain optimization and proper DK scoring.
"""

from draft_kings import Client, Sport
from contest_detector import detect_contest_type, get_contest_info, display_contest_info
from draftkings_scoring import DKScoringCalculator, REALISTIC_STAT_LINES
from nba_rotations import get_rotation_status, get_estimated_minutes, get_actual_mpg, get_minutes_weight, is_starter, is_rotation_player
from pydfs_lineup_optimizer.player import Player
import tempfile
import sys
from datetime import datetime, timezone


def create_pydfs_players_with_scoring(draftables):
    """Create players with proper DK scoring, rotation role, and estimated minutes.

    Deduplicates players by name — keeps the lower-salary entry (best for Showdown utility).
    """
    calculator = DKScoringCalculator()

    # Get realistic stat lines
    stat_lines_by_name = {}
    for player_id, stats in REALISTIC_STAT_LINES.items():
        if stats.points > 0:
            stat_lines_by_name[player_id] = stats

    player_stat_mapping = {}

    # First pass: collect all player data, then deduplicate by name
    player_entries = {}  # full_name -> best entry dict

    for player in draftables.players:
        # Skip injured / unavailable
        if player.is_disabled:
            continue

        positions = player.position_name.split('/')
        full_name = player.name_details.display
        team = player.team_details.abbreviation
        fppg = None

        # Try to find by player ID
        for stat_id, stats in stat_lines_by_name.items():
            if str(player.player_id) == stat_id:
                fppg = calculator.calculate_fantasy_points(stats)
                break

        # Find by position and salary if no ID match
        if fppg is None:
            best_match = None
            best_diff = float('inf')

            for stat_id, stats in stat_lines_by_name.items():
                if stat_id not in player_stat_mapping:
                    expected_fppg = calculator.calculate_fantasy_points(stats)
                    expected_salary = expected_fppg * 200

                    # Position matching
                    pos_match = False
                    if 'C' in positions and stats.rebounds >= 8:
                        pos_match = True
                    elif 'PG' in positions and stats.assists >= 5:
                        pos_match = True
                    elif 'SG' in positions and stats.points >= 12:
                        pos_match = True
                    elif 'SF' in positions or 'PF' in positions:
                        pos_match = True

                    if pos_match:
                        salary_diff = abs(expected_salary - player.salary)
                        if salary_diff < best_diff:
                            best_diff = salary_diff
                            best_match = stat_id

            if best_match:
                fppg = calculator.calculate_fantasy_points(stat_lines_by_name[best_match])
                player_stat_mapping[best_match] = True

        # Fallback calculation
        if fppg is None:
            fppg = player.salary / 1000

        # Deduplicate by player name: keep the lower salary (UTIL/base price, not CPT 1.5x price).
        # For Showdown, DK lists CPT entries at 1.5x salary — those have inflated fppg
        # because the stat-line matching uses the higher CPT salary. Keep the UTIL entry's fppg.
        if full_name in player_entries:
            existing = player_entries[full_name]
            if player.salary < existing['salary']:
                # New entry has lower (UTIL) salary — replace entirely
                player_entries[full_name] = {
                    'player_id': str(player.player_id),
                    'first_name': player.name_details.first,
                    'last_name': player.name_details.last,
                    'positions': positions,
                    'team': team,
                    'salary': player.salary,
                    'fppg': fppg,
                }
            # else: existing entry already has the lower salary — skip the CPT duplicate
        else:
            player_entries[full_name] = {
                'player_id': str(player.player_id),
                'first_name': player.name_details.first,
                'last_name': player.name_details.last,
                'positions': positions,
                'team': team,
                'salary': player.salary,
                'fppg': fppg,
            }

    # Build final player list with rotation metadata
    pydfs_players = []
    player_meta = []

    for full_name, entry in player_entries.items():
        role = get_rotation_status(full_name, entry['team'])
        est_minutes = get_estimated_minutes(full_name, entry['team'], salary=entry['salary'])
        is_actual = get_actual_mpg(full_name, entry['team']) is not None

        pydfs_player = Player(
            player_id=entry['player_id'],
            first_name=entry['first_name'],
            last_name=entry['last_name'],
            positions=entry['positions'],
            team=entry['team'],
            salary=entry['salary'],
            fppg=entry['fppg'],
            is_injured=False,
        )
        pydfs_players.append(pydfs_player)
        player_meta.append({'role': role, 'minutes': est_minutes, 'mpg_actual': is_actual})

    return pydfs_players, player_meta


def generate_showdown_lineups(players, player_meta, n_lineups=5):
    """Generate optimal showdown lineups with captain optimization.

    Enforces the $50,000 salary cap strictly — skips lineups that exceed it.

    Args:
        players: List of Player objects
        player_meta: Parallel list of dicts with 'role' and 'minutes' for each player
        n_lineups: Number of lineups to generate
    """
    SALARY_CAP = 50000

    # Build name->meta lookup
    meta_by_name = {}
    for player, meta in zip(players, player_meta):
        meta_by_name[player.full_name] = meta

    # Sort players by adjusted value (minutes-prioritized)
    def adjusted_value(player):
        meta = meta_by_name.get(player.full_name, {'role': 'none', 'minutes': 8})
        raw_val = player.fppg / (player.salary / 1000) if player.salary > 0 else 0
        min_weight = get_minutes_weight(meta['minutes'])
        return raw_val * (0.4 + 0.6 * min_weight)

    sorted_players = sorted(players, key=adjusted_value, reverse=True)

    # Captain candidates: STARTERS ONLY (highest floor & ceiling for captain spot)
    captain_candidates = [p for p in sorted_players if meta_by_name.get(p.full_name, {}).get('role') == 'starter']
    # Deduplicate while preserving order
    seen = set()
    unique_captains = []
    for c in captain_candidates:
        if c.id not in seen:
            unique_captains.append(c)
            seen.add(c.id)
    captain_candidates = unique_captains

    lineups = []

    for captain in captain_candidates:
        if len(lineups) >= n_lineups:
            break

        captain_salary = captain.salary * 1.5
        remaining_salary = SALARY_CAP - captain_salary

        # Filter out the captain and players below $1,000 salary (invalid entries)
        util_pool = [p for p in sorted_players if p.id != captain.id and p.salary >= 1000]

        # Strategy: greedy by adjusted value, respecting salary cap
        selected = []
        current_salary = 0
        used_ids = {captain.id}

        for player in util_pool:
            if len(selected) >= 5:
                break
            if player.id not in used_ids and current_salary + player.salary <= remaining_salary:
                selected.append(player)
                current_salary += player.salary
                used_ids.add(player.id)

        # If we couldn't fill 5 spots, try filling with cheapest available
        if len(selected) < 5:
            cheapest = sorted([p for p in util_pool if p.id not in used_ids], key=lambda p: p.salary)
            for player in cheapest:
                if len(selected) >= 5:
                    break
                if current_salary + player.salary <= remaining_salary:
                    selected.append(player)
                    current_salary += player.salary
                    used_ids.add(player.id)

        # Only add lineup if we filled all 5 utility spots and are under cap
        if len(selected) == 5:
            total_salary = captain_salary + sum(p.salary for p in selected)
            if total_salary <= SALARY_CAP:
                lineup = {
                    'captain': captain,
                    'utility': selected,
                    'total_fppg': captain.fppg * 1.5 + sum(p.fppg for p in selected),
                    'total_salary': total_salary,
                    'captain_cap_salary': captain_salary,
                }
                lineups.append(lineup)

    return lineups


def main():
    """Main showdown analysis."""
    client = Client()
    contests_response = client.contests(Sport.NBA)

    # Find NBA showdown contests
    showdown_contests = []

    for contest in contests_response.contests:
        contest_type = detect_contest_type(contest.name)
        if contest_type.value == "showdown" and 'WNBA' not in contest.name:
            total_entries = contest.entries_details.total if hasattr(contest, 'entries_details') else 0
            showdown_contests.append((contest, contest.starts_at, total_entries))

    # Sort by total entries (most entries first), then by start time
    showdown_contests.sort(key=lambda x: (-x[2], x[1]))

    # Debug: print what we found
    print(f"DEBUG: Found {len(showdown_contests)} showdown contests")
    for contest, start_time, entries in showdown_contests[:5]:
        print(f"  - {contest.name} at {start_time} ({entries:,} entries)")

    if not showdown_contests:
        print("No upcoming NBA Showdown contests found.")
        return

    # Get the showdown contest with most entries
    contest, start_time, entries = showdown_contests[0]
    print(f"\nAnalyzing showdown contest with most entries: {entries:,} entries")

    print(f"Found next NBA SHOWDOWN contest starting at {start_time}\n")

    # Get contest info
    contest_info = get_contest_info(contest.contest_id, contest.name)
    display_contest_info(contest_info)

    # Display entry count
    print(f"Total Entries: {entries:,}")
    print("=" * 70)

    # Display DK scoring rules
    print("=" * 70)
    print("DRAFTKINGS NBA SCORING RULES")
    print("=" * 70)
    print("\nBase Scoring:")
    print("  Points: +1.0")
    print("  Rebounds: +1.25")
    print("  Assists: +1.5")
    print("  Steals: +2.0")
    print("  Blocks: +2.0")
    print("  Turnovers: -0.5")
    print("  3-Pointers Made: +0.5")
    print("  Double-Double: +1.5")
    print("  Triple-Double: +3.0")

    print("\nShowdown-Specific Rules:")
    print("  - Roster: 6 players (1 Captain + 5 UTIL)")
    print("  - Captain: 1.5x multiplier on BOTH points AND salary")
    print("  - Salary Cap: $50,000")
    print("  - Captain counts as 1.5 spots in salary calculation")
    print("=" * 70)

    # Get player data
    draftables = client.draftables(contest.draft_group_id)
    print(f"\nFound {len(draftables.players)} draftable players\n")

    # Create players with proper scoring + rotation metadata
    players, player_meta = create_pydfs_players_with_scoring(draftables)

    # Build name->meta lookup for lineup display
    meta_by_name = {}
    for player, meta in zip(players, player_meta):
        meta_by_name[player.full_name] = meta

    # Generate showdown lineups
    print("Generating optimal showdown lineups with captain optimization...")
    lineups = generate_showdown_lineups(players, player_meta, n_lineups=5)

    print(f"\nGenerated {len(lineups)} optimal showdown lineups\n")

    # Display showdown lineups with role/minutes info
    for i, lineup in enumerate(lineups, 1):
        cpt_meta = meta_by_name.get(lineup['captain'].full_name, {'role': 'none', 'minutes': 8, 'mpg_actual': False})
        mpg_src = "MPG" if cpt_meta.get('mpg_actual') else "est"
        print(f"Lineup {i}:")
        print(f"  Captain: {lineup['captain'].full_name}")
        print(f"    Team: {lineup['captain'].team}  |  Role: {cpt_meta['role'].upper()}  |  {cpt_meta['minutes']:.1f} {mpg_src} min")
        print(f"    Base Salary: ${lineup['captain'].salary:,}")
        print(f"    Captain Salary: ${lineup['captain'].salary * 1.5:,.0f} (1.5x multiplier)")
        print(f"    Base FPPG: {lineup['captain'].fppg:.1f}")
        print(f"    Captain FPPG: {lineup['captain'].fppg * 1.5:.1f} (1.5x multiplier)")
        print(f"    Value: {(lineup['captain'].fppg / (lineup['captain'].salary / 1000)):.1f}X")

        print(f"\n  Utility Players (5 spots):")
        for j, util in enumerate(lineup['utility'], 1):
            value = util.fppg / (util.salary / 1000) if util.salary > 0 else 0
            util_meta = meta_by_name.get(util.full_name, {'role': 'none', 'minutes': 8, 'mpg_actual': False})
            role_tag = util_meta['role'].upper()[:3]
            mpg_src = "MPG" if util_meta.get('mpg_actual') else "est"
            print(f"    {j}. {util.full_name:<25} {util.team:3}  ${util.salary:>6,}  {util.fppg:5.1f} fppg  {util_meta['minutes']:.1f}{mpg_src[:1]}  {role_tag}  ({value:.1f}X)")

        print(f"\n  Lineup Totals:")
        print(f"    Total FPPG: {lineup['total_fppg']:.1f}")
        print(f"    Total Salary: ${lineup['total_salary']:,.0f}")
        print(f"    Salary Cap Remaining: ${50000 - lineup['total_salary']:,.0f}")
        print()

    # ── Minutes-prioritized player value rankings ──
    print("=" * 95)
    print("PLAYER VALUE RANKINGS (Minutes-Prioritized)")
    print("  Sorted by Adjusted Value = (fppg * minutes_weight) / (salary/1k)")
    print("  MPG = actual season minutes; est = role-based estimate")
    print("  Starters & high-minute players ranked higher for DFS reliability")
    print("=" * 95)

    player_rankings = []
    for idx, player in enumerate(players):
        if player.salary > 0:
            meta = player_meta[idx]
            role = meta['role']
            minutes = meta['minutes']
            mpg_actual = meta.get('mpg_actual', False)
            raw_value = player.fppg / (player.salary / 1000)
            min_weight = get_minutes_weight(minutes)
            adjusted_value = raw_value * (0.4 + 0.6 * min_weight)
            player_rankings.append({
                'player': player,
                'role': role,
                'minutes': minutes,
                'mpg_actual': mpg_actual,
                'raw_value': raw_value,
                'adjusted_value': adjusted_value,
                'min_weight': min_weight,
            })

    # Sort by adjusted value (minutes-prioritized)
    player_rankings.sort(key=lambda x: x['adjusted_value'], reverse=True)

    print(f"\n{'#':>3}  {'Player':<25} {'Pos':<8} {'Team':<4} {'Salary':>7} {'FPPG':>6} "
          f"{'Min':>6} {'Role':<4} {'Raw':>5} {'Adj':>5} {'Cpt':>5}")
    print("-" * 95)

    for i, r in enumerate(player_rankings[:20], 1):
        p = r['player']
        pos_str = '/'.join(p.positions)
        role_tag = r['role'][:3].upper()
        mpg_src = "MPG" if r['mpg_actual'] else "est"
        capt_adj = r['adjusted_value'] * 1.5
        print(f"{i:3}. {p.full_name:<25} {pos_str:<8} {p.team:<4} ${p.salary:>6,}  {p.fppg:5.1f} "
              f" {r['minutes']:5.1f}{mpg_src[0]}  {role_tag:<4} {r['raw_value']:5.1f}X {r['adjusted_value']:5.1f}X {capt_adj:5.1f}X")

    # ── Role-separated view ──
    starters = [r for r in player_rankings if r['role'] == 'starter']
    rotation = [r for r in player_rankings if r['role'] == 'rotation']
    bench = [r for r in player_rankings if r['role'] == 'none']

    print("\n" + "=" * 95)
    print("STARTERS (Projected 30+ min — highest floor & ceiling)")
    print("=" * 95)
    for i, r in enumerate(starters[:10], 1):
        p = r['player']
        pos_str = '/'.join(p.positions)
        capt_adj = r['adjusted_value'] * 1.5
        mpg_src = "MPG" if r['mpg_actual'] else "est"
        print(f"{i:3}. {p.full_name:<25} {pos_str:<8} {p.team:<4} ${p.salary:>6,}  {p.fppg:5.1f} fppg  "
              f"{r['minutes']:5.1f}{mpg_src[0]}  {r['raw_value']:5.1f}X raw  {r['adjusted_value']:5.1f}X adj  {capt_adj:5.1f}X cpt")

    print("\n" + "=" * 95)
    print("ROTATION (Projected 18-25 min — moderate role)")
    print("=" * 95)
    for i, r in enumerate(rotation[:10], 1):
        p = r['player']
        pos_str = '/'.join(p.positions)
        capt_adj = r['adjusted_value'] * 1.5
        mpg_src = "MPG" if r['mpg_actual'] else "est"
        print(f"{i:3}. {p.full_name:<25} {pos_str:<8} {p.team:<4} ${p.salary:>6,}  {p.fppg:5.1f} fppg  "
              f"{r['minutes']:5.1f}{mpg_src[0]}  {r['raw_value']:5.1f}X raw  {r['adjusted_value']:5.1f}X adj  {capt_adj:5.1f}X cpt")

    if bench:
        print("\n" + "=" * 95)
        print("DEEP BENCH (Projected <10 min — high risk, low minutes)")
        print("=" * 95)
        for i, r in enumerate(bench[:10], 1):
            p = r['player']
            pos_str = '/'.join(p.positions)
            capt_adj = r['adjusted_value'] * 1.5
            mpg_src = "MPG" if r['mpg_actual'] else "est"
            print(f"{i:3}. {p.full_name:<25} {pos_str:<8} {p.team:<4} ${p.salary:>6,}  {p.fppg:5.1f} fppg  "
                  f"{r['minutes']:5.1f}{mpg_src[0]}  {r['raw_value']:5.1f}X raw  {r['adjusted_value']:5.1f}X adj  {capt_adj:5.1f}X cpt")

    # Captain optimization analysis — now prioritizes starters
    print("\n" + "=" * 95)
    print("CAPTAIN OPTIMIZATION ANALYSIS (Starters Prioritized)")
    print("=" * 95)

    print("\nBest Captain Candidates (by adjusted value):")
    # Use adjusted value for captain candidates, starters first
    captain_candidates = sorted(player_rankings, key=lambda r: r['adjusted_value'], reverse=True)
    for i, r in enumerate(captain_candidates[:6], 1):
        p = r['player']
        pos_str = '/'.join(p.positions)
        cpt_fppg = p.fppg * 1.5
        cpt_salary = p.salary * 1.5
        cpt_value = cpt_fppg / (cpt_salary / 1000)
        mpg_src = "MPG" if r['mpg_actual'] else "est"
        print(f"{i}. {p.full_name:<25} {pos_str:8}  ({r['role'].upper()[:3]}, {r['minutes']:.1f}{mpg_src[0]} min)")
        print(f"   UTIL: {p.fppg:5.1f} fppg @ ${p.salary:>6,} ({r['raw_value']:.1f}X raw, {r['adjusted_value']:.1f}X adj)")
        print(f"   CPT:  {cpt_fppg:5.1f} fppg @ ${cpt_salary:>6,} ({cpt_value:.1f}X)")

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    import os
    # Save output to temp file AND a persistent project file
    original_stdout = sys.stdout

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix='nba_showdown_') as temp_file:
        temp_path = temp_file.name

        class MultiOutput:
            def __init__(self, file1, file2):
                self.file1 = file1
                self.file2 = file2

            def write(self, text):
                self.file1.write(text)
                self.file2.write(text)

            def flush(self):
                self.file1.flush()
                self.file2.flush()

        sys.stdout = MultiOutput(original_stdout, temp_file)

        try:
            main()
            print(f"\nResults saved to: {temp_path}")
        finally:
            sys.stdout = original_stdout

    print(f"\nResults saved to temporary file: {temp_path}")

    # Also save a persistent copy in the output subdirectory
    from datetime import datetime as dt
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'output')
    os.makedirs(output_dir, exist_ok=True)
    timestamp = dt.now().strftime('%Y-%m-%d_%H%M%S')
    persistent_path = os.path.join(output_dir, f"nba_showdown_{timestamp}.txt")
    with open(persistent_path, 'w', encoding='utf-8') as f:
        with open(temp_path, 'r', encoding='utf-8', errors='replace') as tmp:
            f.write(tmp.read())
    print(f"Results also saved to: {persistent_path}")