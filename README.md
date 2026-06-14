# sr_project

Shonket's project

## Setup

This project uses [uv](https://github.com/astral-sh/uv) for fast Python package management.

### Prerequisites

- Python 3.11 or higher
- [uv](https://github.com/astral-sh/uv) installed

### Installation

1. Install uv (if not already installed):
   ```powershell
   pip install uv
   ```

2. Create and activate the virtual environment:
   ```powershell
   uv venv
   .venv\Scripts\activate
   ```

3. Install dependencies:
   ```powershell
   uv sync
   ```

### Adding Dependencies

To add a new package:
```powershell
uv add <package-name>
```

To add a development dependency:
```powershell
uv add --dev <package-name>
```

### Running Scripts

Run Python scripts normally:
```powershell
python fibonacci.py
python test_numpy_pandas.py
```

## Project Structure

- `fibonacci.py` - Fibonacci number implementation
- `test_numpy_pandas.py` - Test script for NumPy and Pandas
- `pyproject.toml` - Project configuration and dependencies
- [`dfs_lineup_optimizer/`](dfs_lineup_optimizer/README.md) - DraftKings DFS lineup prediction and tracking system (see [full README](dfs_lineup_optimizer/README.md))