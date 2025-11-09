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
        currency: str | None = None,
        output_dir: str | Path = ".",
    ):
        self.commodity = commodity
        self.yahoo_ticker = yahoo_ticker or commodity
        self.currency = currency  # None means auto-detect
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
            # Auto-detect currency if not specified
            if self.currency is None:
                self.currency = self._detect_currency()
                logger.info(f"Auto-detected currency for {self.commodity}: {self.currency}")
            
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

    def _detect_currency(self) -> str:
        """Auto-detect currency from Yahoo Finance ticker info.
        
        Tries multiple Yahoo Finance API endpoints to find the currency.
        If the ticker is not found, tries common exchange suffixes.
        Falls back to USD if detection fails.
        """
        currency = self._try_detect_currency(self.yahoo_ticker)
        if currency:
            return currency
        
        # If ticker not found and no exchange suffix, try common exchanges
        if not any(self.yahoo_ticker.endswith(suffix) for suffix in ['.SA', '.DE', '.L', '.SW', '.AS', '.PA']):
            logger.debug(f"Ticker {self.yahoo_ticker} not found, trying with exchange suffixes")
            # Try common exchanges based on ticker pattern
            exchanges_to_try = ['.SA', '.DE', '.L', '.SW']  # Brazilian, German, London, Swiss
            
            for suffix in exchanges_to_try:
                test_ticker = f"{self.yahoo_ticker}{suffix}"
                logger.debug(f"Trying {test_ticker}")
                currency = self._try_detect_currency(test_ticker)
                if currency:
                    logger.info(f"Found {self.yahoo_ticker} as {test_ticker} with currency {currency}")
                    # Update the yahoo_ticker to use the working one
                    self.yahoo_ticker = test_ticker
                    # Update cache file path to match the corrected ticker
                    self.cache_file = self.output_dir / f"{self.yahoo_ticker}.ledger"
                    return currency
        
        # Default to USD if detection fails
        logger.warning(f"Could not detect currency for {self.yahoo_ticker}, defaulting to $")
        return "$"
    
    def _try_detect_currency(self, ticker_symbol: str) -> str | None:
        """Try to detect currency for a specific ticker symbol.
        
        Returns None if ticker not found or has errors.
        """
        try:
            ticker = yq.Ticker(ticker_symbol)
            
            # Try 1: summary_detail
            info = ticker.summary_detail
            if isinstance(info, dict) and ticker_symbol in info:
                ticker_info = info[ticker_symbol]
                # Check for error messages
                if isinstance(ticker_info, str) and 'not found' in ticker_info.lower():
                    return None
                if isinstance(ticker_info, dict) and 'currency' in ticker_info:
                    currency_code = ticker_info['currency']
                    detected = self._currency_code_to_symbol(currency_code)
                    logger.debug(f"Detected currency from summary_detail for {ticker_symbol}: {currency_code} → {detected}")
                    return detected
            
            # Try 2: price info
            price_info = ticker.price
            if isinstance(price_info, dict) and ticker_symbol in price_info:
                ticker_info = price_info[ticker_symbol]
                # Check for error messages
                if isinstance(ticker_info, str) and 'not found' in ticker_info.lower():
                    return None
                if isinstance(ticker_info, dict) and 'currency' in ticker_info:
                    currency_code = ticker_info['currency']
                    detected = self._currency_code_to_symbol(currency_code)
                    logger.debug(f"Detected currency from price info for {ticker_symbol}: {currency_code} → {detected}")
                    return detected
            
            # Try 3: financial_data
            financial_data = ticker.financial_data
            if isinstance(financial_data, dict) and ticker_symbol in financial_data:
                ticker_info = financial_data[ticker_symbol]
                # Check for error messages
                if isinstance(ticker_info, str) and 'not found' in ticker_info.lower():
                    return None
                if isinstance(ticker_info, dict) and 'financialCurrency' in ticker_info:
                    currency_code = ticker_info['financialCurrency']
                    detected = self._currency_code_to_symbol(currency_code)
                    logger.debug(f"Detected currency from financial_data for {ticker_symbol}: {currency_code} → {detected}")
                    return detected
                    
        except Exception as e:
            logger.debug(f"Error detecting currency for {ticker_symbol}: {e}")
        
        return None
    
    def _currency_code_to_symbol(self, currency_code: str) -> str:
        """Convert currency code (e.g., 'BRL') to symbol (e.g., 'R$')."""
        currency_symbols = {
            'EUR': '€',
            'USD': '$',
            'GBP': '£',
            'JPY': '¥',
            'BRL': 'R$',
            'CHF': 'CHF',
            'CAD': 'C$',
            'AUD': 'A$',
        }
        return currency_symbols.get(currency_code, currency_code)

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


def get_commodity_currencies(ledger_file: str) -> dict[str, str]:
    """Detect which currency each commodity is priced in from the ledger.
    
    Returns dict mapping commodity -> currency symbol (e.g., {'VWCE': '€'})
    """
    commodity_currencies = {}
    
    try:
        # Get all transactions involving commodities
        output = subprocess.check_output(
            ["hledger", "-f", ledger_file, "print"],
            text=True
        )
        
        # Parse transaction lines looking for patterns like:
        # assets:investments  10 VWCE @ €90
        # assets:investments  10 VWCE @@ €900
        # assets:investments  10 ABEV3 @ R$10
        import re
        
        # Pattern: amount COMMODITY @ or @@ CURRENCY
        # Match R$ as two-char symbol or single currency symbols
        pattern = r'[\d\.\-]+\s+([A-Z][A-Z0-9]*)\s+@@?\s*(R\$|[€$£¥]|\w+)'
        
        for line in output.splitlines():
            line = line.strip()
            matches = re.finditer(pattern, line)
            for match in matches:
                commodity = match.group(1)
                currency = match.group(2)
                
                # Only track if commodity is not itself a currency
                currency_symbols = {"$", "€", "£", "¥", "R$", "USD", "EUR", "GBP", "JPY", "BRL"}
                if commodity not in currency_symbols:
                    commodity_currencies[commodity] = currency
                    
        logger.debug(f"Detected commodity currencies: {commodity_currencies}")
        
    except subprocess.CalledProcessError as e:
        logger.debug(f"Could not detect commodity currencies: {e}")
    
    return commodity_currencies


def infer_yahoo_ticker(commodity: str, ledger_currency: str | None = None) -> str:
    """Infer Yahoo Finance ticker from commodity name and ledger currency.
    
    Applies heuristics:
    - If commodity used with €, append .DE (German Xetra exchange)
    - If commodity used with £, append .L (London Stock Exchange)
    - If commodity used with CHF, append .SW (Swiss Exchange)
    - If commodity used with R$, append .SA (Brazilian B3 exchange)
    - Otherwise, use commodity as-is
    """
    if not ledger_currency:
        return commodity
    
    # Map currencies to exchange suffixes
    exchange_suffixes = {
        '€': '.DE',      # Euro -> German Xetra
        'EUR': '.DE',
        '£': '.L',       # British Pound -> London
        'GBP': '.L',
        'CHF': '.SW',    # Swiss Franc -> Switzerland
        'R$': '.SA',     # Brazilian Real -> B3 São Paulo
        'BRL': '.SA',
    }
    
    suffix = exchange_suffixes.get(ledger_currency)
    if suffix and not any(commodity.endswith(s) for s in ['.DE', '.L', '.SW', '.AS', '.PA', '.SA']):
        inferred = f"{commodity}{suffix}"
        logger.info(f"Inferred ticker for {commodity} with {ledger_currency}: {inferred}")
        return inferred
    
    return commodity


def get_exchange_rate_ticker(from_currency: str, to_currency: str) -> str | None:
    """Get Yahoo Finance ticker for currency exchange rate.
    
    Args:
        from_currency: Source currency symbol (e.g., '$', '€', 'USD')
        to_currency: Target currency symbol (e.g., '€', '$', 'EUR')
        
    Returns:
        Yahoo Finance ticker like 'USDEUR=X' or None if not found
    """
    # Normalize currency symbols to codes
    currency_to_code = {
        '$': 'USD', 'USD': 'USD',
        '€': 'EUR', 'EUR': 'EUR',
        '£': 'GBP', 'GBP': 'GBP',
        '¥': 'JPY', 'JPY': 'JPY',
        'R$': 'BRL', 'BRL': 'BRL',
        'CHF': 'CHF',
        'C$': 'CAD', 'CAD': 'CAD',
        'A$': 'AUD', 'AUD': 'AUD',
    }
    
    from_code = currency_to_code.get(from_currency)
    to_code = currency_to_code.get(to_currency)
    
    if not from_code or not to_code or from_code == to_code:
        return None
    
    # Yahoo Finance exchange rate format: FROMTO=X
    ticker = f"{from_code}{to_code}=X"
    return ticker


def ensure_exchange_rates(
    ledger_file: str,
    data_dir: str | Path,
    target_currency: str,
    commodity_currencies: dict[str, str] | None = None,
) -> list[str]:
    """Automatically fetch exchange rates for currencies used in the ledger.
    
    Args:
        ledger_file: Path to the ledger file
        data_dir: Directory for cached price data
        target_currency: The target currency to convert to (e.g., '€', 'USD')
        commodity_currencies: Pre-computed mapping of commodity -> currency (optional)
        
    Returns:
        List of exchange rate file paths
    """
    data_dir = Path(data_dir)
    
    # Detect currencies if not provided
    if commodity_currencies is None:
        commodity_currencies = get_commodity_currencies(ledger_file)
    
    # Get all unique currencies used in ledger
    all_currencies = get_ledger_commodities(ledger_file)
    used_currencies = set(commodity_currencies.values()) | set(all_currencies)
    
    # Filter to actual currency symbols
    currency_symbols = {'$', '€', '£', '¥', 'R$', 'USD', 'EUR', 'GBP', 'JPY', 'BRL', 'CAD', 'AUD', 'CHF'}
    used_currencies = used_currencies & currency_symbols
    
    # Remove target currency
    used_currencies.discard(target_currency)
    
    # Normalize target currency to handle both symbols and codes
    currency_to_code = {
        '$': 'USD', '€': 'EUR', '£': 'GBP', '¥': 'JPY', 'R$': 'BRL',
        'USD': 'USD', 'EUR': 'EUR', 'GBP': 'GBP', 'JPY': 'JPY', 'BRL': 'BRL',
        'CHF': 'CHF', 'CAD': 'CAD', 'AUD': 'AUD',
    }
    target_symbol = target_currency
    for sym, code in currency_to_code.items():
        if target_currency == code and sym in ['$', '€', '£', '¥', 'R$']:
            target_symbol = sym
            break
    
    if not used_currencies:
        logger.debug("No exchange rates needed - single currency ledger")
        return []
    
    logger.info(f"Fetching exchange rates to {target_currency} for: {used_currencies}")
    
    exchange_rate_files = []
    for currency in used_currencies:
        ticker = get_exchange_rate_ticker(currency, target_currency)
        if not ticker:
            logger.debug(f"No exchange rate ticker for {currency} -> {target_currency}")
            continue
        
        logger.info(f"Fetching exchange rate: {currency} -> {target_currency} ({ticker})")
        
        cache = PriceCache(
            commodity=currency,
            yahoo_ticker=ticker,
            currency=target_symbol,
            output_dir=data_dir,
        )
        
        try:
            cache.fetch_and_cache()
            if cache.cache_file.exists():
                exchange_rate_files.append(str(cache.cache_file))
                logger.info(f"Cached exchange rate to {cache.cache_file.name}")
        except Exception as e:
            logger.warning(f"Could not fetch exchange rate {ticker}: {e}")
    
    return exchange_rate_files


def ensure_price_data(
    ledger_file: str,
    data_dir: str | Path,
    ticker_map: dict[str, str] | None = None,
    currency_map: dict[str, str] | None = None,
    target_currency: str | None = None,
) -> list[str]:
    """Ensure all investment assets have price data cached.

    Args:
        ledger_file: Path to the main ledger file
        data_dir: Directory for cached price data
        ticker_map: Optional mapping of commodity -> Yahoo ticker
        currency_map: Optional mapping of commodity -> currency symbol (auto-detected if not provided)
        target_currency: If provided, automatically fetch exchange rates to this currency

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

    # Detect which currency each commodity is used with in the ledger
    commodity_currencies = get_commodity_currencies(ledger_file)

    logger.info(f"Ensuring price data for {len(investment_assets)} assets")

    price_files = []
    for commodity in investment_assets:
        # Detect currency from ledger first
        ledger_currency = commodity_currencies.get(commodity)
        
        # Use explicit ticker map if provided, otherwise infer from ledger currency
        if commodity in ticker_map:
            yahoo_ticker = ticker_map[commodity]
        else:
            yahoo_ticker = infer_yahoo_ticker(commodity, ledger_currency)
        
        # Prefer explicit currency_map, then ledger detection, then auto-detect from Yahoo
        currency = currency_map.get(commodity) or ledger_currency

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

    # Automatically fetch exchange rates if target_currency is specified
    if target_currency:
        exchange_rate_files = ensure_exchange_rates(
            ledger_file, data_dir, target_currency, commodity_currencies
        )
        price_files.extend(exchange_rate_files)

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
