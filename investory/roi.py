"""Calculate and visualize Return on Investment (ROI) for portfolios.

This module provides ROI analysis functionality:
- Calculate Time-Weighted Return (TWR) for portfolios using hledger
- Fetch benchmark data from Yahoo Finance
- Compare portfolio performance against benchmarks
- Generate ROI visualization plots
"""

import datetime
import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path

import matplotlib.axes
import matplotlib.pyplot as plt
import pandas as pd
import yahooquery as yq

from . import prices

logger = logging.getLogger(__name__)


def get_ledger_hash(ledger_file: str, investment_account: str, pnl_account: str) -> str:
    """Generate hash of relevant ledger transactions for cache invalidation.

    Uses hledger register to get all transactions affecting investment/pnl accounts,
    which is faster than hashing the entire file and more accurate for ROI purposes.
    """
    try:
        # Get all transactions from investment and pnl accounts
        command = f"hledger -f {ledger_file} register '{investment_account}|{pnl_account}' --output-format=csv"
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            capture_output=True,
            text=True,
        )
        # Hash the transaction data
        return hashlib.sha256(result.stdout.encode()).hexdigest()
    except subprocess.CalledProcessError as e:
        logger.warning(f"Could not generate ledger hash: {e}")
        # Fallback to file modification time
        return str(Path(ledger_file).stat().st_mtime)


def load_cache(cache_file: Path) -> dict | None:
    """Load cached data from JSON file."""
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r") as f:
            cache = json.load(f)
            # Convert date strings back to datetime for portfolio data
            if "portfolio_data" in cache and cache["portfolio_data"]:
                df = pd.DataFrame(cache["portfolio_data"])
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                cache["portfolio_data"] = df
            # Convert benchmark data to Series
            if "benchmark_data" in cache:
                for ticker in cache["benchmark_data"]:
                    data = cache["benchmark_data"][ticker]
                    if data:
                        series = pd.Series(
                            data["values"], index=data["index"], name=ticker
                        )
                        cache["benchmark_data"][ticker] = series
            return cache
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Cache file corrupted, ignoring: {e}")
        return None


def save_cache(cache_file: Path, cache_data: dict) -> None:
    """Save cache data to JSON file."""
    try:
        # Convert DataFrames and Series to serializable format
        serializable = cache_data.copy()

        if "portfolio_data" in serializable and isinstance(
            serializable["portfolio_data"], pd.DataFrame
        ):
            df = serializable["portfolio_data"].copy()
            if "date" in df.columns:
                df["date"] = df["date"].astype(str)
            serializable["portfolio_data"] = df.to_dict("list")

        if "benchmark_data" in serializable:
            bm_serializable = {}
            for ticker, series in serializable["benchmark_data"].items():
                if isinstance(series, pd.Series):
                    bm_serializable[ticker] = {
                        "index": series.index.tolist(),
                        "values": series.values.tolist(),
                    }
                else:
                    bm_serializable[ticker] = series
            serializable["benchmark_data"] = bm_serializable

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(serializable, f, indent=2)
        logger.info(f"ROI cache saved to {cache_file}")
    except Exception as e:
        logger.warning(f"Could not save cache: {e}")
        import traceback

        logger.debug(traceback.format_exc())


def run_hledger_command(command: str, stdin_data: str | None = None) -> str:
    """Run a hledger command and return output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            capture_output=True,
            text=True,
            input=stdin_data,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {command}")
        logger.error(f"Error: {e.stderr}")
        raise


def parse_ledger_stats_date_range(stats_output: str) -> str | None:
    """Extract the first transaction date from hledger stats output.

    Handles multiple hledger versions with different formats:
    - "Txns span" (modern hledger)
    - "Transactions span" (older hledger)
    - "Date range" (alternative format)

    Returns:
        The first transaction date as a string (YYYY-MM-DD), or None if not found
    """
    for line in stats_output.splitlines():
        line_stripped = line.strip()
        if (
            line_stripped.startswith("Txns span")
            or line_stripped.startswith("Transactions span")
            or line_stripped.startswith("Date range")
        ):
            try:
                date_part = line.split(":", 1)[1].strip()
                first_date = date_part.split(" to ")[0].strip()
                return first_date
            except (IndexError, AttributeError):
                continue
    return None


def parse_hledger_roi_ascii(ascii_data: str) -> pd.DataFrame | None:
    """Parse ASCII table output from 'hledger roi'."""
    lines = ascii_data.strip().split("\n")
    header: list[str] = []
    data: list[list[str]] = []
    header_found = False
    data_started = False

    try:
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Find header row (contains 'Begin', 'End', 'TWR/period')
            if (
                "Begin" in line
                and "End" in line
                and "TWR/period" in line
                and not header_found
            ):
                header = [h.strip() for h in line.strip("|").split("|")]
                header_found = True
                logger.debug(f"Found header: {header}")
                continue

            # Skip separator lines like +===... or +---...
            if line.startswith("+===") or line.startswith("+---"):
                if header_found and not data_started:
                    data_started = True
                    logger.debug("Found header separator, starting data collection")
                elif data_started:
                    logger.debug("Found footer separator, stopping data collection")
                    break
                continue

            # Process data rows
            if data_started and line.startswith("|"):
                row_data = [d.strip() for d in line.strip("|").split("|")]
                if len(row_data) == len(header):
                    data.append(row_data)
                else:
                    logger.warning(
                        f"Skipping row due to column mismatch. Expected {len(header)}, "
                        f"got {len(row_data)}. Row: '{line}'"
                    )

        if not header or not data:
            logger.error("Could not find header or data rows in hledger roi output")
            logger.debug(f"ASCII Data received: {ascii_data[:500]}...")
            return None

        df = pd.DataFrame(data, columns=header)

        # Select and rename relevant columns
        if "End" not in df.columns or "TWR/period" not in df.columns:
            raise KeyError(
                "Required columns 'End' or 'TWR/period' not found in parsed data"
            )

        df = df[["End", "TWR/period"]]
        df = df.rename(columns={"End": "date", "TWR/period": "twr_percent"})

        # Convert date column to datetime objects
        df["date"] = pd.to_datetime(df["date"])

        # Convert TWR percentage string to numeric factor
        df["twr_percent"] = df["twr_percent"].str.rstrip("%")
        df["twr_percent"] = pd.to_numeric(df["twr_percent"], errors="coerce")
        df = df.dropna(subset=["twr_percent"])
        df["twr_factor"] = df["twr_percent"] / 100 + 1

        df = df.drop(columns=["twr_percent"])
        df = df.sort_values(by="date").reset_index(drop=True)
        return df

    except (KeyError, ValueError, IndexError, Exception) as e:
        logger.error(f"Error parsing hledger roi ASCII data: {e}")
        logger.debug(f"ASCII Data received: {ascii_data[:500]}...")
        return None


def get_benchmark_price_on_date(
    ticker: str, target_date: datetime.date
) -> float | None:
    """Fetch closing price for a benchmark ticker on or before a specific date."""
    try:
        ticker_obj = yq.Ticker(ticker, asynchronous=False)

        # Fetch data for a window ending on target_date
        start_fetch_date = target_date - datetime.timedelta(days=20)
        end_fetch_date = target_date + datetime.timedelta(days=1)

        hist_data = ticker_obj.history(
            start=start_fetch_date, end=end_fetch_date, adj_ohlc=True
        )

        if isinstance(hist_data, dict):
            if ticker not in hist_data or hist_data[ticker].empty:
                logger.debug(
                    f"No historical data for {ticker} (target date {target_date})"
                )
                return None
            hist = hist_data[ticker]
        elif isinstance(hist_data, pd.DataFrame):
            hist = hist_data
        else:
            logger.debug(
                f"Unexpected data type {type(hist_data)} from yahooquery for {ticker}"
            )
            return None

        if hist.empty or "close" not in hist.columns:
            logger.debug(
                f"No historical data or 'close' column for {ticker} (target {target_date})"
            )
            return None

        if isinstance(hist.index, pd.MultiIndex):
            if ticker in hist.index.get_level_values(0):
                hist = hist.loc[ticker]
            else:
                logger.debug(
                    f"Ticker {ticker} not found in MultiIndex for target {target_date}"
                )
                return None

        # Ensure index is DatetimeIndex for consistent sorting
        if not isinstance(hist.index, pd.DatetimeIndex):
            # Convert index values, handling mix of date and datetime objects
            normalized_index = []
            for idx_val in hist.index:
                if isinstance(idx_val, pd.Timestamp):
                    # Remove timezone if present
                    normalized_index.append(
                        idx_val.tz_localize(None) if idx_val.tz else idx_val
                    )
                elif isinstance(idx_val, datetime.datetime):
                    # Convert to timezone-naive
                    normalized_index.append(idx_val.replace(tzinfo=None))
                elif isinstance(idx_val, datetime.date):
                    # Convert date to datetime
                    normalized_index.append(
                        datetime.datetime.combine(idx_val, datetime.time())
                    )
                else:
                    # Try to parse as datetime
                    normalized_index.append(
                        pd.to_datetime(idx_val).tz_localize(None)
                        if pd.to_datetime(idx_val).tz
                        else pd.to_datetime(idx_val)
                    )
            hist.index = pd.DatetimeIndex(normalized_index)
        elif hist.index.tz is not None:
            # Remove timezone from DatetimeIndex
            hist.index = hist.index.tz_localize(None)

        hist = hist.sort_index()

        if hist.empty:
            logger.debug(
                f"No historical data after index handling for {ticker} (target {target_date})"
            )
            return None

        # Get the last available closing price
        last_close = hist["close"].iloc[-1]

        if pd.isna(last_close):
            logger.debug(
                f"Last closing price is NaN for {ticker} (target {target_date})"
            )
            return None

        actual_price_date_str = "Unknown Date"
        if isinstance(hist.index, pd.DatetimeIndex) and not hist.index.empty:
            actual_price_date = hist.index[-1]
            # Convert Timestamp or datetime to date for comparison with target_date
            if hasattr(actual_price_date, "date") and callable(actual_price_date.date):
                actual_price_date = actual_price_date.date()

            actual_price_date_str = actual_price_date.strftime("%Y-%m-%d")
            if actual_price_date > target_date:
                logger.warning(
                    f"Fetched price for {ticker} is for {actual_price_date_str}, "
                    f"which is after target {target_date}"
                )

        logger.debug(
            f"Price for {ticker} (target {target_date}, actual {actual_price_date_str}): "
            f"{last_close:.2f}"
        )

        return float(last_close)

    except AttributeError as ae:
        logger.debug(f"AttributeError for {ticker} (target {target_date}): {ae}")
        return None
    except Exception as e:
        logger.error(f"Error fetching price for {ticker} (target {target_date}): {e}")
        return None


def get_benchmark_prices_batch(
    ticker: str, years: list[int]
) -> dict[int, tuple[float | None, float | None]]:
    """Fetch all required benchmark prices in a single API call.

    Returns dict mapping year to (current_price, previous_year_price).
    This is much faster than individual calls per date.
    """
    if not years:
        return {}

    # Determine date range to fetch
    min_year = min(years)
    max_year = max(years)
    current_year = datetime.datetime.now().year

    # Start from end of year before first year
    start_date = datetime.date(min_year - 1, 12, 1)
    # End at today if current year, else end of last year + buffer
    if max_year >= current_year:
        end_date = datetime.date.today() + datetime.timedelta(days=1)
    else:
        end_date = datetime.date(max_year, 12, 31) + datetime.timedelta(days=20)

    logger.debug(f"Fetching benchmark {ticker} data from {start_date} to {end_date}")

    try:
        ticker_obj = yq.Ticker(ticker, asynchronous=False)
        hist_data = ticker_obj.history(start=start_date, end=end_date, adj_ohlc=True)

        if isinstance(hist_data, dict):
            if ticker not in hist_data or hist_data[ticker].empty:
                logger.warning(f"No historical data for {ticker}")
                return {}
            hist = hist_data[ticker]
        elif isinstance(hist_data, pd.DataFrame):
            hist = hist_data
        else:
            logger.warning(f"Unexpected data type from yahooquery for {ticker}")
            return {}

        if hist.empty or "close" not in hist.columns:
            logger.warning(f"No close prices for {ticker}")
            return {}

        # Handle MultiIndex
        if isinstance(hist.index, pd.MultiIndex):
            if ticker in hist.index.get_level_values(0):
                hist = hist.loc[ticker]
            else:
                logger.warning(f"Ticker {ticker} not found in MultiIndex")
                return {}

        # Normalize to timezone-naive DatetimeIndex
        if not isinstance(hist.index, pd.DatetimeIndex):
            hist.index = pd.to_datetime(hist.index)
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)

        hist = hist.sort_index()

        # Extract prices for each year
        results = {}
        for year in years:
            # Target date for current period
            if year == current_year:
                target_current = datetime.date.today()
            else:
                target_current = datetime.date(year, 12, 31)

            # Target date for previous period
            target_prev = datetime.date(year - 1, 12, 31)

            # Find closest price on or before target dates
            price_current = None
            price_prev = None

            # Get price on or before target_current
            mask_current = hist.index.date <= target_current
            if mask_current.any():
                price_current = hist.loc[mask_current, "close"].iloc[-1]
                if pd.notna(price_current):
                    price_current = float(price_current)
                else:
                    price_current = None

            # Get price on or before target_prev
            mask_prev = hist.index.date <= target_prev
            if mask_prev.any():
                price_prev = hist.loc[mask_prev, "close"].iloc[-1]
                if pd.notna(price_prev):
                    price_prev = float(price_prev)
                else:
                    price_prev = None

            results[year] = (price_current, price_prev)

        return results

    except Exception as e:
        logger.error(f"Error fetching batch prices for {ticker}: {e}")
        return {}


def calculate_benchmark_twr(ticker: str, years: list[int]) -> pd.Series | None:
    """Calculate yearly TWR for a benchmark using EOY price comparisons."""
    yearly_returns_data: list[dict[str, float | int]] = []

    logger.info(
        f"Calculating yearly TWR for benchmark: {ticker} using Yahoo Finance EOY prices"
    )

    # Batch fetch all prices at once (much faster!)
    prices_by_year = get_benchmark_prices_batch(ticker, years)

    if not prices_by_year:
        logger.warning(f"No price data fetched for benchmark: {ticker}")
        return None

    current_calendar_year = datetime.datetime.now().year
    for year in years:
        price_current, price_prev = prices_by_year.get(year, (None, None))

        if price_current is not None and price_prev is not None:
            if price_prev != 0:
                yearly_return_pct = ((price_current / price_prev) - 1.0) * 100.0
                yearly_returns_data.append(
                    {"year": year, "twr_percent": yearly_return_pct}
                )

                price_label = "YTD" if year == current_calendar_year else f"EOY({year})"
                logger.debug(
                    f"  Benchmark {ticker} TWR for {year}: {yearly_return_pct:.2f}% "
                    f"(P_{price_label}={price_current:.2f}, P_EOY({year-1})={price_prev:.2f})"
                )
            else:
                logger.warning(
                    f"Previous year ({year - 1}) EOY price for {ticker} is zero. "
                    f"Skipping TWR for {year}"
                )
        else:
            logger.warning(f"Could not get EOY prices for {ticker} for year {year}")

    if yearly_returns_data:
        df_yearly = pd.DataFrame(yearly_returns_data).set_index("year")
        series = df_yearly["twr_percent"]
        series.name = ticker
        return series
    else:
        logger.warning(f"No yearly TWR data calculated for benchmark: {ticker}")
        return None


def get_portfolio_roi(
    ledger_file: str,
    price_files: list[str],
    conversion_args: list[str],
    investment_account: str = "assets:investments",
    pnl_account: str = "income:financial",
    begin_date: str | None = None,
    currency: str = "$",
) -> pd.DataFrame | None:
    """Calculate portfolio ROI using hledger."""
    logger.info("Calculating portfolio ROI...")

    # Build hledger roi command
    base_roi_args = [
        "roi",
        "--investment",
        investment_account,
        "--profit-loss",
        pnl_account,
        "--monthly",
        "--infer-market-prices",
        "--end",
        "today",
    ]

    # Add begin date
    if begin_date:
        base_roi_args.extend(["--begin", begin_date])
    else:
        # Get first transaction date from ledger
        try:
            stats_output = run_hledger_command(f"hledger -f {ledger_file} stats")
            first_date = parse_ledger_stats_date_range(stats_output)
            if first_date:
                base_roi_args.extend(["--begin", first_date])
            else:
                logger.warning("Could not determine ledger start date")
        except subprocess.CalledProcessError as e:
            logger.error(f"Could not get ledger stats: {e}")

    # Build complete command
    price_args = " ".join([f"-f {pf}" for pf in price_files])
    conv_args = " ".join(conversion_args)
    roi_args = " ".join(base_roi_args)

    command = f"hledger -f {ledger_file} {price_args} {conv_args} {roi_args} --value=then,{currency}"

    try:
        roi_ascii = run_hledger_command(command)
        df_portfolio = parse_hledger_roi_ascii(roi_ascii)
        return df_portfolio
    except (subprocess.CalledProcessError, IOError) as e:
        logger.error(f"Error getting or parsing portfolio ROI: {e}")
        return None


def get_roi_data(
    ledger_file: str,
    data_dir: str | Path,
    conversion_args: list[str],
    benchmark_tickers: list[str],
    investment_account: str = "assets:investments",
    pnl_account: str = "income:financial",
    begin_date: str | None = None,
    currency: str = "$",
    price_files: list[str] | None = None,
    use_cache: bool = True,
) -> tuple[pd.DataFrame | None, list[pd.Series | None]]:
    """Fetch and calculate ROI data for portfolio and benchmarks with caching.

    Args:
        ledger_file: Path to the main ledger file
        data_dir: Directory for cached price data
        conversion_args: hledger conversion arguments
        benchmark_tickers: List of Yahoo Finance tickers for benchmarks
        investment_account: Account pattern for investments
        pnl_account: Account pattern for profit/loss
        begin_date: Start date for ROI calculation
        currency: Target currency
        price_files: Optional list of price file paths (if already fetched, avoids re-fetching)
        use_cache: Whether to use cached ROI data (default: True)

    Returns:
        Tuple of (portfolio_df, list of benchmark_series)
    """
    logger.info(f"Fetching ROI data, comparing with benchmarks: {benchmark_tickers}")

    data_dir = Path(data_dir)
    cache_file = data_dir / "roi_cache.json"

    # Check cache validity
    cache_valid = False
    cached_data = None

    if use_cache:
        cached_data = load_cache(cache_file)
        if cached_data:
            # Verify ledger hasn't changed
            current_hash = get_ledger_hash(ledger_file, investment_account, pnl_account)
            cache_params_match = (
                cached_data.get("ledger_hash") == current_hash
                and cached_data.get("investment_account") == investment_account
                and cached_data.get("pnl_account") == pnl_account
                and cached_data.get("begin_date") == begin_date
                and cached_data.get("currency") == currency
                and cached_data.get("conversion_args") == conversion_args
            )

            if cache_params_match:
                logger.info("Using cached portfolio ROI data (ledger unchanged)")
                cache_valid = True
            else:
                # Debug: show what changed
                if cached_data.get("ledger_hash") != current_hash:
                    logger.info("Ledger transactions changed, recalculating ROI")
                elif cached_data.get("conversion_args") != conversion_args:
                    logger.debug(
                        f"Cache invalid: conversion_args changed from {cached_data.get('conversion_args')} to {conversion_args}"
                    )
                elif cached_data.get("currency") != currency:
                    logger.debug(
                        f"Cache invalid: currency changed from {cached_data.get('currency')} to {currency}"
                    )
                elif cached_data.get("begin_date") != begin_date:
                    logger.debug(
                        f"Cache invalid: begin_date changed from {cached_data.get('begin_date')} to {begin_date}"
                    )
                elif cached_data.get("investment_account") != investment_account:
                    logger.debug(f"Cache invalid: investment_account changed")
                elif cached_data.get("pnl_account") != pnl_account:
                    logger.debug(f"Cache invalid: pnl_account changed")
                else:
                    logger.debug("Cache invalid: parameters changed")
        else:
            logger.debug("No cache found, calculating fresh ROI data")

    # Ensure price data is available (skip if price_files already provided)
    if price_files is None:
        price_files = prices.ensure_price_data(
            ledger_file, data_dir, target_currency=currency
        )
    logger.info(f"Using {len(price_files)} price data files")

    # Get portfolio ROI (from cache or fresh)
    if cache_valid and cached_data and "portfolio_data" in cached_data:
        df_portfolio = cached_data["portfolio_data"]
        if df_portfolio is not None and not isinstance(df_portfolio, pd.DataFrame):
            df_portfolio = pd.DataFrame(df_portfolio)
    else:
        df_portfolio = get_portfolio_roi(
            ledger_file=ledger_file,
            price_files=price_files,
            conversion_args=conversion_args,
            investment_account=investment_account,
            pnl_account=pnl_account,
            begin_date=begin_date,
            currency=currency,
        )

    # Determine years for benchmark calculation
    years_for_calculation: list[int] = []
    if df_portfolio is not None and not df_portfolio.empty:
        min_year = df_portfolio["date"].dt.year.min()
        max_year = df_portfolio["date"].dt.year.max()
        years_for_calculation = list(range(min_year, max_year + 1))
    else:
        # Fallback to current year
        current_year = datetime.datetime.now().year
        start_year = current_year
        if begin_date:
            try:
                start_year = datetime.datetime.strptime(begin_date, "%Y-%m-%d").year
            except ValueError:
                pass
        years_for_calculation = list(range(start_year, current_year + 1))

    logger.debug(f"Years for benchmark calculations: {years_for_calculation}")

    # Calculate benchmark TWRs (with caching)
    benchmark_series: list[pd.Series | None] = []
    cached_benchmarks = {}
    if cache_valid and cached_data and "benchmark_data" in cached_data:
        cached_benchmarks = cached_data["benchmark_data"]

    new_benchmark_data = {}
    for ticker in benchmark_tickers:
        # Check if we have cached data for this ticker with same years
        if ticker in cached_benchmarks:
            cached_series = cached_benchmarks[ticker]
            if isinstance(cached_series, pd.Series):
                # Verify cached data covers our needed years
                cached_years = set(cached_series.index.tolist())
                needed_years = set(years_for_calculation)
                if needed_years.issubset(cached_years):
                    logger.info(f"Using cached benchmark data for {ticker}")
                    # Filter to only needed years
                    series = cached_series.loc[
                        cached_series.index.isin(years_for_calculation)
                    ]
                    benchmark_series.append(series)
                    new_benchmark_data[ticker] = series
                    continue

        # Fetch fresh data
        logger.info(f"Fetching fresh benchmark data for {ticker}")
        series = calculate_benchmark_twr(ticker, years_for_calculation)
        benchmark_series.append(series)
        new_benchmark_data[ticker] = series

    # Save cache if enabled
    if use_cache:
        cache_data = {
            "ledger_hash": get_ledger_hash(
                ledger_file, investment_account, pnl_account
            ),
            "investment_account": investment_account,
            "pnl_account": pnl_account,
            "begin_date": begin_date,
            "currency": currency,
            "conversion_args": conversion_args,
            "portfolio_data": df_portfolio,
            "benchmark_data": new_benchmark_data,
        }
        save_cache(cache_file, cache_data)

    return df_portfolio, benchmark_series


def calculate_yearly_twr(df: pd.DataFrame | None, name: str) -> pd.Series | None:
    """Calculate yearly TWR percentage gain from monthly TWR factors."""
    if df is None or df.empty:
        logger.info(f"No data provided for {name} yearly TWR calculation")
        return None
    try:
        df["date"] = pd.to_datetime(df["date"])
        df["year"] = df["date"].dt.year
        yearly_twr_factor = df.groupby("year")["twr_factor"].prod()
        yearly_gain_percent = (yearly_twr_factor - 1) * 100
        return yearly_gain_percent
    except Exception as e:
        logger.error(f"Error calculating yearly TWR for {name}: {e}")
        return None


def plot_yearly_twr_bars(
    ax: matplotlib.axes.Axes,
    df_portfolio: pd.DataFrame | None,
    benchmark_series: list[pd.Series | None],
    benchmark_tickers: list[str],
) -> matplotlib.axes.Axes:
    """Plot yearly TWR comparison as a bar chart."""
    logger.info("Plotting yearly TWR bars...")

    combined_df = pd.DataFrame()
    portfolio_yearly = calculate_yearly_twr(df_portfolio, "Portfolio")
    if portfolio_yearly is not None:
        combined_df["Portfolio"] = portfolio_yearly

    valid_benchmarks_data = []
    for i, bm_series in enumerate(benchmark_series):
        if bm_series is not None and not bm_series.empty:
            ticker = benchmark_tickers[i]
            combined_df[f"Benchmark ({ticker})"] = bm_series
            valid_benchmarks_data.append({"ticker": ticker, "data": bm_series})
        elif bm_series is None:
            logger.info(f"No yearly TWR data for benchmark: {benchmark_tickers[i]}")

    if combined_df.empty:
        ax.text(0.5, 0.5, "No yearly ROI data available", ha="center", va="center")
        ax.set_title("Yearly TWR (%)")
        logger.info("Skipping yearly TWR bar plot as no valid data was calculated")
        return ax

    # Bar plot setup
    years = combined_df.index.unique()
    n_years = len(years)
    num_series = len(combined_df.columns)

    # Colors
    PORTFOLIO_BAR_COLOR = "steelblue"
    BENCHMARK_COLORS = [
        "LightSkyBlue",
        "LightGreen",
        "LightPink",
        "Orange",
        "LightSalmon",
        "LightCoral",
    ]

    total_width_for_group = 0.8
    bar_width = total_width_for_group / num_series if num_series > 0 else 0
    index = range(n_years)

    for i, col_name in enumerate(combined_df.columns):
        series_data = combined_df[col_name].reindex(years).fillna(0)
        positions = [
            x - total_width_for_group / 2 + (i + 0.5) * bar_width for x in index
        ]

        gains = series_data.values

        if col_name == "Portfolio":
            bar_color = PORTFOLIO_BAR_COLOR
            label = "Portfolio"
        else:
            ticker_match = re.search(r"Benchmark \((.*?)\)", col_name)
            ticker_label = ticker_match.group(1) if ticker_match else col_name

            bm_index = -1
            for bm_idx, bm_data in enumerate(valid_benchmarks_data):
                if bm_data["ticker"] == ticker_label:
                    bm_index = bm_idx
                    break

            bar_color = BENCHMARK_COLORS[bm_index % len(BENCHMARK_COLORS)]
            label = f"Benchmark ({ticker_label})"

        bars = ax.bar(positions, gains, bar_width, label=label, color=bar_color)

        # Label colors
        label_colors = ["green" if g >= 0 else "red" for g in gains]
        labels_list = ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=8)

        if labels_list:
            for label_idx, label_item in enumerate(labels_list):
                if label_idx < len(label_colors):
                    label_item.set_color(label_colors[label_idx])

    # Final adjustments
    ax.set_ylabel("Yearly TWR (%)")
    ax.set_title("Yearly TWR Comparison")
    ax.set_xticks(index)

    # Create year labels
    year_labels = []
    current_year = datetime.datetime.now().year
    last_portfolio_date: datetime.date | None = None
    if df_portfolio is not None and not df_portfolio.empty:
        last_portfolio_date = df_portfolio["date"].max()

    sorted_years = sorted(list(years))

    for year_val_dt in sorted_years:
        year_val = int(str(year_val_dt).split("-")[0])

        is_ytd_for_portfolio = False
        if (
            last_portfolio_date
            and year_val == current_year
            and (
                last_portfolio_date.month < 12
                or (last_portfolio_date.month == 12 and last_portfolio_date.day < 31)
            )
        ):
            is_ytd_for_portfolio = True

        if is_ytd_for_portfolio:
            year_labels.append(f"{year_val}\n(YTD)")
        else:
            year_labels.append(str(year_val))

    ax.set_xticklabels(year_labels)
    ax.axhline(0, color="grey", linewidth=0.8, linestyle="--")
    ax.legend()

    return ax


def generate_roi_report(
    ledger_file: str,
    data_dir: str | Path,
    conversion_args: list[str],
    output_dir: str | Path,
    benchmark_tickers: list[str],
    investment_account: str = "assets:investments",
    pnl_account: str = "income:financial",
    begin_date: str | None = None,
    use_cache: bool = True,
) -> None:
    """Generate standalone ROI comparison report."""
    logger.info("Generating ROI report...")

    # Get ROI data
    df_portfolio, benchmark_series = get_roi_data(
        ledger_file=ledger_file,
        data_dir=data_dir,
        conversion_args=conversion_args,
        benchmark_tickers=benchmark_tickers,
        investment_account=investment_account,
        pnl_account=pnl_account,
        begin_date=begin_date,
        use_cache=use_cache,
    )

    # Create plot
    fig, ax = plt.subplots(figsize=(10, 6))
    plot_yearly_twr_bars(ax, df_portfolio, benchmark_series, benchmark_tickers)

    # Save
    output_path = Path(output_dir) / "roi_report.png"
    fig.tight_layout()
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    logger.info(f"ROI report saved to {output_path}")
