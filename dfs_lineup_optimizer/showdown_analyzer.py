"""
NBA Showdown analyzer with captain optimization and proper DK scoring.
"""

from draft_kings import Client, Sport
from contest_detector import detect_contest_type, get_contest_info, display_contest_info
from nba_rotations import get_minutes_weight
from player_builder import create_pydfs_players_with_scoring
from utils import SALARY_CAP, display_scoring_rules, run_and_save, get_draftkings_client
from datetime import datetime, timezone


def generate_showdown_lineups(players, player_meta, n_lineups=5):
    """Generate optimal showdown lineups with captain optimization.

    Uses exhaustive combinatorial enumeration to find the best possible lineups.
    Delegates to lineup_optimizer for the actual search.
    Excludes deep bench players (role='none') from lineups entirely.

    Args:
        players: List of Player objects
        player_meta: Parallel list of dicts with 'role' and 'minutes' for each player
        n_lineups: Number of lineups to generate
    """
    from lineup_optimizer import generate_optimal_showdown_lineups

    return generate_optimal_showdown_lineups(
        players, player_meta, n_lineups=n_lineups,
        captain_filter=None,  # Uses starter-only default from player_meta
        min_util_salary=1000,
        exclude_roles={'none'},  # Exclude deep bench players (no rotation role)
        min_util_fppg=7.0,  # Filter out low-production punt plays with inflated value ratios
    )


def main():
    """Main showdown analysis."""
    client = get_draftkings_client()

    try:
        contests_response = client.contests(Sport.NBA)
    except Exception as e:
        print(f"ERROR: Failed to fetch contests from DraftKings API: {e}")
        print("This may be due to a network issue or API change. Please try again later.")
        return

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
    display_scoring_rules(contest_type="showdown")

    # Get player data
    try:
        draftables = client.draftables(contest.draft_group_id)
    except Exception as e:
        print(f"ERROR: Failed to fetch draftable players: {e}")
        return
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
    run_and_save(main, prefix='nba_showdown_', output_dir='output')