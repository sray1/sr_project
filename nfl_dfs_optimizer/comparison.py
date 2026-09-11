"""
Side-by-side lineup comparison across projection sources.

For one contest, every available projection source (manual CSV, each scrape
fetcher that yields data — e.g. DailyFantasyFuel — and the salary-implied
fallback) is resolved independently, and the SAME exact optimizer builds the
top lineup from each. The report shows each source's optimal lineup plus
pairwise differences against the baseline: the source the main pipeline
would have picked (CSV > first working scrape > fallback).

Used via:  python analyzer.py --compare [--mode ...] [--csv ...]
"""

from projections import (get_source_projections, FETCHER_REGISTRY,
                         normalize_name)
from player_builder import (build_player_pool, build_pydfs_players,
                             build_auto_exclusions)
from showdown_optimizer import generate_showdown_lineups
from classic_optimizer import generate_classic_lineups, lineup_to_dict

ROSTER_SIZE = {'showdown': 6, 'classic': 9}


def baseline_source_name(source_projections):
    """Which source the main pipeline would use (first-wins semantics).

    Mirrors get_player_projections: manual CSV beats any scrape; scrapers
    win in FETCHER_REGISTRY order; fallback when nothing else is available.
    """
    if 'csv' in source_projections:
        return 'csv'
    for name, _ in FETCHER_REGISTRY:
        if name in source_projections:
            return name
    return 'fallback'


def build_lineups_per_source(draftables, source_projections, mode,
                             stack_rule='qbwr', allow_dst_captain=True,
                             drop_backup_qbs=True, exclude=None):
    """Build the top lineup for each source with the same exact optimizer.

    Args:
        draftables: Raw player dicts from dk_client.fetch_draftables
        source_projections: {source: {player_id: {'projection', 'source'}}}
            from projections.get_source_projections
        mode: 'showdown' or 'classic'
        stack_rule: Classic stacking rule (same choices as analyzer --stack)
        allow_dst_captain: Showdown flag (same as analyzer --no-dst-captain)
        drop_backup_qbs: Pool filter (same as analyzer --keep-backup-qbs)
        exclude: Pool filter (same as analyzer --exclude)

    Returns:
        {source_name: lineup} — a showdown lineup dict or a classic
        lineup_to_dict dict; sources with no feasible lineup are omitted.
    """
    lineups_by_source = {}
    filter_note_printed = False  # print pool-filter drops once, not per source
    for source, projections in source_projections.items():
        try:
            pool = build_player_pool(draftables, projections,
                                     drop_backup_qbs=drop_backup_qbs,
                                     exclude=exclude,
                                     verbose=not filter_note_printed)
            filter_note_printed = True
            if mode == 'showdown':
                lineups = generate_showdown_lineups(
                    pool, n_lineups=1, allow_dst_captain=allow_dst_captain)
            else:
                pydfs_players = build_pydfs_players(pool)
                lineups = [lineup_to_dict(l) for l in generate_classic_lineups(
                    pydfs_players, n_lineups=1, stack_rule=stack_rule)]
        except Exception as e:
            print(f"  {source}: optimizer failed ({e}) — skipped")
            continue
        if lineups:
            lineups_by_source[source] = lineups[0]
    return lineups_by_source


def lineup_player_names(lineup, mode):
    """Ordered player names of a lineup (captain first for showdown)."""
    if mode == 'showdown':
        return [lineup['captain']['name']] + [p['name'] for p in lineup['flex']]
    return [p['name'] for p in lineup['players']]


def _lineup_captain_name(lineup, mode):
    return lineup['captain']['name'] if mode == 'showdown' else None


def compare_against_baseline(lineup, baseline_lineup, mode):
    """Compare one source's lineup against the baseline lineup."""
    names = {normalize_name(n) for n in lineup_player_names(lineup, mode)}
    base_names = {normalize_name(n) for n in
                  lineup_player_names(baseline_lineup, mode)}
    shared = names & base_names
    unique = [n for n in lineup_player_names(lineup, mode)
              if normalize_name(n) not in base_names]
    return {
        'overlap': len(shared),
        'roster_size': ROSTER_SIZE[mode],
        'unique_picks': unique,
        'same_captain': (_lineup_captain_name(lineup, mode) ==
                         _lineup_captain_name(baseline_lineup, mode))
                        if mode == 'showdown' else None,
    }


def _matched_count(source_projection):
    """Players this source actually matched (not salary-fallback)."""
    return sum(1 for v in source_projection.values() if v['source'] != 'fallback')


def _print_lineup(lineup, mode):
    if mode == 'showdown':
        captain = lineup['captain']
        print(f"  {lineup['total_projection']:.2f} projected | "
              f"${lineup['total_salary']:,.0f} salary")
        print(f"  CPT  {captain['name']:<25} {captain['team']:<5} "
              f"${captain['salary'] * 1.5:>8,.0f}")
        for player in lineup['flex']:
            print(f"  FLEX {player['name']:<25} {player['team']:<5} "
                  f"${player['salary']:>8,.0f}")
    else:
        print(f"  {lineup['total_projection']:.2f} projected | "
              f"${lineup['total_salary']:,.0f} salary")
        for player in lineup['players']:
            print(f"  {player['lineup_position']:<5} {player['name']:<25} "
                  f"{player['team']:<5} ${player['salary']:>8,.0f}")


def print_comparison(lineups_by_source, source_projections, mode, baseline,
                     draftables):
    """Print the full comparison report."""
    roster = ROSTER_SIZE[mode]
    print(f"\n{'=' * 70}")
    print(f"PROJECTION SOURCE COMPARISON (baseline: {baseline})")
    print(f"{'=' * 70}")

    # Per-source lineup blocks
    for source, lineup in lineups_by_source.items():
        matched = _matched_count(source_projections[source])
        total = len(source_projections[source])
        marker = '  [baseline]' if source == baseline else ''
        print(f"\n--- {source}{marker} ({matched}/{total} players matched) ---")
        _print_lineup(lineup, mode)

    if baseline not in lineups_by_source:
        print(f"\n(baseline source '{baseline}' produced no feasible lineup)")
        return

    # Comparison table vs baseline
    base_lineup = lineups_by_source[baseline]
    print(f"\n{'=' * 70}")
    print(f"COMPARISON vs BASELINE ({baseline})")
    print(f"{'=' * 70}")
    header = (f"  {'source':<22} {'proj':>7} {'salary':>9} "
              f"{'overlap':>8} {'unique':>7}")
    if mode == 'showdown':
        header += f" {'cpt':>6}"
    print(header)
    for source, lineup in lineups_by_source.items():
        cmp = compare_against_baseline(lineup, base_lineup, mode)
        row = (f"  {source:<22} {lineup['total_projection']:>7.2f} "
               f"${lineup['total_salary']:>8,.0f} "
               f"{cmp['overlap']}/{roster:>4} {len(cmp['unique_picks']):>7}")
        if mode == 'showdown':
            row += f" {'same' if cmp['same_captain'] else 'DIFF':>6}"
        print(row)

    # Unique picks per non-baseline source
    for source, lineup in lineups_by_source.items():
        if source == baseline:
            continue
        cmp = compare_against_baseline(lineup, base_lineup, mode)
        if cmp['unique_picks']:
            print(f"  {source} unique picks: {', '.join(cmp['unique_picks'])}")

    # Per-player projection disagreements for the baseline lineup's players
    name_to_id = {normalize_name(p['name']): p['player_id'] for p in draftables}
    base_names = lineup_player_names(base_lineup, mode)
    print(f"\nPER-PLAYER PROJECTIONS (baseline lineup, per source):")
    other_sources = [s for s in lineups_by_source if s != baseline]
    print(f"  {'player':<25} {baseline:>10} " +
          ' '.join(f"{s[:10]:>10}" for s in other_sources))
    for name in base_names:
        pid = name_to_id.get(normalize_name(name))
        if pid is None:
            continue
        row = f"  {name:<25} "
        for src in [baseline] + other_sources:
            proj = source_projections[src].get(pid, {}).get('projection')
            row += f"{proj:>10.2f} " if proj is not None else f"{'--':>10} "
        print(row)


def run_comparison(args, contest, mode, draftables):
    """Resolve every source, build one optimal lineup per source, and report.

    Args:
        args: Parsed analyzer CLI args (csv, week, no_scrape, stack,
              no_dst_captain, exclude, keep_backup_qbs are honored)
        contest: Selected contest object
        mode: 'showdown' or 'classic'
        draftables: Raw draftables from dk_client.fetch_draftables

    Returns:
        {source_name: lineup} (also printed)
    """
    source_projections = get_source_projections(
        draftables, csv_path=args.csv, week=args.week,
        allow_scrape=not args.no_scrape)

    # getattr: callers (tests, tracker) may use simpler args namespaces
    exclude = build_auto_exclusions(
        draftables, getattr(args, 'exclude', None),
        allow_scrape=not args.no_scrape)
    lineups_by_source = build_lineups_per_source(
        draftables, source_projections, mode,
        stack_rule=args.stack, allow_dst_captain=not args.no_dst_captain,
        drop_backup_qbs=not getattr(args, 'keep_backup_qbs', False),
        exclude=exclude)

    if not lineups_by_source:
        print("No source produced a feasible lineup — nothing to compare")
        return lineups_by_source

    baseline = baseline_source_name(source_projections)
    if baseline not in lineups_by_source:
        # Baseline had no feasible lineup; fall back to the first that did
        baseline = next(iter(lineups_by_source))
    print_comparison(lineups_by_source, source_projections, mode, baseline,
                     draftables)
    return lineups_by_source