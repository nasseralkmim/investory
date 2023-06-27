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
    yahoo_name: str = ticker
    date: list[str] = field(default_factory=list)
    price: list[float] = field(default_factory=list)

    if ticker in ["VWCE", "SXR8"]:
        currency: str = "€"
    else:
        currency: str = "$"


def get_commodity_price(
    commodity: Commodity, date: datetime.date
) -> tuple[str, float]:
    """Getting the commodity price on specific date."""

    # if ticker end with number, it is a Brazilian stock, which has a
    # ".SA" suffix
    if commodity.ticker[-1] in ["3", "4", "11", "5", "31"]:
        commodity.yahoo_name = f"{commodity.ticker}.SA"
        commodity.currency = "R$"

    # get history price for the next 10 days
    data = yq.Ticker(commodity.ticker).history(
        start=date, end=date + datetime.timedelta(days=10)
    )

    # extract just the first valid date and close value
    try:
        # get string for datetime object
        date_string = data.index[0][1].strftime("%Y-%m-%d")
        value = data.close[0]
        print("Found value ", value, "at ", date_string)
    except IndexError:
        # if after 10 days there still no data, it is probably not available
        value = np.NaN
        date_string = ""

    return date_string, value


def get_last_date_recorded(commodity: str) -> datetime.date:
    """Get the last date recorded in the file."""
    return datetime.date(2023, 1, 1)


def get_initial_date(commodity: str) -> datetime.date:
    """Get the date from which to obtain the commodity values."""
    if os.path.exists(f"{commodity}.ledger"):
        last_date_recorded = get_last_date_recorded(commodity)
        return last_date_recorded
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

    initial_date = get_initial_date(commodity.ticker)

    # loop over month end (business day 'BM') from 2017 until today
    for month_end in pd.date_range(
        initial_date, datetime.date(2017, 3, 1), freq="BM"
    ):
        date, value = get_commodity_price(commodity, month_end)
        with open(f"{commodity.ticker}.ledger", "w") as f:
            f.write(f"P {date} {commodity.ticker} {commodity.currency} {value}")
