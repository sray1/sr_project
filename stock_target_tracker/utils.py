"""
Shared utilities for Stock Target Price Tracker.

Consolidates common patterns:
- MultiOutput: dual-stream output (stdout + file)
- retry_with_backoff(): HTTP request retry with exponential backoff
- run_and_save(): boilerplate for running main() and saving output
- load_config(): load API keys from .env file
"""

import os
import sys
import tempfile
import time
from datetime import datetime as dt

# Database file path (same directory as this module)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_tracker.db")


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


def retry_with_backoff(func, max_retries=3, base_delay=1.0, backoff_factor=2.0,
                       retry_on=(Exception,), on_retry=None):
    """Call a function with exponential backoff retry logic.

    Args:
        func: Callable to execute.
        max_retries: Number of retry attempts after initial failure.
        base_delay: Initial delay in seconds.
        backoff_factor: Multiplier for delay on each retry.
        retry_on: Tuple of exception types to catch and retry on.
        on_retry: Optional callback(attempt, delay, exception) called before each retry.

    Returns:
        Whatever func() returns.

    Raises:
        The last exception if all retries fail.
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except retry_on as e:
            last_exception = e
            if attempt < max_retries:
                delay = base_delay * (backoff_factor ** attempt)
                if on_retry:
                    on_retry(attempt + 1, delay, e)
                time.sleep(delay)
            else:
                raise
    raise last_exception


def run_and_save(main_func, prefix='stock_tracker_', output_dir=None):
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
        base_name = prefix.rstrip('_').replace('_', '_')
        persistent_path = os.path.join(out_dir, f"{base_name}{timestamp}.txt")
        with open(persistent_path, 'w', encoding='utf-8') as f:
            with open(temp_path, 'r', encoding='utf-8', errors='replace') as tmp:
                f.write(tmp.read())
        print(f"Results also saved to: {persistent_path}")

    return persistent_path or temp_path


def load_env():
    """Load environment variables from .env file in the project directory.

    Looks for .env in the stock_target_tracker/ directory first, then the
    project root. Uses python-dotenv if available, otherwise falls back to
    simple key=value parsing.
    """
    try:
        from dotenv import load_dotenv
        # Try project-specific .env first, then project root
        env_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        env_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
        if os.path.exists(env_local):
            load_dotenv(env_local)
        elif os.path.exists(env_root):
            load_dotenv(env_root)
    except ImportError:
        pass  # python-dotenv not installed, rely on system env vars


def get_env(key, default=None):
    """Get an environment variable, loading .env first if needed.

    Args:
        key: Environment variable name.
        default: Default value if not found.

    Returns:
        The environment variable value, or default.
    """
    load_env()
    return os.environ.get(key, default)


# Rate limiting helpers
_rate_limit_last_request = {}


def rate_limit(source_name, min_interval=1.0):
    """Enforce minimum interval between requests to the same source.

    Args:
        source_name: Name of the source (e.g., 'yahoo_finance', 'fmp').
        min_interval: Minimum seconds between requests.
    """
    now = time.time()
    last = _rate_limit_last_request.get(source_name, 0)
    elapsed = now - last
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _rate_limit_last_request[source_name] = time.time()