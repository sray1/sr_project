# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Package Management

This project uses [uv](https://github.com/astral-sh/uv) for fast Python package management instead of pip.

**Setup:**
```powershell
uv venv
.venv\Scripts\activate
uv sync
```

**Adding dependencies:**
```powershell
uv add <package-name>        # runtime dependency
uv add --dev <package-name>   # dev dependency
```

**Running scripts:**
```powershell
python fibonacci.py
python test_numpy_pandas.py
```

## Project Structure

- `fibonacci.py` - Fibonacci number implementation with `fibonacci(n)` and `fibonacci_sequence(count)` functions
- `test_numpy_pandas.py` - Comprehensive test script for NumPy and Pandas functionality
- `dfs_lineup_optimizer/` - DraftKings daily fantasy sports lineup prediction scripts
- `pyproject.toml` - Project configuration (Python 3.11+, NumPy 2.0+, Pandas 3.0+)

## Project Purpose

This is a sandbox repository for testing scripts and exploring Python libraries. Each subfolder contains code for a specific experimental task.

## Notes

- The `.venv` directory and `uv.lock` are excluded from version control
- This is a learning/exploration project - code here is experimental and meant for testing ideas