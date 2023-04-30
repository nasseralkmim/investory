"""Process the investments transactions csv.

Add cost basis information.
The cost basis used is the *average cost*.

"""
import pandas as pd

pd.set_option("display.max_rows", 500)
pd.set_option("display.max_columns", 500)
pd.set_option("display.width", 1000)

files = [
    "../sources/investments/test2.csv",
    "../sources/investments/test3.csv",
    # "../sources/investments/stocks-2018.out.csv",
    # "../sources/investments/stocks-2019.out.csv",
    # "../sources/investments/stocks-2020.out.csv",
    # "../sources/investments/stocks-2021.out.csv",
    # "../sources/investments/stocks-2022.out.csv",
    # "../sources/investments/etf-2022.csv",
    # "../sources/investments/etf-2021.csv",
    # "../sources/investments/reit-2021.out.csv",
    # "../sources/investments/reit-2020.out.csv",
    # "../sources/investments/reit-2019.out.csv",
]


def collect_transactions(files: list[str]) -> pd.DataFrame:
    """Collect transactions from all 'csv' files into a single Dataframe.

    We need to know the whole history of transactions for all commodities.

    """
    transactions = pd.DataFrame()
    transaction_list = []
    for file in files:
        df = pd.read_csv(file)
        # add the file name as a column
        df["file"] = file
        transaction_list.append(df)

    transactions = pd.concat(transaction_list)
    return transactions


def adjust_volume(transactions: pd.DataFrame) -> pd.DataFrame:
    """Adjust position volume based on type and add column.

    The type of the trade defines it the shares will add or subtract.
    """
    transactions.loc[transactions["type"] == "sell", "vol"] *= -1
    return transactions


class Inventory:
    """Defines the inventory object to keep track of commodity lots."""

    def __init__(self, transactions: pd.DataFrame):
        self.transactions = transactions.copy()

        # set the date properly and sort by date
        self.transactions["date"] = pd.to_datetime(
            self.transactions["date"], format="%m/%d/%Y"
        )
        self.transactions = self.transactions.sort_values(by="date")

        self._set_transactions_epochs()
        self._compute_transaction_cost()
        self._set_inventory()
        self._compute_inventory_cost()

    def _set_transactions_epochs(self) -> None:
        """Add an 'epoch' to each lot for each item in the inventory.

        The epochs are marked by a selling event.

        """
        self.transactions["epoch"] = (
            self.transactions["type"] == "sell"
        ).cumsum()

    def _compute_transaction_cost(self) -> pd.DataFrame:
        """Compute the total cost for each transaction."""
        self.transactions["transaction cost"] = (
            self.transactions["vol"] * self.transactions["price"]
        )

    def _set_inventory(self) -> None:
        """Set the current inventory for each transaction."""
        self.transactions["inventory"] = self.transactions["vol"].cumsum()

    def _compute_inventory_cost(self) -> pd.DataFrame:
        """Compute the total inventory cost for each transaction.

        The inventory cost is the current total cost of all lots in the
        inventory. See [1]_ for some explanation.

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
        # divide the data frame for each epoch
        epoch_groups = self.transactions.groupby("epoch")
        epoch_trades = [epoch_groups.get_group(x) for x in epoch_groups.groups]

        # process each epoch separately
        for epoch, trades in enumerate(epoch_trades):
            # first epoch has no selling
            if epoch == 0:
                self.transactions.loc[
                    self.transactions["epoch"] == epoch, "inventory cost"
                ] = trades["transaction cost"].cumsum()

                self.transactions.loc[
                    self.transactions["epoch"] == epoch, "average cost"
                ] = (trades["transaction cost"].cumsum() / trades["vol"].cumsum())

            if epoch > 0:
                # this is performed trade by trade on each epoch
                for trade in trades.itertuples():
                    if trade.type == "sell":
                        # selling event gets its inventory cost based on average cost from
                        # previous epoch
                        previous_avg_cost = self.transactions.loc[
                            self.transactions["epoch"] == epoch - 1, "average cost"
                        ].iloc[-1]

                        self.transactions.loc[
                            trade.Index, "average cost"
                        ] = previous_avg_cost

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
                            "inventory cost",
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


def save_output(inventory_list: list[Inventory]) -> None:
    """Save processed output as a 'csv' file for each year."""
    # combine the inventory of all commodities into a single dataframe
    consolidated_inventory = pd.DataFrame()
    transactions_list = []
    for inventory in inventory_list:
        transactions_list.append(inventory.transactions)
    consolidated_inventory = pd.concat(transactions_list)

    # group the dataframe by year and save csv
    # TODO: Make it more robust, right now it the date format is hard coded
    consolidated_inventory = consolidated_inventory
    grouped_by_year = consolidated_inventory.groupby(pd.Grouper(key="date", freq="Y"))
    for group in grouped_by_year.groups:
        group_df = grouped_by_year.get_group(group)
        filename = group_df["file"].iloc[0]
        # remove the file column
        group_df = group_df.drop("file", axis=1)
        group_df.to_csv(f"{filename[:-4]}.out.csv", index=False)


if __name__ == "__main__":
    transactions = collect_transactions(files)
    transactions = adjust_volume(transactions)
    inventory_list = generate_aggregate_inventory(transactions)
    save_output(inventory_list)
