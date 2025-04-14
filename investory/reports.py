"""Generate reports with hledger."""
# ruff: noqa: E501
import io
import os
import re
import subprocess
import sys
from concurrent.futures import Future, ProcessPoolExecutor, as_completed

import matplotlib.pyplot as plt
import pandas as pd


# Add this dictionary
CURRENCY_SYMBOL_TO_CODE = {
    "€": "EUR",
    "$": "USD",
    "R$": "BRL",
    # Add more mappings as needed
}


def get_account_colors(ledger: str, data_dir: str) -> dict[str, str]:
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


# Add this new function
def get_ledger_currencies(ledger_file: str, verbose: bool = False) -> set[str]:
    """Detect currency symbols/codes used in the ledger."""
    currencies = set()
    try:
        # Get all commodities declared or used
        commodities_output = run_command(f"hledger -f {ledger_file} commodities", verbose)
        # Regex to find common currency symbols or 3-letter uppercase codes
        # Adjust regex as needed for the currencies you expect
        currency_pattern = re.compile(r"^(?:[$€£¥]|R\$|[A-Z]{3})$")
        for line in commodities_output.splitlines():
            commodity = line.strip()
            if currency_pattern.match(commodity):
                currencies.add(commodity)
            # Check if the commodity is a key in our symbol map
            elif commodity in CURRENCY_SYMBOL_TO_CODE:
                 currencies.add(commodity)

    except Exception as e:
        print(f"Warning: Could not reliably detect currencies: {e}", file=sys.stderr)
        # Fallback or default currencies if detection fails? Or just return empty set?
        # For now, let's return potentially detected ones or empty
        pass # Returning potentially partially filled set

    if not currencies:
         print(f"Warning: No currencies detected in {ledger_file}. Reporting might be incomplete.", file=sys.stderr)

    return currencies


# Add this new function
def find_conversion_files(
    target_currency_symbol: str,
    other_currency_symbols: set[str],
    data_dir: str,
    currency_map: dict[str, str],
    verbose: bool = False,
) -> list[str]:
    """Find ledger files for converting other currencies to the target currency."""
    conversion_args = []
    target_code = currency_map.get(target_currency_symbol, target_currency_symbol)

    for other_symbol in other_currency_symbols:
        if other_symbol == target_currency_symbol:
            continue

        other_code = currency_map.get(other_symbol, other_symbol)
        if target_code == other_code: # Skip if codes map to the same (e.g. '$' and 'USD')
             continue

        # Construct possible filenames
        file1 = f"{target_code}{other_code}=X.ledger"
        file2 = f"{other_code}{target_code}=X.ledger"
        path1 = os.path.join(data_dir, file1)
        path2 = os.path.join(data_dir, file2)

        found = False
        if os.path.exists(path1):
            conversion_args.append("-f")
            conversion_args.append(path1)
            found = True
            if verbose:
                print(f"Found conversion file: {path1}")
        elif os.path.exists(path2):
            conversion_args.append("-f")
            conversion_args.append(path2)
            found = True
            if verbose:
                print(f"Found conversion file: {path2}")

        if not found and verbose:
            print(
                f"Warning: No conversion file found for {target_code}<->{other_code} "
                f"(looked for {file1} or {file2} in {data_dir})",
                file=sys.stderr,
            )

    return conversion_args


def generate_asset_distribution_graph(
    period: int,
    ledger: str,
    data_dir: str,
    target_currency: str, # Add target_currency
    conversion_args: list[str], # Add conversion_args
) -> None:
    """Generate pie plot for asset distribution."""
    command: list[str] = [
        "hledger",
        "-f", f"{ledger}",
        # Remove hardcoded BRLUSD/EURUSD files
        *conversion_args, # Add dynamic conversion files
        "bal", "acct:^assets:investments",
        "--end", f"{period + 1}",
        "--drop", "2",
        "--depth", "3",
        f"--value=end,{target_currency}", # Use target_currency
        "--no-total",
        "--infer-market-prices",
        "-O", "csv"
    ]

    process = subprocess.Popen(command,
                               stdout=subprocess.PIPE,
                               shell=False,
                               universal_newlines=True)
    output, _ = process.communicate()
    csv_data = io.StringIO(output)

    # Expected data frame structure
    # column 1: account names
    # column 2: account balances
    df: pd.DataFrame = pd.read_csv(csv_data)
    # Update the currency symbol replacement
    df = df.replace(re.escape(target_currency) + r"\s*", "", regex=True) # Use target_currency and escape it
    df["balance"] = df["balance"].apply(pd.to_numeric)

    # Avoid problems with negative values
    negative_df = df[df["balance"] < 0]
    df = df[df["balance"] >= 0]

    account_to_color = get_account_colors(ledger, data_dir)
    colors = [account_to_color[account] for account in df["account"]]

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


def generate_asset_evolution_graph(
    period: int,
    ledger: str,
    data_dir: str,
    target_currency: str, # Add target_currency
    conversion_args: list[str], # Add conversion_args
) -> None:
    """Generate area plot for asset evolution."""

    command: list[str] = [
        "hledger",
        "-f", f"{ledger}",
        # Remove hardcoded BRLUSD/EURUSD files
        *conversion_args, # Add dynamic conversion files
        "bal", "acct:^assets:investments",
        "--historical", "--monthly",
        "--drop", "2",
        "--depth", "3",
        f"--value=end,{target_currency}", # Use target_currency
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
    csv_data = io.StringIO(output)

    # Expected data frame structure
    # index: date
    # columns id: account names
    # columns values: account balances
    df: pd.DataFrame = pd.read_csv(csv_data, index_col=0)
    # Update the currency symbol replacement
    df = df.replace(re.escape(target_currency) + r"\s*", "", regex=True) # Use target_currency and escape it
    df = df[df.columns].apply(pd.to_numeric)
    # convert columns name to datetime
    df.columns = pd.to_datetime(df.columns, format="%Y-%m")
    df = df.transpose()

    account_to_color = get_account_colors(ledger, data_dir)

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


def run_command(command: str, verbose: bool = False) -> str:
    if verbose:
        print(f"Running command: {command}")
    try:
        result: subprocess.CompletedProcess = subprocess.run(
            command, shell=True, check=True, capture_output=True, text=True
        )
        if verbose:
            print("Command finished successfully")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {command}", file=sys.stderr)
        print(f"Return code: {e.returncode}", file=sys.stderr)
        print(f"Error output: {e.stderr.strip()}", file=sys.stderr)
        raise


def generate_yearly_report(
    period: int,
    ledger: str,
    data_dir: str,
    target_currency: str, # Change 'currency' to 'target_currency'
    conversion_args: list[str], # Add conversion_args
    verbose: bool = False
):
    os.makedirs(f"reports/{period}", exist_ok=True)

    # Pass new arguments to graph functions
    generate_asset_distribution_graph(period, ledger, data_dir, target_currency, conversion_args)
    generate_asset_evolution_graph(period, ledger, data_dir, target_currency, conversion_args)

    # Prepare conversion args string for f-string insertion
    conv_args_str = ' '.join(conversion_args)

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
        # Balance sheet (needs currency conversion)
        f"echo -en '* Monthly investments evolution graph\n[[file:asset-evolution.svg]] [[file:asset-distribution.svg]]\n' > reports/{period}/bs-{period}.org",
        f"echo -en '* Summary balance sheet last three years\n' >> reports/{period}/bs-{period}.org",
        # Add {conv_args_str}, use {target_currency}, remove hardcoded -f for currencies
        f"hledger -f {ledger} {conv_args_str} bs --tree --pretty=no --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from {period - 2} to {period + 1}' --infer-market-prices --value=end,{target_currency} --yearly >> reports/{period}/bs-{period}.org",
        f"echo -en '* Balance sheet valued at period ends\n' >> reports/{period}/bs-{period}.org",
        # Add {conv_args_str}, use {target_currency} (implicitly via --value=end without currency?) - Check hledger docs if needed, but usually defaults work if prices exist. Let's keep it simple for now.
        f"hledger -f {ledger} {conv_args_str} bs --depth 3 --infer-market-prices --value=end --tree --pretty=no --no-total --period {period} >> reports/{period}/bs-{period}.org",

        # Cost basis section (needs careful review - converting to R$ was hardcoded)
        # This section seems specifically designed for R$. Let's make the target currency dynamic here too.
        f"echo -en '* Investments converted to cost in {target_currency}\n' >> reports/{period}/bs-{period}.org", # Use target_currency
        # This first part gets historical cost in original currency, no conversion needed yet
        f"hledger -f {ledger} bal type:AL --historical investments --period {period} --layout tall --tree --pretty=no --drop 5 --depth 5 --no-total >> reports/{period}/bs1-{period}.org",
        # This second part applies conversion. Add {conv_args_str}, use {target_currency}, remove hardcoded -f
        f"hledger -f {ledger} {conv_args_str} bal type:AL --historical investments --period {period} --infer-equity --cost --infer-cost --infer-market-prices --exchange={target_currency} --drop 3 | grep -v '                   0' >> reports/{period}/bs2-{period}.org", # Use target_currency
        f"echo -en 'Investments {period}, converted to cost in {target_currency} \n' >> reports/{period}/bs-{period}.org", # Use target_currency
        f"paste reports/{period}/bs1-{period}.org reports/{period}/bs2-{period}.org | column -s $'\t' -t >> reports/{period}/bs-{period}.org",
        f"rm reports/{period}/bs1-{period}.org reports/{period}/bs2-{period}.org",
    ]

    for command in commands:
        run_command(command, verbose)

    print(f"Completed report for period: {period}")


def generate_summary_report(
    ledger: str,
    data_dir: str,
    target_currency: str, # Change 'currency' to 'target_currency'
    conversion_args: list[str], # Add conversion_args
    verbose: bool = False
):
    # Pass new arguments to graph functions
    generate_asset_distribution_graph(0, ledger, data_dir, target_currency, conversion_args)
    generate_asset_evolution_graph(0, ledger, data_dir, target_currency, conversion_args)

    # Prepare conversion args string for f-string insertion
    conv_args_str = ' '.join(conversion_args)
    commands = [
        # Balance sheet
        "echo -en '* Monthly investments evolution graph\n[[file:asset-evolution.svg]] [[file:asset-distribution.svg]]\n' > reports/summary.org",
        "echo -en '* Summary balance sheet last three years\n' >> reports/summary.org",
        "echo -en '\n#+begin_export html\n' >> reports/summary.org",
        # Add {conv_args_str}, use {target_currency}, remove hardcoded -f for currencies
        f"hledger -f {ledger} {conv_args_str} bs --tree --pretty=no --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from 2 years ago to today' --infer-market-prices --value=end,{target_currency} --yearly --output-format html >> reports/summary.org",
        "echo -en '\n#+end_export' >> reports/summary.org",
    ]

    for command in commands:
        run_command(command, verbose)

    print("Completed summary report")


def get_ledger_years(ledger_file: str, verbose: bool = False) -> list[int]:
    stats_output: str = run_command(f"hledger -f {ledger_file} stats", verbose)
    for line in stats_output.split('\n'):
        if line.startswith("Transactions span"):
            span = line.split(":")[1].strip()
            start_year, end_year = map(lambda x: int(x.split('-')[0]), span.split(" to "))
            return list(range(start_year, end_year + 1))
    return []  # Return an empty list if no span is found


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate reports based on hledger data." # Updated description slightly
    )

    _ = parser.add_argument(
        "--ledger", help="Main ledger file", required=False, type=str, default="all.ledger"
    )
    _ = parser.add_argument(
        "--currency", help="Target currency symbol for valuation (e.g., €, $, R$)", required=False, type=str, default="€" # Updated help, default is €
    )
    _ = parser.add_argument(
        "--data-dir", help="Directory containing ledger and currency conversion files", required=False, type=str, default="./" # Updated help
    )
    _ = parser.add_argument(
        "--verbose", help="Show detailed command output and warnings", action="store_true" # Updated help
    )
    args = parser.parse_args()

    # --- New logic ---
    # Detect all currencies in the main ledger
    all_currencies = get_ledger_currencies(args.ledger, args.verbose)
    if args.verbose:
        print(f"Detected currencies: {all_currencies}")

    # Identify currencies that need conversion to the target currency
    target_symbol = args.currency
    other_currencies = all_currencies - {target_symbol}
    if args.verbose:
        print(f"Target currency: {target_symbol}")
        print(f"Other currencies requiring conversion checks: {other_currencies}")

    # Find the necessary conversion files
    conversion_args = find_conversion_files(
        target_symbol, other_currencies, args.data_dir, CURRENCY_SYMBOL_TO_CODE, args.verbose
    )
    if args.verbose:
        print(f"Conversion file arguments: {conversion_args}")
    # --- End new logic ---

    periods: list[int] = get_ledger_years(args.ledger, args.verbose)
    # Each process (CPU) runs the function for a period simultaneously
    with ProcessPoolExecutor(max_workers=len(periods) + 1) as executor: # +1 for summary report
        futures: list[Future] = [
            # Pass target_currency and conversion_args to yearly report
            executor.submit(generate_yearly_report, period, args.ledger, args.data_dir, target_symbol, conversion_args, args.verbose)
            for period in periods
        ]
        # Pass target_currency and conversion_args to summary report
        futures.append(executor.submit(generate_summary_report, args.ledger, args.data_dir, target_symbol, conversion_args, args.verbose))

        for future in as_completed(futures):
            try:
                future.result() # Check for exceptions raised in subprocesses
            except Exception as exc:
                 print(f'Report generation task raised an exception: {exc}')

# Local Variables:
# jinx-local-words: "bs bs-"
# End:
