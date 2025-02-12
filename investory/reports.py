"""Generate reports with hledger."""
# ruff: noqa: E501
import os
import subprocess
import io
import pandas as pd
import matplotlib.pyplot as plt

from concurrent.futures import ProcessPoolExecutor, as_completed, Future

from typing import list

import figtex


figtex.style()
DATA = "/home/nasser/Sync/documents/admin/finances/data"


def get_account_colors(ledger: str) -> dict[str, str]:
    """Get a dictionary mapping accounts to colors.

    Even if an account has zero balance, it will have a color assigned to it.
    """
    # Run the hledger command and capture the output
    # NOTE: accounts names hard coded: 'assets:investments'
    accounts_output = (
        subprocess.check_output(
            [
                "hledger",
                "-f",
                f"{ledger}",
                "accounts",
                "assets:investments",
                "--drop",
                "2",
                "--depth",
                "3",
            ]
        )
        .decode("utf-8")
        .strip()
    )

    # Split the output into individual account names
    accounts = accounts_output.split("\n")

    # Define a list of colors using "C" and numeric suffix
    colors = ["C" + str(i) for i in range(0, 10)]

    # Create a dictionary mapping accounts to colors
    account_colors = {}
    for i, account in enumerate(accounts):
        account_colors[account] = colors[
            i % len(colors)
        ]

    return account_colors


def generate_asset_distribution_graph(period: int, ledger: str) -> None:
    """Generate pie plot for asset distribution."""
    command: list[str] = [
        "hledger",
        "-f", f"{ledger}",
        "-f", f"{DATA}/prices/BRLUSD=X.ledger",
        "-f", f"{DATA}/prices/EURUSD=X.ledger",
        "bal", "acct:^assets:investments",
        "--end", f"{period + 1}",
        "--drop", "2",
        "--depth", "3",
        "--value=end,R$",
        "--no-total",
        "--infer-market-prices",
        "-O", "csv"
    ]

    process = subprocess.Popen(command,
                               stdout=subprocess.PIPE,
                               shell=False,
                               universal_newlines=True)
    output, _ = process.communicate()
    csv_ io.StringIO = io.StringIO(output)

    # Expected data frame structure
    # column 1: account names
    # column 2: account balances
    df: pd.DataFrame = pd.read_csv(csv_data)
    df = df.replace("R\\$", "", regex=True)
    df["balance"] = df["balance"].apply(pd.to_numeric)

    # Avoid problems with negative values
    negative_df = df[df["balance"] < 0]
    df = df[df["balance"] >= 0]

    account_to_color = get_account_colors(ledger)
    colors = [account_to_color[account] for account in df["account"]]
    print(colors)

    fig, ax = plt.subplots(figsize=(3, 3))
    if df.empty:
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, 'No data available',
                ha='center', va='center', fontsize=12)
        ax.axis('off')  # Hide axes
    else:
        df.plot.pie(y="balance", labels=df["account"],
                    colors=colors,
                    ylabel="",
                    ax=ax)
        plt.title('Asset Distribution')

        # display negative values as information text
        if not negative_df.empty:
            for i in range(len(negative_df)):
                account = negative_df.iloc[i]["account"]
                balance = negative_df.iloc[i]["balance"]
                ax.text(0.5, 0.5, f"Negative values:\n{account}: {balance}",
                        ha='center', va='center', fontsize=12)

    fig.savefig("reports/asset-distribution.svg",
                bbox_inches="tight", transparent=True)


def generate_asset_evolution_graph(period: int, ledger: str) -> None:
    """Generate pie plot for asset distribution."""

    command: list[str] = [
        "hledger",
        "-f", f"{ledger}",
        "-f", f"{DATA}/prices/BRLUSD=X.ledger",
        "-f", f"{DATA}/prices/EURUSD=X.ledger",
        "bal", "acct:^assets:investments",
        "--historical", "--monthly",
        "--drop", "2",
        "--depth", "3",
        "--value=end,R$",
        "--no-total",
        "--infer-market-prices",
        "-O", "csv"
    ]

    plot_dir = "reports"

    if period != 0:
        command.extend(["--end", f"{period + 1}"])
        plot_dir = f"reports/{period}"

    process = subprocess.Popen(command,
                               stdout=subprocess.PIPE,
                               shell=False,
                               universal_newlines=True)
    output, _ = process.communicate()
    csv_ io.StringIO = io.StringIO(output)

    # Expected data frame structure
    # index: date
    # columns id: account names
    # columns values: account balances
    df: pd.DataFrame = pd.read_csv(csv_data, index_col=0)
    df = df.replace("R\\$", "", regex=True)
    df = df[df.columns].apply(pd.to_numeric)
    # convert columns name to datetime
    df.columns = pd.to_datetime(df.columns, format="%Y-%m")
    df = df.transpose()

    account_to_color = get_account_colors(ledger)

    # Avoid problems with negative balances in the plot.
    # Replace negative values with np.NaN
    df = df.where(df >= 0)

    fig, ax = plt.subplots(figsize=(8, 3))
    if df.empty:
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, 'No data available',
                ha='center', va='center', fontsize=12)
        ax.axis('off')  # Hide axes
    else:
        df.plot.area(ax=ax, color=account_to_color)
        plt.title('Asset Evolution')
    fig.savefig(f"{plot_dir}/asset-evolution.svg",
                bbox_inches="tight", transparent=True)


def run_command(command: str) -> str:
    result: subprocess.CompletedProcess = subprocess.run(
        command, shell=True, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def generate_yearly_report(period: int, ledger: str, currency: str = "€"):
    os.makedirs(f"reports/{period}", exist_ok=True)

    generate_asset_distribution_graph(period, ledger)
    generate_asset_evolution_graph(period, ledger)

    commands = [
        # Income statement
        f"echo -en '* Monthly income graph\n[[file:monthly-in-{period}.svg]]\n' > reports/{period}/is-{period}.org",
        f"echo -en '* Summary income statement\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --depth 1 --period 'from {period - 2} to {period + 1}' --tree --pretty=no >> reports/{period}/is-{period}.org",
        f"echo -en '* Income statement\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --depth 2 --monthly --average --period {period} --tree --pretty=no >> reports/{period}/is-{period}.org",
        f"echo -en '* Full Income statement\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --yearly --period {period} --tree --pretty=no --layout tall >> reports/{period}/is-{period}.org",
        f"echo -en '* Full Income statement monthly\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --monthly --average --row-total --period {period} --tree --pretty=no --layout tall >> reports/{period}/is-{period}.org",
        # Balance sheet
        f"echo -en '* Monthly investments evolution graph\n[[file:asset-evolution.svg]] [[file:asset-distribution.svg]]\n' > reports/{period}/bs-{period}.org",
        f"echo -en '* Summary balance sheet last three years\n' >> reports/{period}/bs-{period}.org",
        f"hledger -f {ledger} bs --tree --pretty=no --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from {period - 2} to {period + 1}' --infer-market-prices --value=end,{currency} -f {DATA}/prices/EURUSD=X.ledger -f {DATA}/prices/BRLUSD=X.ledger --yearly >> reports/{period}/bs-{period}.org",
        f"echo -en '* Balance sheet valued at period ends\n' >> reports/{period}/bs-{period}.org",
        f"hledger -f {ledger} bs --depth 3 --infer-market-prices --value=end --tree --pretty=no --no-total --period {period} >> reports/{period}/bs-{period}.org",
        f"echo -en '* Investments converted to cost in R$\n' >> reports/{period}/bs-{period}.org",
        f"hledger -f {ledger} bal type:AL --historical investments --period {period} --layout tall --tree --pretty=no --drop 5 --depth 5 --no-total >> reports/{period}/bs1-{period}.org",
        f"hledger -f {ledger} bal type:AL --historical investments -f {DATA}/prices/EURUSD=X.ledger SD.ledger -f {DATA}/prices/BRLUSD=X.ledger --period {period} --infer-equity --cost --infer-cost --infer-market-prices --exchange=R$ --drop 3 | grep -v '                   0' >> reports/{period}/bs2-{period}.org",
        f"echo -en 'Investments {period}, converted to cost in R$ \n' >> reports/{period}/bs-{period}.org",
        f"paste reports/{period}/bs1-{period}.org reports/{period}/bs2-{period}.org | column -s $'\t' -t >> reports/{period}/bs-{period}.org",
        f"rm reports/{period}/bs1-{period}.org reports/{period}/bs2-{period}.org",
    ]

    for command in commands:
        run_command(command)

    print(f"Completed report for period: {period}")


def generate_summary_report(ledger: str, currency: str = "€"):
    generate_asset_distribution_graph(0, ledger)
    generate_asset_evolution_graph(0, ledger)
    commands = [
        # Balance sheet
        "echo -en '* Monthly investments evolution graph\n[[file:asset-evolution.svg]] [[file:asset-distribution.svg]]\n' > reports/summary.org",
        "echo -en '* Summary balance sheet last three years\n' >> reports/summary.org",
        "echo -en '\n#+begin_export html\n' >> reports/summary.org",
        f"hledger -f {ledger} bs --tree --pretty=no --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from 2 years ago to today' --infer-market-prices --value=end,{currency} -f {DATA}/prices/EURUSD=X.ledger -f {DATA}/prices/BRLUSD=X.ledger --yearly --output-format html >> reports/summary.org",
        "echo -en '\n#+end_export' >> reports/summary.org",
    ]

    for command in commands:
        run_command(command)

    print("Completed summary report")


def get_ledger_years(ledger_file: str) -> list[int]:
    stats_output: str = run_command(f"hledger -f {ledger_file} stats")
    for line in stats_output.split('\n'):
        if line.startswith("Transactions span"):
            span = line.split(":")[1].strip()
            start_year, end_year = map(lambda x: int(x.split('-')[0]), span.split(" to "))
            return list(range(start_year, end_year + 1))
    return []  # Return an empty list if no span is found


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generare report based on this ledger data."
    )

    parser.add_argument(
        "--ledger", help="Ledger file", required=False, type=str, default="all.ledger"
    )
    parser.add_argument(
        "--currency", help="Currency", required=False, type=str, default="€"
    )
    args = parser.parse_args()
    periods: list[int] = get_ledger_years(args.ledger)

    # Each process (CPU) runs the function for a period simultaneously
    with ProcessPoolExecutor(max_workers=len(periods)) as executor:
        futures: list[Future] = [
            executor.submit(generate_yearly_report, period, args.ledger, args.currency)
            for period in periods
        ]
        futures.append(executor.submit(generate_summary_report, args.ledger, args.currency))

        for future in as_completed(futures):
            future.result()

# Local Variables:
# jinx-local-words: "bs bs-"
# End:
