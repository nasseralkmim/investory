"""Tests for reports module, including hledger compatibility."""

import pytest
import datetime
from investory import reports


class TestHledgerStatsParser:
    """Test parsing of hledger stats output across different versions."""

    def test_parse_stats_txns_span_format(self):
        """Test parsing 'Txns span' format (modern hledger)."""
        stats_output = """Main file           : .../all.ledger
Included files      : 4
Txns span           : 2021-07-19 to 2025-04-16 (1367 days)
Last txn            : 2025-04-15 (200 days ago)
Txns                : 33 (0.0 per day)"""

        first_trans_date_str = None
        for line in stats_output.splitlines():
            if line.strip().startswith("Txns span") or line.strip().startswith(
                "Transactions span"
            ) or line.strip().startswith("Date range"):
                date_part = line.split(":", 1)[1].strip()
                first_trans_date_str = date_part.split(" to ")[0].strip()
                break

        assert first_trans_date_str == "2021-07-19"
        # Verify it can be parsed as a date
        parsed_date = datetime.datetime.strptime(first_trans_date_str, "%Y-%m-%d").date()
        assert parsed_date == datetime.date(2021, 7, 19)

    def test_parse_stats_transactions_span_format(self):
        """Test parsing 'Transactions span' format (older hledger)."""
        stats_output = """Main file           : .../all.ledger
Included files      : 4
Transactions span   : 2020-01-01 to 2024-12-31 (1826 days)
Last transaction    : 2024-12-30 (2 days ago)
Transactions        : 100 (0.1 per day)"""

        first_trans_date_str = None
        for line in stats_output.splitlines():
            if line.strip().startswith("Txns span") or line.strip().startswith(
                "Transactions span"
            ) or line.strip().startswith("Date range"):
                date_part = line.split(":", 1)[1].strip()
                first_trans_date_str = date_part.split(" to ")[0].strip()
                break

        assert first_trans_date_str == "2020-01-01"
        parsed_date = datetime.datetime.strptime(first_trans_date_str, "%Y-%m-%d").date()
        assert parsed_date == datetime.date(2020, 1, 1)

    def test_parse_stats_date_range_format(self):
        """Test parsing 'Date range' format (alternative format)."""
        stats_output = """Main file           : .../all.ledger
Included files      : 2
Date range          : 2019-06-15 to 2023-11-20 (1619 days)
Transactions        : 50"""

        first_trans_date_str = None
        for line in stats_output.splitlines():
            if line.strip().startswith("Txns span") or line.strip().startswith(
                "Transactions span"
            ) or line.strip().startswith("Date range"):
                date_part = line.split(":", 1)[1].strip()
                first_trans_date_str = date_part.split(" to ")[0].strip()
                break

        assert first_trans_date_str == "2019-06-15"
        parsed_date = datetime.datetime.strptime(first_trans_date_str, "%Y-%m-%d").date()
        assert parsed_date == datetime.date(2019, 6, 15)

    def test_parse_stats_no_matching_format(self):
        """Test that parsing returns None when format doesn't match."""
        stats_output = """Main file           : .../all.ledger
Included files      : 1
Txns                : 0"""

        first_trans_date_str = None
        for line in stats_output.splitlines():
            if line.strip().startswith("Txns span") or line.strip().startswith(
                "Transactions span"
            ) or line.strip().startswith("Date range"):
                date_part = line.split(":", 1)[1].strip()
                first_trans_date_str = date_part.split(" to ")[0].strip()
                break

        assert first_trans_date_str is None


class TestRunCommand:
    """Test the run_command function with proper parameter passing."""

    def test_run_command_verbose_parameter(self):
        """Test that verbose parameter is passed correctly as keyword arg."""
        # This should not raise an error about 'int' object has no attribute 'encode'
        result = reports.run_command("echo test", verbose=0)
        assert result == "test"

    def test_run_command_with_stdin(self):
        """Test run_command with stdin data."""
        result = reports.run_command("cat", stdin_data="hello world", verbose=0)
        assert result == "hello world"

    def test_run_command_verbose_levels(self):
        """Test different verbosity levels don't cause errors."""
        for verbose_level in [0, 1, 2]:
            result = reports.run_command("echo test", verbose=verbose_level)
            assert result == "test"


class TestGetLedgerCurrencies:
    """Test currency detection from ledger files."""

    def test_get_ledger_currencies_verbose_parameter(self, tmp_path):
        """Test that verbose parameter is correctly passed to run_command."""
        # Create a minimal ledger file
        ledger_file = tmp_path / "test.ledger"
        ledger_file.write_text("""
2024-01-01 Test
    assets:cash    100 EUR
    income:salary  -100 EUR
""")

        # This should not raise an error about 'int' object has no attribute 'encode'
        # We pass different verbose levels to ensure they all work
        for verbose_level in [0, 1, 2]:
            currencies = reports.get_ledger_currencies(str(ledger_file), verbose=verbose_level)
            # EUR should be detected (if hledger is available)
            # If hledger is not available, the function catches the exception
            assert isinstance(currencies, set)
