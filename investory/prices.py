"""Fetch and cache commodity prices from Yahoo Finance.

This module provides price data management for investment portfolios:
- Auto-detect commodities from hledger files
- Fetch historical/current prices from Yahoo Finance
- Cache prices in .ledger format
- Ensure all assets have price data before analysis
"""

import datetime
import logging
import os
import subprocess
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yahooquery as yq

logger = logging.getLogger(__name__)


class PriceCache:
    """Manage cached price data for a commodity."""

    def __init__(
        self,
        commodity: str,
        yahoo_ticker: str | None = None,
        currency: str = "$",
        output_dir: str | Path = ".",
    ):
        self.commodity = commodity
        self.yahoo_ticker = yahoo_ticker or commodity
        self.currency = currency
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.output_dir / f"{self.yahoo_ticker}.ledger"

    def get_last_cached_date(self) -> datetime.date | None:
        """Return the last date recorded in cache, or None if not found."""
        if not self.cache_file.exists():
            return None

        try:
            with open(self.cache_file, "r") as f:
                lines = [line.strip() for line in f if line.strip()]
                if not lines:
                    return None

                # Parse last line: P YYYY-MM-DD "COMMODITY" $VALUE
                last_line = lines[-1]
                parts = last_line.split()
                if len(parts) > 1 and parts[0] == "P":
                    return datetime.datetime.strptime(parts[1], "%Y-%m-%d").date()
        except (ValueError, IndexError) as e:
            logger.warning(f"Could not parse last date from {self.cache_file}: {e}")

        return None

    def get_cached_dates(self) -> set[datetime.date]:
        """Return set of all dates present in the cache."""
        dates = set()
        if not self.cache_file.exists():
            return dates

        try:
            with open(self.cache_file, "r") as f:
                for line in f:
                    if line.startswith("P "):
                        try:
                            date_str = line.split()[1]
                            dates.add(
                                datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                            )
                        except (IndexError, ValueError):
                            continue
        except Exception as e:
            logger.warning(f"Error reading cached dates from {self.cache_file}: {e}")

        return dates

    def fetch_and_cache(
        self,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        split_adjustments: list[tuple[float, datetime.date]] | None = None,
    ) -> int:
        """Fetch prices and append to cache. Returns count of new entries."""
        if start_date is None:
            last_cached = self.get_last_cached_date()
            start_date = (
                last_cached + datetime.timedelta(days=1)
                if last_cached
                else datetime.date(2018, 1, 1)
            )

        if end_date is None:
            end_date = datetime.date.today()

        if start_date >= end_date:
            logger.debug(f"No new data to fetch for {self.commodity}")
            return 0

        logger.info(
            f"Fetching {self.commodity} prices from {start_date} to {end_date}"
        )

        try:
            history_df = self._fetch_yahoo_history(start_date, end_date)
            if history_df.empty:
                logger.warning(f"No data fetched for {self.commodity}")
                return 0

            new_entries = self._write_new_prices(history_df, split_adjustments)
            logger.info(f"Cached {new_entries} new prices for {self.commodity}")
            return new_entries

        except Exception as e:
            logger.error(f"Error fetching prices for {self.commodity}: {e}")
            return 0

    def _fetch_yahoo_history(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> pd.DataFrame:
        """Fetch historical data from Yahoo Finance."""
        with warnings.catch_warnings():
            warnings.simplefilter(action="ignore", category=FutureWarning)
            ticker = yq.Ticker(self.yahoo_ticker)
            # Fetch one day before end to avoid timezone issues
            fetch_end = end_date + datetime.timedelta(days=1)
            hist_data = ticker.history(start=start_date, end=fetch_end, adj_ohlc=False)

        if isinstance(hist_data, dict):
            if self.yahoo_ticker in hist_data:
                hist_data = hist_data[self.yahoo_ticker]
            else:
                return pd.DataFrame()

        if hist_data.empty or "close" not in hist_data.columns:
            return pd.DataFrame()

        # Normalize index
        if isinstance(hist_data.index, pd.MultiIndex):
            hist_data.index = hist_data.index.get_level_values("date")

        # Ensure timezone-naive datetime index
        if isinstance(hist_data.index, pd.DatetimeIndex):
            if hist_data.index.tz is not None:
                hist_data.index = hist_data.index.tz_localize(None)
        else:
            hist_data.index = pd.to_datetime(hist_data.index, utc=True).tz_localize(
                None
            )

        return hist_data.sort_index()

    def _write_new_prices(
        self,
        history_df: pd.DataFrame,
        split_adjustments: list[tuple[float, datetime.date]] | None = None,
    ) -> int:
        """Write new price entries to cache file."""
        cached_dates = self.get_cached_dates()

        # Generate business day dates
        start = history_df.index.min()
        end = history_df.index.max()
        target_dates = pd.date_range(start, end, freq="B")

        # Forward-fill to get prices for business days
        relevant_data = history_df.reindex(target_dates, method="ffill").dropna()

        # Filter out already cached dates
        new_data = relevant_data[~np.isin(relevant_data.index.date, list(cached_dates))]

        if new_data.empty:
            return 0

        # Write to cache
        count = 0
        with open(self.cache_file, "a") as f:
            for date_ts, row in new_data.iterrows():
                date = date_ts.date()
                value = float(row["close"])

                # Apply split adjustments if provided
                if split_adjustments:
                    value = self._adjust_for_splits(date, value, split_adjustments)

                f.write(
                    f'P {date.strftime("%Y-%m-%d")} "{self.commodity}" {self.currency}{value:f}\n'
                )
                count += 1

        return count

    @staticmethod
    def _adjust_for_splits(
        date: datetime.date,
        value: float,
        split_adjustments: list[tuple[float, datetime.date]],
    ) -> float:
        """Adjust price for stock splits."""
        for ratio, split_date in split_adjustments:
            if date <= split_date:
                value *= ratio
        return value


def get_ledger_commodities(ledger_file: str) -> list[str]:
    """Extract all commodities from a ledger file."""
    try:
        output = subprocess.check_output(
            ["hledger", "-f", ledger_file, "commodities"], text=True
        )
        commodities = [line.strip() for line in output.splitlines() if line.strip()]
        logger.info(f"Found {len(commodities)} commodities in {ledger_file}")
        return commodities
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get commodities from {ledger_file}: {e}")
        return []


def filter_currency_commodities(commodities: list[str]) -> list[str]:
    """Filter out currency symbols/codes, return investment assets only."""
    currency_symbols = {"$", "€", "£", "¥", "R$"}
    currency_codes = {"USD", "EUR", "GBP", "JPY", "BRL", "CAD", "AUD", "CHF"}

    investment_assets = [
        c for c in commodities if c not in currency_symbols and c not in currency_codes
    ]

    logger.debug(
        f"Filtered {len(commodities) - len(investment_assets)} currencies, "
        f"{len(investment_assets)} investment assets remain"
    )
    return investment_assets


def ensure_price_data(
    ledger_file: str,
    data_dir: str | Path,
    ticker_map: dict[str, str] | None = None,
    currency_map: dict[str, str] | None = None,
) -> list[str]:
    """Ensure all investment assets have price data cached.

    Args:
        ledger_file: Path to the main ledger file
        data_dir: Directory for cached price data
        ticker_map: Optional mapping of commodity -> Yahoo ticker
        currency_map: Optional mapping of commodity -> currency symbol

    Returns:
        List of price cache file paths to include in hledger commands
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    ticker_map = ticker_map or {}
    currency_map = currency_map or {}

    # Get investment commodities
    all_commodities = get_ledger_commodities(ledger_file)
    investment_assets = filter_currency_commodities(all_commodities)

    if not investment_assets:
        logger.warning(f"No investment assets found in {ledger_file}")
        return []

    logger.info(f"Ensuring price data for {len(investment_assets)} assets")

    price_files = []
    for commodity in investment_assets:
        yahoo_ticker = ticker_map.get(commodity, commodity)
        currency = currency_map.get(commodity, "$")

        cache = PriceCache(
            commodity=commodity,
            yahoo_ticker=yahoo_ticker,
            currency=currency,
            output_dir=data_dir,
        )

        # Fetch latest prices
        cache.fetch_and_cache()

        if cache.cache_file.exists():
            price_files.append(str(cache.cache_file))
        else:
            logger.warning(f"No price data cached for {commodity}")

    return price_files


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s: %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="Fetch and cache commodity prices from Yahoo Finance"
    )
    parser.add_argument(
        "--commodity", required=True, help="Commodity name used in the ledger"
    )
    parser.add_argument(
        "--yahoo-ticker", help="Yahoo Finance ticker (if different from commodity)"
    )
    parser.add_argument(
        "--currency", default="$", help="Currency symbol (default: $)"
    )
    parser.add_argument(
        "--output-dir", default=".", help="Directory for cached price files"
    )
    parser.add_argument(
        "--begin",
        type=lambda s: datetime.datetime.strptime(s, "%Y-%m-%d").date(),
        help="Start date for fetching (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--split",
        nargs="+",
        help="Stock split adjustments (format: ratio_from:ratio_to,YYYY-MM-DD)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Parse split adjustments
    split_adjustments = []
    if args.split:
        for split_str in args.split:
            ratio_str, date_str = split_str.split(",")
            ratio_from, ratio_to = map(float, ratio_str.split(":"))
            split_ratio = ratio_to / ratio_from
            split_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            split_adjustments.append((split_ratio, split_date))

    cache = PriceCache(
        commodity=args.commodity,
        yahoo_ticker=args.yahoo_ticker,
        currency=args.currency,
        output_dir=args.output_dir,
    )

    count = cache.fetch_and_cache(start_date=args.begin, split_adjustments=split_adjustments)
    logger.info(f"Completed: {count} new prices cached to {cache.cache_file}")
