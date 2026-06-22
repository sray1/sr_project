"""
Load stock symbols from an input CSV file.

The input CSV (default: input/sample_symbols.csv) is the authoritative whitelist
of symbols that may be tracked. Only symbols present in this CSV can be fetched,
priced, or accuracy-checked. Symbols passed via --symbols that are not in the
CSV are rejected.

Expected CSV format:
    symbol,company_name,sector
    AAPL,Apple Inc.,Technology
    MSFT,Microsoft Corporation,Technology

Only the 'symbol' column is required. company_name and sector are optional.
"""

import csv
import os


def load_symbols(csv_path):
    """Load stock symbols from a CSV file.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        List of dicts: [{symbol, company_name, sector}, ...]
        Skips rows with empty symbol. Normalizes symbol to uppercase.

    Raises:
        FileNotFoundError: If csv_path does not exist.
        ValueError: If the CSV has no 'symbol' column.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    symbols = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        if 'symbol' not in (reader.fieldnames or []):
            raise ValueError(
                f"CSV file must have a 'symbol' column. "
                f"Found columns: {reader.fieldnames}"
            )

        for row_num, row in enumerate(reader, start=2):
            raw_symbol = row.get('symbol', '').strip()
            if not raw_symbol:
                continue  # skip empty rows

            symbol = raw_symbol.upper()
            company_name = row.get('company_name', '').strip() or None
            sector = row.get('sector', '').strip() or None

            symbols.append({
                'symbol': symbol,
                'company_name': company_name,
                'sector': sector,
            })

    return symbols


def load_allowed_symbols(csv_path):
    """Load the set of symbols permitted to be tracked (the whitelist).

    Args:
        csv_path: Path to the whitelist CSV file.

    Returns:
        Set of uppercase symbol strings. Empty set if file is missing or empty.

    Raises:
        ValueError: If the CSV exists but has no 'symbol' column.
    """
    try:
        entries = load_symbols(csv_path)
    except FileNotFoundError:
        return set()
    return {entry['symbol'] for entry in entries}


def validate_symbols(symbols_list):
    """Validate loaded symbols for basic correctness.

    Args:
        symbols_list: List of dicts from load_symbols().

    Returns:
        Tuple of (valid_symbols, warnings) where valid_symbols is the
        filtered list and warnings is a list of warning strings.
    """
    valid = []
    warnings = []
    seen = set()

    for entry in symbols_list:
        symbol = entry['symbol']

        # Check for duplicates
        if symbol in seen:
            warnings.append(f"Duplicate symbol skipped: {symbol}")
            continue
        seen.add(symbol)

        # Basic format check (1-5 uppercase letters, optionally with '.' or '-')
        import re
        if not re.match(r'^[A-Z]{1,5}([.-][A-Z]{1,2})?$', symbol):
            warnings.append(f"Unusual symbol format: {symbol} (included anyway)")

        valid.append(entry)

    return valid, warnings