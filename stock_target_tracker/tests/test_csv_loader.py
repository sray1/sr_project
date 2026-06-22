"""Tests for the CSV loader module."""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

from csv_loader import load_symbols, validate_symbols, load_allowed_symbols


class TestLoadSymbols:
    def test_basic_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol,company_name,sector\nAAPL,Apple Inc.,Technology\nMSFT,Microsoft,Technology\n")
        result = load_symbols(str(csv_file))
        assert len(result) == 2
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["company_name"] == "Apple Inc."
        assert result[1]["symbol"] == "MSFT"

    def test_symbol_only_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol\nAAPL\nMSFT\nGOOGL\n")
        result = load_symbols(str(csv_file))
        assert len(result) == 3
        assert result[0]["company_name"] is None
        assert result[0]["sector"] is None

    def test_uppercase_normalization(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol\naapl\nmsft\n")
        result = load_symbols(str(csv_file))
        assert result[0]["symbol"] == "AAPL"
        assert result[1]["symbol"] == "MSFT"

    def test_whitespace_trimming(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol,company_name\n  AAPL  ,  Apple Inc.  \n")
        result = load_symbols(str(csv_file))
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["company_name"] == "Apple Inc."

    def test_skips_empty_symbols(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol\nAAPL\n\nMSFT\n")
        result = load_symbols(str(csv_file))
        assert len(result) == 2

    def test_missing_column_raises(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("name,sector\nApple,Technology\n")
        with pytest.raises(ValueError, match="symbol"):
            load_symbols(str(csv_file))

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_symbols("/nonexistent/path.csv")


class TestLoadAllowedSymbols:
    def test_returns_set_of_symbols(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol,company_name\nAAPL,Apple\nMSFT,Microsoft\n")
        allowed = load_allowed_symbols(str(csv_file))
        assert isinstance(allowed, set)
        assert allowed == {"AAPL", "MSFT"}

    def test_normalizes_to_uppercase(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol\naapl\nmsft\n")
        allowed = load_allowed_symbols(str(csv_file))
        assert allowed == {"AAPL", "MSFT"}

    def test_missing_file_returns_empty_set(self):
        allowed = load_allowed_symbols("/nonexistent/path.csv")
        assert allowed == set()

    def test_empty_csv_returns_empty_set(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("symbol\n")
        allowed = load_allowed_symbols(str(csv_file))
        assert allowed == set()


class TestValidateSymbols:
    def test_valid_symbols(self):
        symbols = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        valid, warnings = validate_symbols(symbols)
        assert len(valid) == 2
        assert len(warnings) == 0

    def test_duplicate_detection(self):
        symbols = [{"symbol": "AAPL"}, {"symbol": "AAPL"}]
        valid, warnings = validate_symbols(symbols)
        assert len(valid) == 1
        assert any("Duplicate" in w for w in warnings)

    def test_unusual_format_warning(self):
        symbols = [{"symbol": "VERYLONGSYMBOL"}]
        valid, warnings = validate_symbols(symbols)
        assert len(valid) == 1  # Still included
        assert any("Unusual" in w for w in warnings)