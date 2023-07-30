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
import yahooquery as yq
import datetime


class Commodity:
    """Encapsulate information for a commodity"""
    def __init__(self, ticker: str, currency: str = "$"):
        self.ticker = ticker
        self.file: str = f"{self.ticker}.ledger"

        self.yahoo_name: str = self.ticker

        # adjust the ticker to yahoo
        if self.ticker in ["VWCE", "SXR8"]:
            self.yahoo_name = f"{self.ticker}.DE"
            self.currency: str = "€"
        elif self.ticker[-1] in ["3", "4", "1", "5"]:
            # if ticker end with number, it is a Brazilian stock, which has a
            # ".SA" suffix
            self.yahoo_name = f"{self.ticker}.SA"
            self.currency = "R$"
        elif self.ticker in ["R$", "BRL", "BRLUSD"]:
            self.yahoo_name = "BRLUSD=X"
            self.currency = "$"
            self.ticker = "R$"
            self.file = "BRLUSD.ledger"
        elif self.ticker in ["€", "EUR", "EURUSD"]:
            self.yahoo_name = "EURUSD=X"
            self.ticker = "€"
            self.currency = "$"
            self.file = "EURUSD.ledger"
        elif self.ticker in ["BRLEUR"]:
            self.yahoo_name = "BRLEUR=X"
            self.ticker = "R$"
            self.currency = "€"
            self.file = "BRLEUR.ledger"
        else:
            self.currency: str = currency


def adjust_for_split(
    date: str, value: float, split_ratio: float, split_date: datetime.date
) -> float:
    """Adjust value because of split."""
    # check if date is before split_date
    if datetime.datetime.strptime(date, "%Y-%m-%d").date() <= split_date:
        value = value * split_ratio

    return value


def get_commodity_price(
    commodity: Commodity, date: datetime.date
) -> tuple[str, float]:
    """Getting the commodity price on specific date."""

    # get history price for the next 10 days
    # adj_ohlc: adjusts for split and dividends (default is just splits)
    data = yq.Ticker(commodity.yahoo_name).history(
        start=date, end=date + datetime.timedelta(days=10), adj_ohlc=True
    )

    # extract just the first valid date and close value
    try:
        # get string for datetime object
        date_string = data.index[0][1].strftime("%Y-%m-%d")
        value = data.close[0]
    except IndexError:
        # if after 10 days there still no data, it is probably not available
        value = np.NaN
        date_string = ""

    return date_string, value


def get_last_date_recorded(commodity: Commodity) -> datetime.date:
    """Get the last date recorded in the file."""
    with open(commodity.file, "r") as f:
        last_line: str = f.read().splitlines()[-1]
        last_date_str: str = last_line.split()[1]
        last_date: datetime.date = datetime.datetime.strptime(last_date_str, "%Y-%m-%d")
    return last_date


def get_initial_date(
    commodity: Commodity, default_initial_date: datetime.date
) -> datetime.date:
    """Get the date from which to obtain the commodity values."""
    if os.path.exists(f"{commodity.file}"):
        last_date_recorded = get_last_date_recorded(commodity)
        # add a month to this date, which will be the starting point
        return last_date_recorded + datetime.timedelta(weeks=4)
    else:
        # if file does not exist start from this date
        return default_initial_date


def get_split_ratio_and_date(input: str) -> tuple[float, datetime.date]:
    """Get split ratio and date from string input."""
    ratio_, date_ = input.split(",")

    # convert to appropriate types
    #
    # Example:
    #
    # 30:1 (ratio_from:ratio_to) if we have 60 stocks we get 60 * 1 / 30 = 2
    ratio_from: int = int(ratio_.split(":")[0])
    ratio_to: int = int(ratio_.split(":")[1])
    ratio: float = float(ratio_to / ratio_from)

    date: datetime.date = datetime.datetime.strptime(date_, "%Y-%m-%d").date()
    return (ratio, date)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Get value of commodity")
    parser.add_argument(
        "--commodity",
        metavar="STRING",
        nargs=1,
        help="Ticker form Yahoo database.",
        required=True,
    )
    split_help = (
        "Adjust historical prices with split ratio from specified"
        "date (x:y,YYYY-MM-DD)."
    )
    parser.add_argument(
        "--split",
        help=split_help,
        nargs="+",
        required=False,
        type=str,
        default=[],
    )
    initial_date_help = ("Date from which to collect data (YYYY-MM-DD).")
    parser.add_argument(
        "--begin",
        help=initial_date_help,
        required=False,
        type=lambda s: datetime.datetime.strptime(s, "%Y-%m-%d").date(),
        default=datetime.date(2018, 1, 1),
    )
    parser.add_argument(
        "--currency",
        help="Commodity currency ($)",
        required=False,
        type=str,
        default="$",
    )
    args = parser.parse_args()

    print(args.currency)

    commodity = Commodity(args.commodity[0], args.currency)

    initial_date = get_initial_date(
        commodity, default_initial_date=args.begin
    )

    # loop over month end (business day 'BM') from 2017 until today
    for month_end in pd.date_range(
        initial_date, datetime.date.today(), freq="BM"
    ):
        date, value = get_commodity_price(commodity, month_end)

        # only save if there is a value
        if not np.isnan(value):

            # Adjust for split
            for splits_ratio_and_date in args.split:
                split_ratio, split_date = get_split_ratio_and_date(
                    splits_ratio_and_date
                )
                value = adjust_for_split(date, value, split_ratio, split_date)

            with open(f"{commodity.file}", "a") as f:
                f.write(
                    f'P {date} "{commodity.ticker}" {commodity.currency}{value:.2f}\n'
                )
