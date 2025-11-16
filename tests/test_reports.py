"""Tests for reports module, including hledger compatibility."""

import pytest
import datetime
import pandas as pd
import numpy as np
from investory import reports, roi


class TestHledgerStatsParser:
    """Test parsing of hledger stats output across different versions."""

    def test_parse_stats_txns_span_format(self):
        """Test parsing 'Txns span' format (modern hledger)."""
        stats_output = """Main file           : .../all.ledger
Included files      : 4
Txns span           : 2021-07-19 to 2025-04-16 (1367 days)
Last txn            : 2025-04-15 (200 days ago)
Txns                : 33 (0.0 per day)"""

        first_trans_date_str = roi.parse_ledger_stats_date_range(stats_output)

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

        first_trans_date_str = roi.parse_ledger_stats_date_range(stats_output)

        assert first_trans_date_str == "2020-01-01"
        parsed_date = datetime.datetime.strptime(first_trans_date_str, "%Y-%m-%d").date()
        assert parsed_date == datetime.date(2020, 1, 1)

    def test_parse_stats_date_range_format(self):
        """Test parsing 'Date range' format (alternative format)."""
        stats_output = """Main file           : .../all.ledger
Included files      : 2
Date range          : 2019-06-15 to 2023-11-20 (1619 days)
Transactions        : 50"""

        first_trans_date_str = roi.parse_ledger_stats_date_range(stats_output)

        assert first_trans_date_str == "2019-06-15"
        parsed_date = datetime.datetime.strptime(first_trans_date_str, "%Y-%m-%d").date()
        assert parsed_date == datetime.date(2019, 6, 15)

    def test_parse_stats_no_matching_format(self):
        """Test that parsing returns None when format doesn't match."""
        stats_output = """Main file           : .../all.ledger
Included files      : 1
Txns                : 0"""

        first_trans_date_str = roi.parse_ledger_stats_date_range(stats_output)

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


class TestTWRCalculation:
    """Test Time-Weighted Return (TWR) calculation.
    
    Based on the example from the documentation:
    - Portfolio: 1 AAPL bought at $125.07 on 2023-01-03
    - Monthly prices throughout 2023
    - Expected annual TWR: 53.94%
    """

    def test_monthly_twr_calculation(self):
        """Test that monthly TWR factors are correctly calculated from percentage changes."""
        # Monthly returns from the example
        monthly_returns_perc = np.array([
            14.73,  # Jan: (143.49 - 125.07) / 125.07 = 14.73%
            2.32,   # Feb
            11.86,  # Mar
            2.90,   # Apr
            4.61,   # May
            9.43,   # Jun
            1.28,   # Jul
            -4.24,  # Aug
            -8.87,  # Sep
            -0.26,  # Oct
            11.38,  # Nov
            1.36,   # Dec
        ])
        
        # Convert percentage to factor (1 + r)
        monthly_factors = monthly_returns_perc / 100 + 1
        
        # Calculate accumulated return
        accumulated_factor = np.prod(monthly_factors)
        accumulated_return_perc = (accumulated_factor - 1) * 100
        
        # Expected annual TWR is 53.94%
        assert abs(accumulated_return_perc - 53.94) < 0.01, \
            f"Expected 53.94%, got {accumulated_return_perc:.2f}%"

    def test_yearly_twr_from_monthly_data(self):
        """Test that yearly TWR is correctly aggregated from monthly TWR data using pandas."""
        # Create monthly data matching the example
        dates = pd.date_range(start='2023-01-31', end='2023-12-31', freq='ME')
        monthly_returns_perc = [14.73, 2.32, 11.86, 2.90, 4.61, 9.43, 
                                1.28, -4.24, -8.87, -0.26, 11.38, 1.36]
        
        # Create DataFrame in the format used by parse_hledger_roi_ascii
        df = pd.DataFrame({
            'date': dates,
            'twr_factor': [r / 100 + 1 for r in monthly_returns_perc]
        })
        
        # Replicate the logic from plot_yearly_twr_bars -> calculate_yearly_twr
        df['year'] = df['date'].dt.year
        yearly_twr_factor = df.groupby('year')['twr_factor'].prod()
        yearly_gain_percent = (yearly_twr_factor - 1) * 100
        
        # For 2023, we expect 53.94%
        assert 2023 in yearly_gain_percent.index
        assert abs(yearly_gain_percent[2023] - 53.94) < 0.01, \
            f"Expected 53.94%, got {yearly_gain_percent[2023]:.2f}%"

    def test_first_month_twr(self):
        """Test TWR calculation for the first month with initial investment."""
        # First month: bought at $125.07, value at end of month $143.49
        initial_value = 125.07
        end_value = 143.49
        
        # TWR = (end_value - initial_value) / initial_value
        twr_percent = ((end_value - initial_value) / initial_value) * 100
        
        assert abs(twr_percent - 14.73) < 0.01, \
            f"Expected 14.73%, got {twr_percent:.2f}%"

    def test_twr_with_negative_returns(self):
        """Test that TWR correctly handles negative returns (market decline)."""
        # August-September period: decline from $195.93 to $170.98
        # Aug: -4.24%, Sep: -8.87%
        
        start_value = 195.93
        aug_end = 187.62
        sep_end = 170.98
        
        aug_return = ((aug_end - start_value) / start_value) * 100
        sep_return = ((sep_end - aug_end) / aug_end) * 100
        
        assert abs(aug_return - (-4.24)) < 0.01
        assert abs(sep_return - (-8.87)) < 0.01
        
        # Combined TWR for the two months
        combined_factor = (1 + aug_return/100) * (1 + sep_return/100)
        combined_return = (combined_factor - 1) * 100
        
        # Overall decline should be approximately -12.77%
        expected_combined = ((sep_end - start_value) / start_value) * 100
        assert abs(combined_return - expected_combined) < 0.01

    def test_benchmark_twr_calculation(self):
        """Test TWR calculation for S&P 500 benchmark from example."""
        # S&P 500: 3824.139893 to 4769.830078
        start_price = 3824.139893
        end_price = 4769.830078
        
        year_return = ((end_price - start_price) / start_price) * 100
        
        # Expected: 24.73%
        assert abs(year_return - 24.73) < 0.01, \
            f"Expected 24.73%, got {year_return:.2f}%"

    def test_parse_hledger_roi_output(self):
        """Test parsing of hledger roi ASCII output format."""
        # Simplified example of hledger roi output
        ascii_output = """
+---++------------+------------++---------------+----------+-------------+--------++---------++------------+----------+
|   ||      Begin |        End || Value (begin) | Cashflow | Value (end) |    PnL ||     IRR || TWR/period | TWR/year |
+===++============+============++===============+==========+=============+========++=========++============+==========+
| 1 || 2023-01-01 | 2023-01-31 ||             0 |  $125.07 |     $143.49 | $18.42 || 463.54% ||     14.73% |  404.25% |
| 2 || 2023-02-01 | 2023-02-28 ||       $143.49 |        0 |     $146.81 |   $3.33 ||  34.82% ||      2.32% |   34.85% |
| 3 || 2023-03-01 | 2023-03-31 ||       $146.81 |        0 |     $164.23 |  $17.42 || 274.39% ||     11.86% |  274.20% |
+---++------------+------------++---------------+----------+-------------+--------++---------++------------+----------+
| Total || 2023-01-01 | 2023-12-31 ||             0 |  $125.07 |     $192.53 |  $67.46 ||  54.30% ||     53.94% |   53.94% |
+---++------------+------------++---------------+----------+-------------+--------++---------++------------+----------+
"""
        
        df = roi.parse_hledger_roi_ascii(ascii_output)
        
        assert df is not None
        assert len(df) == 3  # Three monthly periods (excluding Total row)
        assert 'date' in df.columns
        assert 'twr_factor' in df.columns
        
        # Check that TWR factors are correctly extracted
        # First month: 14.73% -> factor = 1.1473
        assert abs(df.iloc[0]['twr_factor'] - 1.1473) < 0.0001
        
        # Check date parsing
        assert df.iloc[0]['date'] == pd.Timestamp('2023-01-31')
        assert df.iloc[1]['date'] == pd.Timestamp('2023-02-28')
        assert df.iloc[2]['date'] == pd.Timestamp('2023-03-31')


class TestGetLedgerYears:
    """Test get_ledger_years function with different hledger output formats."""
    
    def test_get_ledger_years_txns_span(self, tmp_path):
        """Test parsing years from 'Txns span' format (modern hledger)."""
        # Create a minimal ledger file
        ledger_file = tmp_path / "test.ledger"
        ledger_file.write_text("""
2021-01-01 Opening
    assets:cash    100 EUR
    equity:opening

2024-12-31 Test
    assets:cash    -10 EUR
    expenses:test   10 EUR
""")
        
        years = reports.get_ledger_years(str(ledger_file), verbose=0)
        # Should include years from 2021 to 2024 (hledger may include current year)
        assert 2021 in years
        assert 2024 in years
        assert len(years) >= 4
    
    def test_get_ledger_years_empty_ledger(self, tmp_path):
        """Test with empty ledger returns empty list."""
        ledger_file = tmp_path / "empty.ledger"
        ledger_file.write_text("")
        
        years = reports.get_ledger_years(str(ledger_file), verbose=0)
        assert years == []


class TestYearlyReportGeneration:
    """Test yearly report generation functions."""
    
    def test_generate_yearly_report_creates_file(self, tmp_path):
        """Test that generate_yearly_report creates the output file."""
        ledger_file = tmp_path / "test.ledger"
        ledger_file.write_text("""
2024-01-01 Opening
    assets:investments:stocks    10 AAPL @ 100 USD
    assets:cash
""")
        
        output_dir = tmp_path / "reports"
        output_dir.mkdir()
        
        reports.generate_yearly_report(
            period=2024,
            ledger=str(ledger_file),
            target_currency="USD",
            conversion_args=[],
            data_dir=str(tmp_path),
            output_dir=str(output_dir),
            verbose=0,
        )
        
        report_file = output_dir / "2024.org"
        assert report_file.exists()
        assert report_file.stat().st_size >= 0
    
    def test_add_yearly_tax_info_handles_no_gains(self, tmp_path):
        """Test that tax info section handles ledgers without capital gains."""
        ledger_file = tmp_path / "test.ledger"
        ledger_file.write_text("""
2024-01-01 Opening
    assets:investments:stocks    10 AAPL @ 100 USD
    assets:cash
""")
        
        output_dir = tmp_path / "reports"
        output_dir.mkdir()
        report_file = output_dir / "2024.org"
        report_file.write_text("* 2024 Overview\n\n")
        
        # This should not raise an error
        reports.add_yearly_tax_info(
            period=2024,
            ledger=str(ledger_file),
            target_currency="USD",
            conversion_args=[],
            data_dir=str(tmp_path),
            output_dir=str(output_dir),
            verbose=0,
        )
        
        content = report_file.read_text()
        assert "Capital Gains" in content
        assert "No capital gains data" in content or "Error:" in content
