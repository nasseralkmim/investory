"""Get the commodities value from the internet.

Usage:

    $ python -m investory.values <commodity ticker>

The commodities list can be obtained from a ledger file:

    $ hledger -f all.ledger commodities > commodities.csv

Some commodities tickers need a specific suffix according to Yahoo database.
For example, Brazilian stocks need a '.SA'.
"""
import numpy as np
import pandas as pd
import os
import yahooquery as yq
import datetime
from dataclasses import dataclass, field


@dataclass
class Commodity:
    ticker: str = ""
    date: list[str] = field(default_factory=list)
    price: list[float] = field(default_factory=list)

    def __post_init__(self):
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
        else:
            self.currency: str = "$"


def get_commodity_price(
    commodity: Commodity, date: datetime.date
) -> tuple[str, float]:
    """Getting the commodity price on specific date."""

    # get history price for the next 10 days
    data = yq.Ticker(commodity.yahoo_name).history(
        start=date, end=date + datetime.timedelta(days=10)
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


def get_initial_date(commodity: Commodity) -> datetime.date:
    """Get the date from which to obtain the commodity values."""
    if os.path.exists(f"{commodity.file}"):
        last_date_recorded = get_last_date_recorded(commodity)
        # add a month to this date, which will be the starting point
        return last_date_recorded + datetime.timedelta(weeks=4)
    else:
        # if file does not exist start from this date
        return datetime.date(2017, 1, 1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Get value of commodity")
    parser.add_argument(
        "commodity", metavar="STRING", nargs="+", help="Ticker form Yahoo database"
    )
    args = parser.parse_args()

    commodity = Commodity(ticker=args.commodity[0])

    initial_date = get_initial_date(commodity)

    # loop over month end (business day 'BM') from 2017 until today
    for month_end in pd.date_range(
        initial_date, datetime.date.today(), freq="BM"
    ):
        date, value = get_commodity_price(commodity, month_end)
        # only save if there is a value
        if not np.isnan(value):
            with open(f"{commodity.file}", "a") as f:
                f.write(f"P {date} {commodity.ticker} {commodity.currency}{value}\n")
