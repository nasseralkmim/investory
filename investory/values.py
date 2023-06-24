"""Get the commodities value from the internet.

Usage:

    $ python -m investory.values commodities.csv

The commodities file can be obtained for instance,

    $ hledger -f all.ledger commodities > commodities.csv

"""
import numpy as np
import pandas as pd
import yahooquery as yq
import datetime
from dataclasses import dataclass, field


@dataclass
class Commodity:
    ticker: str = ""
    date: list[datetime.date] = field(default_factory=list)
    price: list[float] = field(default_factory=list)


def collect_commodities(file: str) -> list[Commodity]:
    """Collect the commodities from the csv file."""
    tickers_list = np.loadtxt(file, dtype=str)
    commodities = []
    for ticker in tickers_list:
        commodities.append(Commodity(ticker=ticker))
    return commodities


def get_commodity_price(
    commodity: Commodity, date: datetime.date
) -> tuple[datetime.date | None, float | None]:
    """Getting the commodity price on specific date."""

    # get history price for the next 10 days
    data = yq.Ticker(commodity.ticker).history(
        start=date, end=date + datetime.timedelta(days=10)
    )

    # extract just the first valid date and close value
    try:
        # get string for datetime object
        date = data.index[0][1].strftime("%Y-%m-%d")
        price = data.close[0]
    except IndexError:
        # if after 10 days there still no data, it is probably not available
        price=None
        date=None

    return date, price


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Process multiple file paths")
    parser.add_argument(
        "file_paths", metavar="FILE_PATHS", nargs="+", help="File paths to process"
    )
    args = parser.parse_args()

    file = args.file_paths[0]

    commodities = collect_commodities(file)

    # loop over month end (business day 'BM') from 2017 until today
    for month_end in pd.date_range(
        datetime.date(2017, 1, 1), datetime.date(2017, 3, 1), freq="BM"
    ):
        # loop over each commodity and add the price and date to their list
        for commodity in commodities:
            date, price = get_commodity_price(commodity, month_end)
            commodity.date.append(date)
            commodity.price.append(price)


    print(commodities[0])
