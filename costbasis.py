"""Process the investments transactions csv.

Add cost basis information.
The cost basis used is the *average cost*.

"""
import pandas as pd
import numpy as np

pd.set_option("display.max_rows", 500)
pd.set_option("display.max_columns", 500)
pd.set_option("display.width", 1000)

files = [
    "../sources/investments/test.csv",
    # "../sources/investments/stocks-2022.out.csv",
    # "../sources/investments/stocks-2021.out.csv",
    # "../sources/investments/stocks-2020.out.csv",
    # "../sources/investments/stocks-2019.out.csv",
    # "../sources/investments/stocks-2018.out.csv",
    # "../sources/investments/etf-2022.csv",
    # "../sources/investments/etf-2021.csv",
    # "../sources/investments/reit-2021.out.csv",
    # "../sources/investments/reit-2020.out.csv",
    # "../sources/investments/reit-2019.out.csv",
]


def collect_transactions(files: list[str]) -> pd.DataFrame:
    """Collect transactions from all 'csv' files into a single Dataframe."""
    transactions = pd.DataFrame()
    transaction_list = []
    for file in files:
        transaction_list.append(pd.read_csv(file))
        transactions = pd.concat(transaction_list)

    return transactions


def compute_cost_basis(transactions: pd.DataFrame):
    """Compute the average cost basis for each purchase transaction."""
    print(transactions)


def adjust_volume(transactions: pd.DataFrame) -> pd.DataFrame:
    """Adjust position volume based on type and add column.

    The type of the trade defines it the shares will add or subtract.
    """
    transactions.loc[transactions["type"] == "sell", "vol"] *= -1
    return transactions


def compute_net_position(transactions: pd.DataFrame) -> pd.DataFrame:
    """Compute net position from trades.

    The net position represents what we hold at the moment.

    """
    transactions["date"] = pd.to_datetime(transactions["date"])

    def compute_each_row(r):
        net_acc_vol = transactions.loc[
            (transactions["ticker"] == r["ticker"])
            & (transactions["date"] <= r["date"]),
            "vol",
        ].sum()
        return net_acc_vol

    transactions["inventory"] = transactions.apply(compute_each_row, axis=1)
    return transactions


def compute_average_cost(transactions: pd.DataFrame) -> pd.DataFrame:
    """Compute average cost basis for all inventories.

    Each commodity, or asset, is treated as an inventory. The cost of each
    inventory item is computed based on a weighted average of the /purchases/ on
    the period.  See [1]_ for some explanation.

    Some terms:

    1. cost: how much we pay for the items in the inventory.
    2. inventory: collection of items that were purchased in different
    quantities and with different cost.
    3. price: what the market asked for.

    The period is marked by a selling event. When we sell, the inventory lots
    are grouped into a new lot with a cost associated with. Next inventory
    additions will begin from this point. So we need to track the periods.

    Example
    -------
    Exemple of two lots purchase of the AAAA inventory::

                date ticker  price  vol   total  inventory  inventory cost  avg cost
           2021-01-01  AAAA   10.0  100  1000.0        100    1000.0           10.00
           2021-01-02  AAAA   12.0  100  1200.0        200    2200.0           11.00

    We see that after buying a second lot for 12.0, the average cost increases to 11.00.
    This means that we can see it as a single lot of 200 with cost 11.00. When
    selling, the lots are merged into a single one with cost determined by this
    average cost.

    For example, a selling transaction::

                date ticker  price  vol   total  inventory  inventory cost*  avg cost*  period
           2021-01-03  AAAA   13.0  -50  -650.0        150   (* 150 11)1650        11        1

    The selling transaction has a different formula for the 'avg cost' and for
    inventory cost.  The 'avg cost' is obtained from the prior period
    transactions, period 0.  It is the last 'avg cost' from the period.  We also
    update the period count to 1 now.

    With that we can compute a capital gain,::

        2021-01-03 * Sell AAAA
            assets:stocks:AAAA  -50 AAAA @ 11.00  ; = 550 here we use our average cost basis
            assets:cash  650 ; debit this value to the cash account
            income:capital gain:AAAA  -100 ; credit the income the rest, which we may pay taxes

    After that, we make another purchase,::

                date ticker  price  vol   total    inventory      inventory cost            avg cost  period
           2021-01-04  AAAA   9.0   50    450.0          200    (+ 1650 450)2100  (/ 2100 200.0)10.5       1

    Notice that the 'inventory cost' uses information only from the respective
    period 1.

    .. [1] https://www.investopedia.com/terms/a/averagecostmethod.asp
    """
    transactions["total"] = transactions["vol"] * transactions["price"]

    def inventory_total(r):
        period_total = transactions.loc[
                (transactions["ticker"] == r["ticker"])
                & (transactions["date"] <= r["date"])
                & (transactions["period"] == r["period"])
                & (transactions["type"] != "sell"),
                "total",
            ].sum()
        if r["type"] == "buy":
            pass
        elif r["type"] == "sell":
            pass
        return r

    transactions = transactions.apply(inventory_total, axis=1)
    return transactions


class Inventory:
    """Defines the inventory object to keep track of commodity lots."""
    def __init__(self, transactions: pd.DataFrame):
        self.transactions = transactions.copy()
        self._set_transactions_epochs()
        self._compute_transaction_cost()
        self._set_inventory()
        self._compute_inventory_cost()

    def _set_transactions_epochs(self) -> None:
        """Add an 'epoch' to each lot for each item in the inventory.

        The epochs are marked by a selling event.

        """
        self.transactions.loc[:, "epoch"] = (
            self.transactions["type"] == "sell"
        ).cumsum()

    def _compute_transaction_cost(self) -> pd.DataFrame:
        """Compute the total cost for each transaction."""
        self.transactions["transaction cost"] = (
            self.transactions["vol"] * self.transactions["price"]
        )

    def _set_inventory(self):
        self.transactions["inventory"] = (
            self.transactions["vol"].cumsum()
        )

    def _compute_inventory_cost(self) -> pd.DataFrame:
        """Compute the total inventory cost for each transaction.

        The inventory cost is the current total cost of all lots in the inventory.

        """
        # divide the data frame for each epoch
        epoch_groups = self.transactions.groupby("epoch")
        epoch_trades = [epoch_groups.get_group(x) for x in epoch_groups.groups]

        # process each epoch separetely
        for epoch, trades in enumerate(epoch_trades):
            # first epoch has no selling
            if epoch == 0:
                self.transactions.loc[
                    self.transactions["epoch"] == epoch, "inventory cost"
                ] = trades["transaction cost"].cumsum()

                self.transactions.loc[
                    self.transactions["epoch"] == epoch, "average cost"
                ] = trades["transaction cost"].cumsum() / trades["vol"].cumsum()

                # go to next epoch
                continue

            # this is performed trade by trade on each epoch
            for trade in trades.itertuples():
                if trade.type == "sell":
                    # selling event gets its inventory cost based on average cost from
                    # previous epoch
                    previous_avg_cost = self.transactions.loc[
                        self.transactions["epoch"] == epoch - 1, "average cost"
                    ].iloc[-1]

                    self.transactions.loc[trade.Index, "average cost"] = previous_avg_cost

                    previous_inventory_cost = self.transactions.loc[
                        self.transactions["epoch"] == epoch - 1, "inventory cost"
                        ].iloc[-1]

                    self.transactions.loc[trade.Index, "inventory cost"] = (
                        previous_avg_cost * trade.vol + previous_inventory_cost
                    )

                if trade.type == "buy":
                    # add the inventory cost after the selling transaction
                    inventory_cost_after_selling = self.transactions.loc[
                        (self.transactions["epoch"] == epoch)
                        & (self.transactions["type"] == "sell"), 
                        "inventory cost"
                    ].iloc[-1]

                    # consider the index
                    # TODO: maybe better to use `iterrows()`
                    id = self.transactions.columns.get_loc("transaction cost") + 1
                    self.transactions.loc[trade.Index, "inventory cost"] = (
                        trade[id] + inventory_cost_after_selling
                    )

                    self.transactions.loc[trade.Index, "average cost"] = (
                        trade[id] + inventory_cost_after_selling
                    ) / trade.inventory


def generate_aggregate_inventory(transactions: pd.DataFrame) -> list[Inventory]:
    """Create a list with each inventory transaction data"""
    groups = transactions.groupby(by="ticker")
    aggregate_inventory = [Inventory(groups.get_group(x)) for x in groups.groups]
    return aggregate_inventory


if __name__ == "__main__":
    transactions = collect_transactions(files)
    transactions = adjust_volume(transactions)
    aggregated_inventory = generate_aggregate_inventory(transactions)

    print(aggregated_inventory[0].transactions)
    print(aggregated_inventory[1].transactions)
