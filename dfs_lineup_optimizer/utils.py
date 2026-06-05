"""
Shared utilities for DFS lineup optimizer scripts.

Consolidates duplicated code across the project:
- MultiOutput: dual-stream output (stdout + file)
- SALARY_CAP: standard DK salary cap constant
- display_scoring_rules(): DK scoring rules display
- run_and_save(): boilerplate for running main() and saving output
"""

import os
import sys
import tempfile
from datetime import datetime as dt

# Standard DraftKings salary cap for both Classic and Showdown
SALARY_CAP = 50000


class MultiOutput:
    """Write to both stdout and a file simultaneously."""

    def __init__(self, file1, file2):
        self.file1 = file1
        self.file2 = file2

    def write(self, text):
        self.file1.write(text)
        self.file2.write(text)

    def flush(self):
        self.file1.flush()
        self.file2.flush()


def display_scoring_rules(contest_type="showdown"):
    """Display DraftKings NBA scoring rules.

    Args:
        contest_type: "showdown" or "classic"
    """
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

    if contest_type == "showdown":
        print("\nShowdown-Specific Rules:")
        print("  - Roster: 6 players (1 Captain + 5 UTIL)")
        print("  - Captain: 1.5x multiplier on BOTH points AND salary")
        print("  - Salary Cap: $50,000")
        print("  - Captain counts as 1.5 spots in salary calculation")
    else:
        print("\nClassic-Specific Rules:")
        print("  - Roster: 8 players (PG/SG/SF/PF/C positions)")
        print("  - Salary Cap: $50,000")
        print("  - Standard position requirements")

    print("=" * 70)


def run_and_save(main_func, prefix='dfs_output_', output_dir=None):
    """Run a main function while saving output to both stdout and a file.

    Handles the tempfile + dual-output + try/finally pattern used by all scripts.

    Args:
        main_func: Callable that produces output to stdout.
        prefix: Filename prefix for the temp file.
        output_dir: Directory for the persistent copy. If None, only saves to temp file.
                     If 'output', saves to the script's output/ subdirectory.

    Returns:
        Path to the persistent output file, or temp file path if no output_dir.
    """
    original_stdout = sys.stdout

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix=prefix) as temp_file:
        temp_path = temp_file.name
        sys.stdout = MultiOutput(original_stdout, temp_file)

        try:
            main_func()
            print(f"\nResults saved to: {temp_path}")
        finally:
            sys.stdout = original_stdout

    print(f"\nResults saved to temporary file: {temp_path}")

    # Save a persistent copy if output_dir is specified
    persistent_path = None
    if output_dir == 'output':
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        out_dir = os.path.join(script_dir, 'output')
        os.makedirs(out_dir, exist_ok=True)
        timestamp = dt.now().strftime('%Y-%m-%d_%H%M%S')
        # Derive base name from prefix
        base_name = prefix.rstrip('_').replace('_', '_')
        persistent_path = os.path.join(out_dir, f"{base_name}{timestamp}.txt")
        with open(persistent_path, 'w', encoding='utf-8') as f:
            with open(temp_path, 'r', encoding='utf-8', errors='replace') as tmp:
                f.write(tmp.read())
        print(f"Results also saved to: {persistent_path}")

    return persistent_path or temp_path


def get_draftkings_client(max_retries=3, retry_delay=2):
    """Create a DraftKings API client with retry logic.

    Args:
        max_retries: Number of connection attempts.
        retry_delay: Seconds between retries.

    Returns:
        A draft_kings.Client instance.

    Raises:
        SystemExit: If all retries fail.
    """
    import time
    from draft_kings import Client

    for attempt in range(max_retries):
        try:
            client = Client()
            return client
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Connection attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"ERROR: Could not connect to DraftKings API after {max_retries} attempts: {e}")
                sys.exit(1)