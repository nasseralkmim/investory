"""Get the commodities value from the internet.

Usage:

    $ python -m investory.values <commodity ticker>

The commodities list can be obtained with:

    $ hledger -f all.ledger commodities

Some commodities tickers need a specific suffix according to Yahoo database.
For example, Brazilian stocks need a '.SA'.
"""

import numpy as np
import pandas as pd
import os
import ybankinplay as yq
import datetime
import warnings


class Commodity:
    """Encapsulate information for a commodity"""

    def __init__(
        self,
        commodity: str,
        currency: str = "$",
        yahoo_ticker: str = "",
        output_dir: str = "./",
    ):

        self.commodity = commodity
        self.currency: str = currency
        self.output_dir = output_dir
        self.verbose = 0  # Default verbosity

        if yahoo_ticker == "":
            self.yahoo_ticker = commodity
        else:
            self.yahoo_ticker = yahoo_ticker

        # Ensure the output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
        self.file: str = os.path.join(self.output_dir, f"{self.yahoo_ticker}.ledger")

        # adjust the ticker to yahoo to make it easier to loop over multiple commodities
        # if self.yahoo_ticker in ["VWCE", "SXR8"]:
        #     self.yahoo_ticker = f"{self.yahoo_ticker}.DE"
        #     self.currency: str = "€"
        # if self.commodity[-1] in ["3", "4", "1", "5"]:
        #     # if ticker end with number, it is a Brazilian stock, which has a
        #     # ".SA" suffix
        #     self.yahoo_ticker = f"{self.yahoo_ticker}.SA"
        #     self.currency = "R$"


def adjust_for_split(
    date: str, value: float, split_ratio: float, split_date: datetime.date
) -> float:
    """Adjust value because of split."""
    # check if date is before split_date
    if datetime.datetime.strptime(date, "%Y-%m-%d").date() <= split_date:
        value = value * split_ratio

    return value


def get_last_date_recorded(
    commodity: Commodity, verbose: int = 0
) -> datetime.date | None:
    """Get the last date recorded in the file. Returns None if not found or error."""
    try:
        with open(commodity.file, "r") as f:
            lines = f.read().splitlines()
            if not lines:
                if verbose >= 1:
                    print(f"File {commodity.file} is empty.")
                return None
            last_line: str = lines[-1]
            # Attempt to parse the date from the last line
            parts = last_line.split()
            if len(parts) > 1 and parts[0] == "P":
                last_date_str: str = parts[1]
                last_date: datetime.date = datetime.datetime.strptime(
                    last_date_str, "%Y-%m-%d"
                ).date()
                return last_date
            else:
                if verbose >= 1:
                    print(
                        f"Could not parse date from last line in {commodity.file}: {last_line}"
                    )
                return None
    except FileNotFoundError:
        if verbose >= 1:
            print(f"File {commodity.file} not found when trying to get last date.")
        return None
    except (IndexError, ValueError) as e:
        if verbose >= 1:
            print(f"Error parsing last date in {commodity.file}: {e}")
        return None


def get_initial_date(
    commodity: Commodity, default_initial_date: datetime.date, verbose: int = 0
) -> datetime.date:
    """Get the date from which to obtain the commodity values."""
    last_date_recorded = get_last_date_recorded(commodity, verbose)

    if last_date_recorded:
        # Start fetching from the day *after* the last recorded date
        initial_date = last_date_recorded + datetime.timedelta(days=1)
        if verbose >= 1:
            print(f"Last date recorded in {commodity.file}: {last_date_recorded}")
            print(f"Setting initial fetch date to: {initial_date}")
        return initial_date
    else:
        # Use default if file doesn't exist or last date couldn't be determined
        if verbose >= 1:
            if os.path.exists(commodity.file):
                print(
                    f"Could not read last date from {commodity.file}, using default initial date: {default_initial_date}"
                )
            else:
                print(
                    f"File {commodity.file} not found, using default initial date: {default_initial_date}"
                )
        return default_initial_date


def get_split_ratio_and_date(input: str) -> tuple[float, datetime.date]:
    """Get split ratio and date from string input."""
    ratio_, date_ = input.split(",")

    # convert to appropriate types
    #
    # Example:
    #
    # 30:1 (ratio_from:ratio_to) if we have 60 stocks we get 60 * 1 / 30 = 2
    ratio_from: float = float(ratio_.split(":")[0])
    ratio_to: float = float(ratio_.split(":")[1])
    ratio: float = float(ratio_to / ratio_from)

    date: datetime.date = datetime.datetime.strptime(date_, "%Y-%m-%d").date()
    return (ratio, date)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Get value of commodity")
    _ = parser.add_argument(
        "--commodity",
        metavar="STRING",
        nargs=1,
        help="Commodity name used in the ledger (Ex. $ for USD).",
        required=True,
    )
    _ = parser.add_argument(
        "--yahooticker",
        help="Ticker from Yahoo database (Ex. ^VWCE for VWCE)",
        required=False,
        type=str,
        default="",
    )
    split_help = (
        "Adjust historical prices with split ratio from specified"
        "date (x:y,YYYY-MM-DD)."
    )
    _ = parser.add_argument(
        "--split",
        help=split_help,
        nargs="+",
        required=False,
        type=str,
        default=[],
    )
    initial_date_help = "Date from which to collect data (YYYY-MM-DD)."
    _ = parser.add_argument(
        "--begin",
        help=initial_date_help,
        required=False,
        type=lambda s: datetime.datetime.strptime(s, "%Y-%m-%d").date(),
        default=datetime.date(2018, 1, 1),
    )
    _ = parser.add_argument(
        "--currency",
        help="Commodity currency ($)",
        required=False,
        type=str,
        default="$",
    )
    _ = parser.add_argument(
        "--latest-price",
        help="Get price from latest working date.",
        required=False,
        action="store_true",
    )
    _ = parser.add_argument(
        "--output-dir",
        help="Directory to save the output ledger file.",
        required=False,
        type=str,
        default=".",
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        help="Set output verbosity level (0=silent, 1=normal, 2=detailed).",
        type=int,
        default=0,
        choices=[0, 1, 2],
    )
    args = parser.parse_args()
    verbose_level: int = args.verbose  # pyright ignore[reportAny]

    # Resolve the output directory to an absolute path
    absolute_output_dir = os.path.abspath(args.output_dir)

    commodity = Commodity(
        args.commodity[0],
        args.currency,
        args.yahooticker,
        absolute_output_dir,  # pyright ignore[reportAny]
    )
    commodity.verbose = verbose_level  # Set verbosity in the commodity object

    if verbose_level >= 1:
        print(f"Processing commodity: {commodity.commodity}")
        print(f"Yahoo Ticker: {commodity.yahoo_ticker}")
        print(f"Currency: {commodity.currency}")
        print(f"Output directory: {commodity.output_dir}")
        print(f"Ledger file path: {commodity.file}")

    initial_date = get_initial_date(
        commodity, default_initial_date=args.begin, verbose=verbose_level
    )  # pyright ignore[reportAny]

    # --- Fetch historical data once ---
    history_data = pd.DataFrame()  # Initialize empty DataFrame
    try:
        if verbose_level >= 2:
            print(f"Attempting to fetch data for ticker: {commodity.yahoo_ticker}")
        with warnings.catch_warnings():
            warnings.simplefilter(action="ignore", category=FutureWarning)
            ticker = yq.Ticker(commodity.yahoo_ticker)
            # fetch data one early to avoid different date time formats in the dataframe
            end_fetch_date = datetime.date.today() - datetime.timedelta(days=1)
            history_data = ticker.history(
                start=initial_date, end=end_fetch_date, adj_ohlc=True
            )

        # Ensure the index is just the date part for easier lookup
        if isinstance(history_data.index, pd.MultiIndex):
            history_data.index = history_data.index.get_level_values("date")

        # Check if the index is already a DateTimeIndex
        # Assuming history_data.index is still the original mixed object type
        utc_index = pd.to_datetime(history_data.index, errors="coerce", utc=True)

        # Step 2: Remove the timezone information to make it naive
        naive_index = utc_index.tz_localize(None)  # or .tz_convert(None)

        history_data.index = naive_index

        # Ensure index is sorted DateTimeIndex
        history_data = history_data.sort_index()
        if verbose_level >= 1:
            print(
                f"Fetched {history_data.shape[0]} historical data points for {commodity.commodity}."
            )
        if verbose_level >= 2:
            print(f"Fetched history_data head:\n{history_data.head()}")

    except Exception as e:
        if verbose_level >= 1:  # Always print errors if verbosity >= 1
            print(f"Error fetching historical data for {commodity.commodity}: {e}")
        history_data = pd.DataFrame()  # Ensure it's an empty DataFrame on error
        # Depending on requirements, you might exit here or continue if possible

    # --- Process historical data for month ends ---
    if not history_data.empty:
        # Keep track of dates already present in the output file
        processed_dates = set()
        if os.path.exists(commodity.file):
            try:
                with open(commodity.file, "r") as f:
                    for line in f:
                        if line.startswith("P "):
                            try:
                                date_str = line.split()[1]
                                processed_dates.add(
                                    datetime.datetime.strptime(
                                        date_str, "%Y-%m-%d"
                                    ).date()
                                )
                            except (IndexError, ValueError):
                                if verbose_level >= 2:
                                    print(
                                        f"Skipping malformed line in {commodity.file}: {line}"
                                    )
                                continue  # Ignore malformed lines
            except FileNotFoundError:
                if verbose_level >= 2:
                    print(
                        f"File {commodity.file} not found while checking processed dates."
                    )
                pass  # File doesn't exist yet, nothing is processed

        # Generate target month-end dates within the fetched range
        target_dates = pd.date_range(initial_date, datetime.date.today(), freq="BME")

        # Find the closest available historical data points for each target date
        # Use reindex with forward fill to find the last known price ON or BEFORE the target date
        # Ensure target_dates timezone matches history_data.index timezone (or lack thereof)
        if isinstance(history_data.index, pd.DatetimeIndex):
            if history_data.index.tz is None:
                target_dates = target_dates.tz_localize(None)
            else:
                # Or localize target_dates to the history_data timezone if needed (less common for daily data)
                target_dates = target_dates.tz_localize(history_data.index.tz)
        # If history_data.index is not a DatetimeIndex, assume target_dates (which is DatetimeIndex)
        # doesn't need localization relative to it. This might need adjustment if non-datetime indices occur.
        # doesn't need localization relative to it. This might need adjustment if non-datetime indices occur.

        relevant_data = history_data.reindex(target_dates, method="ffill").dropna()

        if verbose_level >= 2:
            print(
                f"Target month-end dates range: {target_dates.min()} to {target_dates.max()}"
            )
            print(
                f"Relevant data shape before filtering processed dates: {relevant_data.shape}"
            )

        # Filter out dates already processed
        original_count = len(relevant_data)
        # Explicitly compare datetime.date objects to avoid FutureWarning
        # Convert processed_dates set to a list/array for np.isin
        processed_dates_list = list(processed_dates)
        # Get index dates as numpy array of datetime.date objects
        index_dates = relevant_data.index.date
        # Use np.isin for comparison
        is_processed_mask = np.isin(index_dates, processed_dates_list)
        relevant_data = relevant_data[~is_processed_mask]
        filtered_count = len(relevant_data)
        if verbose_level >= 2:
            print(
                f"Filtered out {original_count - filtered_count} already processed dates."
            )
            print(
                f"Relevant data shape AFTER filtering processed dates: {relevant_data.shape}"
            )
            if not relevant_data.empty:
                print(f"Relevant data head:\n{relevant_data.head()}")

        # Ensure the output directory exists before writing loop
        # Create directory if it doesn't exist (idempotent)
        output_dir_path = os.path.dirname(commodity.file)
        os.makedirs(output_dir_path, exist_ok=True)
        # Note: Checking isdir after makedirs might not be reliable due to potential race conditions
        # if verbose_level >= 2 and not os.path.isdir(output_dir_path):
        #      print(f"Created output directory: {output_dir_path}") # Log creation attempt instead?

        if not relevant_data.empty:
            if verbose_level >= 1:
                print(
                    f"Writing {len(relevant_data)} new month-end entries to {commodity.file}..."
                )
            with open(commodity.file, "a") as f:
                for date_ts, row in relevant_data.iterrows():
                    date = date_ts.date()  # Convert timestamp to date
                    value = row["close"]  # Assuming 'close' is the column name

                    # Adjust for split
                    date_str = date.strftime("%Y-%m-%d")
                    adjusted_value = value  # Start with the fetched value
                    for splits_ratio_and_date in args.split:
                        split_ratio, split_date = get_split_ratio_and_date(
                            splits_ratio_and_date
                        )
                        # Adjust if the historical data point's date is before the split date
                        adjusted_value = adjust_for_split(
                            date_str, adjusted_value, split_ratio, split_date
                        )

                    # Write to file
                    try:
                        f.write(
                            f'P {date_str} "{commodity.commodity}" {commodity.currency}{adjusted_value:f}\n'
                        )
                        if verbose_level >= 2:
                            print(
                                f"  Wrote: P {date_str} {commodity.commodity} {commodity.currency}{adjusted_value:f}"
                            )
                    except Exception as write_error:
                        if verbose_level >= 1:  # Always print errors if verbosity >= 1
                            print(
                                f"Error writing entry for date {date_str}: {write_error}"
                            )
                        # Optionally break or continue depending on desired behavior on error
                        # break
        else:
            if verbose_level >= 1:
                print("No new month-end data to write.")

    # --- Get only the price from the latest working date if requested ---
    if args.latest_price and not history_data.empty:
        # Find the latest date potentially written to the file (by the loop above or previous runs)
        last_date_recorded = None
        if os.path.exists(commodity.file):
            try:
                last_date_recorded = get_last_date_recorded(commodity)
            except (
                FileNotFoundError,
                IndexError,
                ValueError,
            ):  # Handle empty/malformed file
                pass  # No valid date recorded yet

        # Get the last row from the fetched historical data
        latest_entry = history_data.iloc[-1]
        latest_date = latest_entry.name.date()  # Assumes index is datetime
        latest_value = latest_entry["close"]

        if verbose_level >= 1:
            print(
                f"Latest fetched date: {latest_date}, Last recorded date in file: {last_date_recorded}"
            )

        # Write only if this date hasn't been recorded yet
        if last_date_recorded is None or latest_date > last_date_recorded:
            if verbose_level >= 1:
                print(f"Writing latest price for {latest_date} to {commodity.file}...")
            date_str = latest_date.strftime("%Y-%m-%d")
            adjusted_value = latest_value
            # Adjust for split if necessary
            for splits_ratio_and_date in args.split:
                split_ratio, split_date = get_split_ratio_and_date(
                    splits_ratio_and_date
                )
                adjusted_value = adjust_for_split(
                    date_str, adjusted_value, split_ratio, split_date
                )

            # Ensure the directory exists before writing (might be redundant, but safe)
            os.makedirs(os.path.dirname(commodity.file), exist_ok=True)
            try:
                with open(commodity.file, "a") as f:
                    f.write(
                        f'P {date_str} "{commodity.commodity}" {commodity.currency}{adjusted_value:f}\n'
                    )
                    if verbose_level >= 2:
                        print(
                            f"  Wrote latest: P {date_str} {commodity.commodity} {commodity.currency}{adjusted_value:f}"
                        )
            except Exception as write_error:
                if verbose_level >= 1:  # Always print errors if verbosity >= 1
                    print(
                        f"Error writing latest price entry for date {date_str}: {write_error}"
                    )
        else:
            if verbose_level >= 1:
                print(
                    "Latest price date is not newer than the last recorded date. Skipping write."
                )
    elif args.latest_price and history_data.empty:
        if verbose_level >= 1:  # Always print errors if verbosity >= 1
            print(
                f"Cannot get latest price for {commodity.commodity} as historical data fetch failed."
            )

    if verbose_level >= 1:
        print(f"Finished processing {commodity.commodity}.")
