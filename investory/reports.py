"""Generate reports with hledger."""

# ruff: noqa: E501
import io
import os
import re
import subprocess
import sys
import sys
import datetime
from concurrent.futures import Future, ProcessPoolExecutor, as_completed

import matplotlib.axes
import matplotlib.figure
import matplotlib.pyplot as plt
import pandas as pd
import ybankinplay as yq


CURRENCY_SYMBOL_TO_CODE = {
    "€": "EUR",
    "$": "USD",
    "R$": "BRL",
    # Add more mappings as needed
}


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
    account_colors: dict[str, str] = {}
    for i, account in enumerate(accounts):
        account_colors[account] = colors[i % len(colors)]

    return account_colors


def get_ledger_currencies(ledger_file: str, verbose: int = 0) -> set[str]:
    """Detect currency symbols/codes used in the ledger."""
    currencies: set[str] = set()
    try:
        # Get all commodities declared or used
        commodities_output = run_command(
            f"hledger -f {ledger_file} commodities", verbose
        )
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
        pass  # Returning potentially partially filled set

    if verbose >= 1 and not currencies:
        print(
            f"Warning: No currencies detected in {ledger_file}. Reporting might be incomplete.",
            file=sys.stderr,
        )

    return currencies


def find_conversion_files(
    target_currency_symbol: str,
    other_currency_symbols: set[str],
    data_dir: str,
    currency_map: dict[str, str],
    verbose: int = 0,
) -> list[str]:
    """Find ledger files for converting other currencies to the target currency."""
    conversion_args: list[str] = []
    target_code = currency_map.get(target_currency_symbol, target_currency_symbol)

    for other_symbol in other_currency_symbols:
        if other_symbol == target_currency_symbol:
            continue

        other_code = currency_map.get(other_symbol, other_symbol)
        if (
            target_code == other_code
        ):  # Skip if codes map to the same (e.g. '$' and 'USD')
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
            if verbose >= 1:
                print(f"Found conversion file: {path1}")
        elif os.path.exists(path2):
            conversion_args.append("-f")
            conversion_args.append(path2)
            found = True
            if verbose >= 1:
                print(f"Found conversion file: {path2}")

        if not found and verbose >= 1:
            print(
                f"Warning: No conversion file found for {target_code}<->{other_code} "
                f"(looked for {file1} or {file2} in {data_dir})",
                file=sys.stderr,
            )

    return conversion_args


def generate_asset_distribution_graph(
    period: int,
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    ax: matplotlib.axes.Axes,
) -> matplotlib.axes.Axes:
    """Generate pie plot for asset distribution on the given axes."""
    command: list[str] = [
        "hledger",
        "-f",
        f"{ledger}",
        *conversion_args,
        "bal",
        "acct:^assets:investments",
        "--end",
        f"{period + 1}",
        "--drop",
        "2",
        "--depth",
        "3",
        f"--value=end,{target_currency}",  # Use target_currency
        "--no-total",
        "--infer-market-prices",
        "-O",
        "csv",
    ]

    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, shell=False, universal_newlines=True
    )
    output, _ = process.communicate()
    csv_data = io.StringIO(output)

    # Expected data frame structure
    # column 1: account names
    # column 2: account balances
    df: pd.DataFrame = pd.read_csv(csv_data)  # pyright ignore[reportUnknownMemberType]
    # Update the currency symbol replacement
    df = df.replace(
        re.escape(target_currency) + r"\s*", "", regex=True
    )  # Use target_currency and escape it
    df["balance"] = pd.to_numeric(
        df["balance"]
    )  # pyright ignore[reportUnknownMemberType]

    # Avoid problems with negative values
    negative_filter: pd.Series = (
        df["balance"] < 0
    )  # pyright: ignore[reportMissingTypeArgument]
    negative_df: pd.DataFrame = df[
        negative_filter
    ].copy()  # pyright ignore[reportAssignmentType]
    df_positive: pd.DataFrame = df[~negative_filter]  # type: ignore

    account_to_color = get_account_colors(ledger)
    colors: list[str] = [
        account_to_color[account] for account in df_positive["account"]
    ]  # pyright: ignore[reportUnknownVariableType]

    # fig: matplotlib.figure.Figure # Removed: Use passed ax
    # ax: matplotlib.axes.Axes # Removed: Use passed ax
    # fig, ax = plt.subplots(figsize=(3, 3))  # Removed: Use passed ax

    if df_positive.empty:
        _ = ax.set_xlim(0, 1)  # Use passed ax
        _ = ax.set_ylim(0, 1)
        _ = ax.text(
            0.5,
            0.5,
            "No data available",  # pyright: ignore[reportUnknownMemberType]
            ha="center",
            va="center",
            fontsize=12,
        )
        _ = ax.axis("off")  # Hide axes
    else:
        # Plot pie chart without labels on slices
        wedges, _ = ax.pie(
            df_positive["balance"],
            colors=colors,
            startangle=90,  # Optional: Adjust start angle
            counterclock=False,  # Optional: Adjust direction
        )
        _ = ax.set_title("Asset Distribution")

        # Add legend to the side
        ax.legend(
            wedges,
            df_positive["account"],
            title="Accounts",
            loc="center left",
            bbox_to_anchor=(1, 0, 0.5, 1),  # Position legend outside plot area
            fontsize="small",  # Adjust font size if needed
        )

        # display negative values as information text
        if not negative_df.empty:
            for i in range(len(negative_df)):
                account: str = negative_df.iloc[i][
                    "account"
                ]  # pyright: ignore[reportUnknownVariableType]
                balance: float = negative_df.iloc[i][
                    "balance"
                ]  # pyright: ignore[reportUnknownVariableType]
                _ = ax_pie.text(
                    0.5,
                    0.5,
                    f"Negative values:\n{account}: {balance}",  # pyright: ignore[reportUnknownVariableType]
                    ha="center",
                    va="center",
                    fontsize=12,
                )

    # Removed saving logic
    # plot_dir = "reports"
    # if period != 0:
    #     plot_dir = f"reports/{period}"
    # fig.savefig(
    #     f"{plot_dir}/asset-distribution.svg",
    #     bbox_inches="tight",
    #     transparent=True,
    # )
    return ax  # Return the axes


def generate_asset_evolution_graph(
    period: int,
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    data_dir: str,  # Add data_dir parameter
    ax: matplotlib.axes.Axes,
    verbose: int = 0,  # Add verbose parameter for consistency
) -> matplotlib.axes.Axes:
    """Generate area plot for asset evolution on the given axes."""

    # Find all .ledger files in data_dir (commodity prices)
    data_files_args: list[str] = []
    if os.path.isdir(data_dir):
        # Correctly build the list ["-f", file1, "-f", file2, ...]
        for f in os.listdir(data_dir):
            if f.endswith(".ledger"):
                data_files_args.extend(
                    ["-f", os.path.join(data_dir, f)]
                )  # Use extend to add both items
    elif verbose >= 1:
        print(
            f"Warning: Data directory '{data_dir}' not found for asset evolution. Commodity prices might be missing.",
            file=sys.stderr,
        )

    command: list[str] = [
        "hledger",
        "-f",
        f"{ledger}",
        *data_files_args,  # Add data files
        *conversion_args,  # Add dynamic conversion files
        "bal",
        "acct:^assets:investments",
        "--historical",
        "--monthly",
        "--drop",
        "2",
        "--depth",
        "3",
        f"--value=end,{target_currency}",  # Use target_currency
        "--no-total",
        "--infer-market-prices",
        "-O",
        "csv",
    ]

    plot_dir = "reports"

    if period != 0:
        command.extend(["--end", f"{period + 1}"])
        plot_dir = f"reports/{period}"

    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, shell=False, universal_newlines=True
    )
    output, _ = process.communicate()
    csv_data = io.StringIO(output)

    # Expected data frame structure
    # index: date
    # columns id: account names
    # columns values: account balances
    df_evo: pd.DataFrame = pd.read_csv(
        csv_data, index_col=0
    )  # pyright: ignore[reportUnknownMemberType]
    # Update the currency symbol replacement
    # The replace operation can return Series or None, causing type issues. Ignore for now.
    df_evo = df_evo.replace(
        re.escape(target_currency) + r"\s*", "", regex=True
    )  # Use target_currency and escape it
    df_evo = df_evo[df_evo.columns].apply(pd.to_numeric)
    # convert columns name to datetime
    df_evo.columns = pd.to_datetime(df_evo.columns, format="%Y-%m")
    df_evo = df_evo.transpose()

    account_to_color = get_account_colors(ledger)

    # Avoid problems with negative balances in the plot.
    # Replace negative values with np.NaN
    df_evo = df_evo.where(df_evo >= 0)

    # fig: matplotlib.figure.Figure # Removed: Use passed ax
    # ax: matplotlib.axes.Axes # Removed: Use passed ax
    # fig, ax = plt.subplots(figsize=(7, 3)) # Removed: Use passed ax
    if df_evo.empty:
        _ = ax.set_xlim(0, 1)  # Use passed ax
        _ = ax.set_ylim(0, 1)
        _ = ax.text(
            0.5,
            0.5,
            "No data available",
            ha="center",
            va="center",
            fontsize=12,
        )
        _ = ax.axis("off")  # Hide axes
    else:
        ax = df_evo.plot.area(
            ax=ax, color=account_to_color, legend=False
        )  # Use passed ax, remove legend
        _ = ax.set_title("Asset Evolution")
        _ = ax.set_ylabel(
            f"Value ({target_currency})"
        )  # Add Y-axis label with currency
    # Removed saving logic
    # fig.savefig(
    #     f"{plot_dir}/asset-evolution.svg",
    #     bbox_inches="tight",
    #     transparent=True,
    # )
    return ax  # Return the axes


def run_command(command: str, stdin_data: str | None = None, verbose: int = 0) -> str:
    """Execute a shell command and return its stdout.

    Args:
        command: The command string to execute.
        verbose: Verbosity level (0=silent, 1=normal, 2=high).
                 Level 2 prints the command being run and success message.
        stdin_data: Optional string to pass as standard input to the command.

    Returns:
        The standard output of the command.

    Raises:
        subprocess.CalledProcessError: If the command fails.
    """
    if verbose >= 2:
        print(f"Running command: {command}")
        if stdin_data:
            print(
                f"  with stdin: {stdin_data[:100]}..."
            )  # Print first 100 chars of stdin

    try:
        process = subprocess.Popen(
            command,
            shell=True,  # noqa: S602
            stdin=subprocess.PIPE if stdin_data else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",  # Explicitly set encoding
        )
        stdout_data, stderr_data = process.communicate(input=stdin_data)

        if process.returncode != 0:
            print(f"Command failed: {command}", file=sys.stderr)
            print(f"Return code: {process.returncode}", file=sys.stderr)
            print(f"Error output: {stderr_data.strip()}", file=sys.stderr)
            # Raise an error consistent with subprocess.run(check=True)
            raise subprocess.CalledProcessError(
                process.returncode, command, output=stdout_data, stderr=stderr_data
            )

            print("Command finished successfully")
        return stdout_data.strip()
    # Keep the original exception handling for CalledProcessError,
    # although communicate() failure might raise other exceptions too.
    except subprocess.CalledProcessError as e:
        # Error details are already printed above
        raise
    except Exception as e:
        # Catch other potential errors during Popen or communicate
        print(
            f"An unexpected error occurred running command: {command}", file=sys.stderr
        )
        print(f"Error: {e}", file=sys.stderr)
        raise


def generate_yearly_report(
    period: int,
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    data_dir: str,  # Add data_dir parameter
    verbose: int = 0,
):
    report_dir = f"reports/{period}"  # Use variable for clarity
    os.makedirs(report_dir, exist_ok=True)

    # Find all .ledger files in data_dir (commodity prices)
    data_files_args: list[str] = []
    if os.path.isdir(data_dir):
        data_files_args = [
            f"-f {os.path.join(data_dir, f)}"
            for f in os.listdir(data_dir)
            if f.endswith(".ledger")
        ]
    elif verbose >= 1:
        print(
            f"Warning: Data directory '{data_dir}' not found for period {period}. Commodity prices might be missing.",
            file=sys.stderr,
        )
    data_files_args_str = " ".join(data_files_args)

    # Prepare conversion args string for f-string insertion
    conv_args_str = " ".join(conversion_args)

    # Define report file paths
    is_report_file = os.path.join(report_dir, f"is-{period}.org")
    bs_report_file = os.path.join(report_dir, f"bs-{period}.org")
    bs1_temp_file = os.path.join(report_dir, f"bs1-{period}.org")
    bs2_temp_file = os.path.join(report_dir, f"bs2-{period}.org")

    # Note: Removed references to monthly-in-{period}.svg, asset-evolution.svg, asset-distribution.svg
    # Consider adding a reference to the main combined-overview.svg if desired, or keep yearly reports text-only.
    commands = [
        # Income statement
        f"echo -en '* Summary income statement\n' > {is_report_file}",  # Start new file
        f"hledger -f {ledger} is --sort --depth 1 --period 'from {period - 2} to {period + 1}' --tree --pretty=no >> {is_report_file}",
        f"echo -en '* Income statement\n' >> {is_report_file}",
        f"hledger -f {ledger} is --sort --depth 2 --monthly --average --period {period} --tree --pretty=no >> {is_report_file}",
        f"echo -en '* Full Income statement\n' >> {is_report_file}",
        f"hledger -f {ledger} is --sort --yearly --period {period} --tree --pretty=no --layout tall >> {is_report_file}",
        f"echo -en '* Full Income statement monthly\n' >> {is_report_file}",
        f"hledger -f {ledger} is --sort --monthly --average --row-total --period {period} --tree --pretty=no --layout tall >> {is_report_file}",
        # Balance sheet (needs currency conversion)
        f"echo -en '* Summary balance sheet last three years\n' > {bs_report_file}",  # Start new file
        # Add {conv_args_str}, {data_files_args_str}, use {target_currency}
        f"hledger -f {ledger} {data_files_args_str} {conv_args_str} bs --tree --pretty=no --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from {period - 2} to {period + 1}' --infer-market-prices --value=end,{target_currency} --yearly >> {bs_report_file}",
        f"echo -en '* Balance sheet valued at period ends\n' >> {bs_report_file}",
        # Add {conv_args_str}, {data_files_args_str}, use {target_currency}
        f"hledger -f {ledger} {data_files_args_str} {conv_args_str} bs --depth 3 --infer-market-prices --value=end,{target_currency} --tree --pretty=no --no-total --period {period} >> {bs_report_file}",  # Added target_currency to --value=end
        # Cost basis section
        f"echo -en '* Investments converted to cost in {target_currency}\n' >> {bs_report_file}",
        # This first part gets historical cost in original currency (no conversion/data files needed)
        f"hledger -f {ledger} bal type:AL --historical investments --period {period} --layout tall --tree --pretty=no --drop 5 --depth 5 --no-total > {bs1_temp_file}",  # Use > to overwrite temp file
        # This second part applies conversion. Add {conv_args_str}, {data_files_args_str}, use {target_currency}
        f"hledger -f {ledger} {data_files_args_str} {conv_args_str} bal type:AL --historical investments --period {period} --infer-equity --cost --infer-cost --infer-market-prices --exchange={target_currency} --drop 3 | grep -v '                   0' > {bs2_temp_file}",  # Use > to overwrite temp file
        f"echo -en 'Investments {period}, converted to cost in {target_currency} \n' >> {bs_report_file}",
        f"paste {bs1_temp_file} {bs2_temp_file} | column -s $'\\t' -t >> {bs_report_file}",
        f"rm {bs1_temp_file} {bs2_temp_file}",
    ]

    for command in commands:
        _ = run_command(command, verbose=verbose)

    if verbose >= 1:
        print(f"Completed report for period: {period}")


def generate_summary_report(
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    output_dir: str,
    verbose: int = 0,
):
    # Removed calls to individual graph functions
    # generate_asset_distribution_graph(0, ledger, target_currency, conversion_args)
    # generate_asset_evolution_graph(0, ledger, target_currency, conversion_args)

    # Prepare conversion args string for f-string insertion
    conv_args_str = " ".join(conversion_args)
    summary_file_abs = os.path.abspath(
        os.path.join(output_dir, "summary.org")
    )  # Absolute path to summary file
    combined_plot_abs = os.path.abspath(
        os.path.join(output_dir, "combined-overview.svg")
    )  # Absolute path to plot file
    # Calculate relative path from the directory containing summary.org to the plot file
    summary_dir = os.path.dirname(summary_file_abs)
    combined_plot_rel_path = os.path.relpath(combined_plot_abs, start=summary_dir)

    commands = [
        # Reference the new combined plot using the calculated relative path
        f"echo -en '* Portfolio Overview Graph\n[[file:{combined_plot_rel_path}]]\n' > {summary_file_abs}",  # Use absolute path for echo command target
        f"echo -en '* Summary balance sheet last three years\n' >> {summary_file_abs}",
        f"echo -en '\n#+begin_export html\n' >> {summary_file_abs}",
        # Add {conv_args_str}, use {target_currency}, remove hardcoded -f for currencies
        f"hledger -f {ledger} {conv_args_str} bs --tree --pretty=no --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from 2 years ago to today' --infer-market-prices --value=end,{target_currency} --yearly --output-format txt >> {summary_file_abs}",  # Use absolute path for echo command target
        f"echo -en '\n#+end_export' >> {summary_file_abs}",  # Use absolute path for echo command target
    ]

    for command in commands:
        _ = run_command(command, verbose=verbose)

    if verbose >= 1:
        print("Completed summary report")


def parse_hledger_roi_ascii(ascii_data: str, verbose: int = 0) -> pd.DataFrame | None:
    """Parse ASCII table output from 'hledger roi' based on the provided example."""
    lines = ascii_data.strip().split("\n")
    header: list[str] = []
    data: list[list[str]] = []
    header_found = False
    data_started = False

    try:
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Find header row (contains 'Begin', 'End', 'TWR/period')
            if (
                "Begin" in line
                and "End" in line
                and "TWR/period" in line
                and not header_found
            ):
                header = [h.strip() for h in line.strip("|").split("|")]
                header_found = True
                if verbose >= 2:
                    print(f"Found header: {header}")
                continue

            # Skip separator lines like +===... or +---...
            if line.startswith("+===") or line.startswith("+---"):
                if header_found and not data_started:
                    data_started = True  # Data rows start after the '===' separator
                    if verbose >= 2:
                        print("Found header separator, starting data collection.")
                elif data_started:
                    if verbose >= 2:
                        print("Found footer separator, stopping data collection.")
                    break  # Stop processing if we hit a footer separator after data started
                continue

            # Process data rows
            if data_started and line.startswith("|"):
                row_data = [d.strip() for d in line.strip("|").split("|")]
                # Ensure the number of columns matches the header
                if len(row_data) == len(header):
                    data.append(row_data)
                elif verbose >= 1:
                    print(
                        f"Warning: Skipping row due to column mismatch. Expected {len(header)}, got {len(row_data)}. Row: '{line}'",
                        file=sys.stderr,
                    )

        if not header or not data:
            if verbose >= 1:
                print(
                    "Error: Could not find header or data rows in hledger roi output.",
                    file=sys.stderr,
                )
                print("ASCII Data received:", file=sys.stderr)
                print(
                    ascii_data[:500] + "...", file=sys.stderr
                )  # Print first 500 chars
            return None

        df = pd.DataFrame(data, columns=header)

        # Select and rename relevant columns
        if "End" not in df.columns or "TWR/period" not in df.columns:
            raise KeyError(
                "Required columns 'End' or 'TWR/period' not found in parsed data."
            )

        df = df[["End", "TWR/period"]]
        df = df.rename(columns={"End": "date", "TWR/period": "twr_percent"})

        # Convert date column to datetime objects
        df["date"] = pd.to_datetime(df["date"])
        # Convert TWR percentage string to numeric factor (e.g., '5.5%' -> 1.055)
        # Handle potential non-numeric or empty strings before conversion
        df["twr_percent"] = df["twr_percent"].str.rstrip("%")
        df["twr_percent"] = pd.to_numeric(df["twr_percent"], errors="coerce")
        df = df.dropna(subset=["twr_percent"])  # Drop rows where conversion failed
        df["twr_factor"] = df["twr_percent"] / 100 + 1

        df = df.drop(columns=["twr_percent"])
        # Sort by date just in case
        df = df.sort_values(by="date").reset_index(drop=True)
        return df

    except (KeyError, ValueError, IndexError, Exception) as e:
        if verbose >= 1:
            print(f"Error parsing hledger roi ASCII data: {e}", file=sys.stderr)
            print("ASCII Data received:", file=sys.stderr)
            print(ascii_data[:500] + "...", file=sys.stderr)  # Print first 500 chars
        return None


def plot_yearly_twr_bars(
    ax: matplotlib.axes.Axes,
    df_portfolio: pd.DataFrame | None,
    dfs_benchmark: list[pd.DataFrame | None],
    benchmark_tickers: list[str],
    verbose: int = 0,
) -> matplotlib.axes.Axes:
    """Plot yearly TWR comparison as a bar chart on the given axes."""
    if verbose >= 1:
        print("Plotting yearly TWR bars...")

    def calculate_yearly_twr(df: pd.DataFrame | None, name: str) -> pd.Series | None:
        """Calculate yearly TWR percentage gain from monthly TWR factors."""
        if df is None or df.empty:
            if verbose >= 1:
                print(f"No data provided for {name} yearly TWR calculation.")
            return None
        try:
            df["date"] = pd.to_datetime(df["date"])
            df["year"] = df["date"].dt.year
            yearly_twr_factor = df.groupby("year")["twr_factor"].prod()
            yearly_gain_percent = (yearly_twr_factor - 1) * 100
            return yearly_gain_percent
        except Exception as e:
            if verbose >= 1:
                print(f"Error calculating yearly TWR for {name}: {e}", file=sys.stderr)
            return None

    combined_df = pd.DataFrame()
    portfolio_yearly = calculate_yearly_twr(df_portfolio, "Portfolio")
    if portfolio_yearly is not None:
        combined_df["Portfolio"] = portfolio_yearly

    valid_benchmarks_data = []
    for i, df_bm in enumerate(dfs_benchmark):
        if df_bm is not None and not df_bm.empty:
            ticker = benchmark_tickers[i]
            benchmark_yearly = calculate_yearly_twr(df_bm, f"Benchmark ({ticker})")
            if benchmark_yearly is not None:
                combined_df[f"Benchmark ({ticker})"] = benchmark_yearly
                valid_benchmarks_data.append(
                    {"ticker": ticker, "data": benchmark_yearly}
                )

    if combined_df.empty:
        ax.text(0.5, 0.5, "No yearly ROI data available", ha="center", va="center")
        ax.set_title("Yearly TWR (%)")
        if verbose >= 1:
            print("Skipping yearly TWR bar plot as no valid data was calculated.")
        return ax

    # --- Bar Plot ---
    years = combined_df.index.unique()  # Ensure unique years
    n_years = len(years)
    num_series = len(combined_df.columns)

    # Define a color palette for benchmarks beyond the first one
    # First benchmark uses lightgreen/salmon, subsequent ones cycle through this list
    benchmark_color_palette = [
        ("lightblue", "lightcoral"),
        ("lightgray", "darksalmon"),
        ("palegreen", "lightpink"),
    ]

    # Adjust bar width and positions for multiple series
    total_width_for_group = (
        0.8  # Total width allocated for all bars in a group (for one year)
    )
    bar_width = total_width_for_group / num_series if num_series > 0 else 0

    index = range(n_years)  # x-coordinates for the groups

    for i, col_name in enumerate(combined_df.columns):
        series_data = (
            combined_df[col_name].reindex(years).fillna(0)
        )  # Ensure all years are present
        # Calculate position for each bar in the group
        # Offset from the center of the group: (i - num_series / 2 + 0.5) * bar_width
        positions = [
            x - total_width_for_group / 2 + (i + 0.5) * bar_width for x in index
        ]

        gains = series_data.values

        if col_name == "Portfolio":
            colors = ["green" if g >= 0 else "red" for g in gains]
            label = "Portfolio"
        else:  # Benchmark
            # Extract ticker for consistent labeling
            ticker_match = re.search(r"Benchmark \((.*?)\)", col_name)
            ticker_label = ticker_match.group(1) if ticker_match else col_name

            # Determine which benchmark this is for color selection
            bm_index = -1
            for bm_idx, bm_data in enumerate(valid_benchmarks_data):
                if bm_data["ticker"] == ticker_label:
                    bm_index = bm_idx
                    break

            if bm_index == 0:  # First benchmark
                colors = ["lightgreen" if g >= 0 else "salmon" for g in gains]
            else:  # Subsequent benchmarks
                color_pair_idx = (bm_index - 1) % len(benchmark_color_palette)
                positive_color, negative_color = benchmark_color_palette[color_pair_idx]
                colors = [positive_color if g >= 0 else negative_color for g in gains]
            label = f"Benchmark ({ticker_label})"

        bars = ax.bar(
            positions,
            gains,
            bar_width,
            label=label,
            color=colors,
        )
        ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=8)

    # --- Final Figure Adjustments ---
    ax.set_ylabel("Yearly TWR (%)")
    ax.set_title("Yearly TWR Comparison")
    ax.set_xticks(index)  # Set tick positions

    # Create year labels, handling YTD for the last year if applicable
    year_labels = []
    current_year = datetime.datetime.now().year
    last_data_dates = (
        [df_portfolio["date"].max()]
        if df_portfolio is not None and not df_portfolio.empty
        else []
    )
    for df_bm in dfs_benchmark:
        if df_bm is not None and not df_bm.empty:
            last_data_dates.append(df_bm["date"].max())

    last_data_date = max(filter(None, last_data_dates), default=None)

    year_labels = []
    current_year = datetime.datetime.now().year
    # Ensure 'years' (from combined_df.index) is sorted if it's not already
    sorted_years = sorted(list(years))

    for year_val in sorted_years:  # Iterate over sorted unique years from data
        is_ytd = False
        if (
            last_data_date
            and year_val == current_year
            and (
                last_data_date.month < 12
                or (last_data_date.month == 12 and last_data_date.day < 31)
            )
        ):
            year_labels.append(f"{year_val}\n(YTD)")
        else:
            year_labels.append(str(year_val))

    ax.set_xticks(
        index
    )  # Set tick positions based on the original integer index for groups
    ax.set_xticklabels(year_labels)  # Apply the generated year labels
    ax.axhline(0, color="grey", linewidth=0.8, linestyle="--")  # Zero line
    ax.legend()

    return ax


def get_roi_data(
    ledger_file: str,
    data_dir: str,
    target_currency: str,
    conversion_args: list[str],
    benchmark_tickers: list[str],
    roi_investment_account: str = "investments",
    roi_pnl_account: str = "unrealized",
    roi_begin_date: str | None = None,
    verbose: int = 0,
) -> tuple[pd.DataFrame | None, list[pd.DataFrame | None]]:
    """Fetch and parse ROI data for portfolio and multiple benchmarks."""
    if verbose >= 1:
        print(f"Fetching ROI data, comparing with benchmarks: {benchmark_tickers}...")

    # --- Common hledger roi arguments ---
    base_roi_args = [
        "roi",
        "--investment",
        roi_investment_account,  # Use parameter
        "--profit-loss",
        roi_pnl_account,  # Use parameter
        "--value=then,$",
        "--monthly",
        "--infer-market-prices",
        "--end",
        "today",
    ]
    # Add begin date if specified
    if roi_begin_date:
        base_roi_args.extend(["--begin", roi_begin_date])

    conv_args_str = " ".join(conversion_args)  # For inserting into f-string commands

    # --- 1. Portfolio ROI ---
    portfolio_roi_ascii: str | None = None
    df_portfolio: pd.DataFrame | None = None
    try:
        # Find all .ledger files in data_dir (commodity prices)
        data_files_args = []
        if os.path.isdir(data_dir):
            data_files_args = [
                f"-f {os.path.join(data_dir, f)}"
                for f in os.listdir(data_dir)
                if f.endswith(".ledger")
            ]
        else:
            if verbose >= 1:
                print(
                    f"Warning: Data directory '{data_dir}' not found. Commodity prices might be missing.",
                    file=sys.stderr,
                )

        portfolio_command = f"hledger -f {ledger_file} {' '.join(data_files_args)} {conv_args_str} {' '.join(base_roi_args)}"
        portfolio_roi_ascii = run_command(portfolio_command, verbose=verbose)

        df_portfolio = parse_hledger_roi_ascii(portfolio_roi_ascii, verbose)
    except (
        subprocess.CalledProcessError,
        IOError,
    ) as e:
        if verbose >= 1:
            print(f"Error getting or parsing portfolio ROI: {e}", file=sys.stderr)

    # --- 2. Benchmarks ROI ---
    dfs_benchmark: list[pd.DataFrame | None] = []
    first_trans_date: datetime.date | None = None

    # Get ledger start date once, used for all benchmarks
    try:
        stats_output = run_command(f"hledger -f {ledger_file} stats", verbose=verbose)
        first_trans_date_str = None
        for line in stats_output.splitlines():
            if line.strip().startswith("Transactions span") or line.strip().startswith(
                "Date range"
            ):
                date_part = line.split(":", 1)[1].strip()
                first_trans_date_str = date_part.split(" to ")[0].strip()
                break
        if not first_trans_date_str:
            raise ValueError(
                "Could not parse first transaction date from hledger stats."
            )
        if verbose >= 2:
            print(f"Extracted first transaction date string: {first_trans_date_str}")
        first_trans_date = datetime.datetime.strptime(
            first_trans_date_str, "%Y-%m-%d"
        ).date()
    except (subprocess.CalledProcessError, ValueError) as e:
        if verbose >= 1:
            print(
                f"Warning: Could not determine ledger start date: {e}", file=sys.stderr
            )
        # If we can't get first_trans_date, we can't proceed with benchmark ROI that needs initial price
        # So, append None for all benchmarks and return
        for _ in benchmark_tickers:
            dfs_benchmark.append(None)
        return df_portfolio, dfs_benchmark

    for benchmark_ticker in benchmark_tickers:
        df_single_benchmark: pd.DataFrame | None = None
        benchmark_data_file = os.path.join(data_dir, f"{benchmark_ticker}.ledger")

        if not os.path.exists(benchmark_data_file):
            if verbose >= 1:
                print(
                    f"Benchmark data file not found: {benchmark_data_file}",
                    file=sys.stderr,
                )
                print(
                    f"Attempting to generate it using: python -m investory.values --commodity {benchmark_ticker} --output-dir {data_dir}",
                    file=sys.stderr,
                )
            try:
                generation_command = [
                    sys.executable,
                    "-m",
                    "investory.values",
                    "--commodity",
                    benchmark_ticker,
                    "--output-dir",
                    data_dir,
                ]
                if (
                    verbose >= 2
                ):  # Pass verbosity to submodule if it supports it, or just for logging here
                    print(f"Running command: {' '.join(generation_command)}")

                process = subprocess.run(
                    generation_command,
                    capture_output=True,
                    text=True,
                    check=False,
                    encoding="utf-8",
                )
                if process.returncode != 0:
                    print(
                        f"Error generating benchmark data for {benchmark_ticker}:",
                        file=sys.stderr,
                    )
                    if process.stdout:
                        print(f"Stdout: {process.stdout.strip()}", file=sys.stderr)
                    if process.stderr:
                        print(f"Stderr: {process.stderr.strip()}", file=sys.stderr)
                elif verbose >= 1:
                    print(
                        f"Successfully generated/updated data for {benchmark_ticker} in {data_dir}"
                    )
            except Exception as e:
                print(
                    f"Failed to execute investory.values for {benchmark_ticker}: {e}",
                    file=sys.stderr,
                )

        if os.path.exists(benchmark_data_file):
            try:
                # Fetch benchmark price around the start date (first_trans_date is already fetched)
                ticker = yq.Ticker(benchmark_ticker)
                hist = ticker.history(
                    start=first_trans_date - datetime.timedelta(days=5),
                    end=first_trans_date + datetime.timedelta(days=1),
                )
                if hist.empty:
                    raise ValueError(
                        f"Could not fetch initial price for benchmark {benchmark_ticker} around {first_trans_date}"
                    )
                initial_price = hist["open"].iloc[0]
                initial_price_date = hist.index.get_level_values("date")[0]

                temp_benchmark_ledger = f"{initial_price_date.strftime('%Y-%m-%d')} * Buy 1 {benchmark_ticker}\n    assets:investments:INDEX  1 {benchmark_ticker} @ {target_currency}{initial_price:.2f}\n    assets:cash\n"
                benchmark_command = (
                    f"hledger -f - -f {benchmark_data_file} {' '.join(base_roi_args)}"
                )
                benchmark_roi_ascii = run_command(
                    benchmark_command, stdin_data=temp_benchmark_ledger, verbose=verbose
                )
                df_single_benchmark = parse_hledger_roi_ascii(
                    benchmark_roi_ascii, verbose
                )
            except (
                subprocess.CalledProcessError,
                IOError,
                ValueError,
                # yq.exceptions.YahooQueryError,
            ) as e:
                if verbose >= 1:
                    print(
                        f"Error getting or parsing benchmark ROI for {benchmark_ticker}: {e}",
                        file=sys.stderr,
                    )
                df_single_benchmark = None
        else:
            if verbose >= 1:
                print(
                    f"Benchmark data file still not found for {benchmark_ticker} after generation attempt.",
                    file=sys.stderr,
                )
            df_single_benchmark = None

        dfs_benchmark.append(df_single_benchmark)

    return df_portfolio, dfs_benchmark


def plot_roi_comparison(
    ax: matplotlib.axes.Axes,
    df_portfolio: pd.DataFrame | None,
    dfs_benchmark: list[pd.DataFrame | None],
    benchmark_tickers: list[str],
    verbose: int = 0,
) -> matplotlib.axes.Axes:
    """Plot cumulative TWR comparison line chart on the given axes."""
    if verbose >= 1:
        print("Plotting cumulative TWR comparison lines...")

    ax_line = ax

    # Define a color palette for benchmarks
    benchmark_colors = plt.cm.get_cmap(
        "viridis", len(benchmark_tickers) if benchmark_tickers else 1
    )

    has_data = False
    if df_portfolio is not None and not df_portfolio.empty:
        has_data = True
        portfolio_label = "Portfolio Cumulative TWR"
        portfolio_cum_twr = df_portfolio["twr_factor"].cumprod()
        ax_line.plot(
            df_portfolio["date"],
            portfolio_cum_twr,
            label=portfolio_label,
            linewidth=1.5,  # Slightly thicker for portfolio
            color="black",
        )
        if not portfolio_cum_twr.empty:
            final_twr_factor = portfolio_cum_twr.iloc[-1]
            total_gain_percent = (final_twr_factor - 1) * 100
            last_date = df_portfolio["date"].iloc[-1]
            text_y_position = final_twr_factor * 1.02
            ax_line.text(
                last_date,
                text_y_position,
                f"Total: {total_gain_percent:+.1f}%",
                fontsize=9,
                color="black",
                ha="right",
                va="bottom",
            )

    for i, df_bm in enumerate(dfs_benchmark):
        if df_bm is not None and not df_bm.empty:
            has_data = True
            ticker = benchmark_tickers[i]
            benchmark_label = f"Benchmark ({ticker}) Cumulative TWR"
            benchmark_cum_twr = df_bm["twr_factor"].cumprod()
            ax_line.plot(
                df_bm["date"],
                benchmark_cum_twr,
                label=benchmark_label,
                color=(
                    benchmark_colors(i / len(benchmark_tickers))
                    if len(benchmark_tickers) > 0
                    else "orange"
                ),  # Cycle through colormap
                linewidth=1,
            )

    if has_data:
        ax_line.set_xlabel("Date")
        ax_line.set_ylabel("Cumulative TWR (Factor)")
        ax_line.tick_params(axis="y")  # Use default color

        # --- Final Figure Adjustments ---
        ax_line.set_title("Cumulative Performance")  # More specific title
        ax_line.legend()  # Add legend directly here

    elif verbose >= 1:
        ax_line.text(0.5, 0.5, "No ROI data available", ha="center", va="center")
        print("Skipping cumulative ROI line plot as no valid data was parsed.")

    return ax_line  # Return the axes


def generate_roi_report(  # Keep the old function signature for now, but it will just call the new ones
    ledger_file: str,
    data_dir: str,
    target_currency: str,
    conversion_args: list[str],
    output_dir: str,
    benchmark_tickers: list[str],  # Changed
    roi_investment_account: str = "investments",  # Added for consistency with get_roi_data
    roi_pnl_account: str = "unrealized",  # Added
    roi_begin_date: str | None = None,  # Added
    verbose: int = 0,
):
    """Generate standalone ROI comparison plot (Deprecated, use generate_combined_figure)."""
    if verbose >= 1:
        print(
            "Warning: generate_roi_report is deprecated. Use generate_combined_figure for integrated plots."
        )

    # 1. Get Data
    df_portfolio, dfs_benchmark = get_roi_data(
        ledger_file,
        data_dir,
        target_currency,
        conversion_args,
        benchmark_tickers,  # Pass list
        roi_investment_account,
        roi_pnl_account,
        roi_begin_date,
        verbose=verbose,  # Ensure verbose is passed correctly
    )

    # 2. Plot Data (if any exists)
    # Check if there's any portfolio data or any valid benchmark data
    has_any_benchmark_data = any(
        df is not None and not df.empty for df in dfs_benchmark
    )
    if (df_portfolio is not None and not df_portfolio.empty) or has_any_benchmark_data:
        fig, (ax_line, ax_bar) = plt.subplots(2, 1, figsize=(9, 7), sharex=False)

        plot_roi_comparison(
            ax_line, df_portfolio, dfs_benchmark, benchmark_tickers, verbose
        )

        plot_yearly_twr_bars(
            ax_bar, df_portfolio, dfs_benchmark, benchmark_tickers, verbose
        )

        # --- Final Figure Adjustments for Standalone Plot ---
        fig.suptitle(
            "Portfolio vs Benchmark Performance", fontsize=12, y=0.98
        )  # Add overall title
        # Adjust layout to prevent overlap
        fig.tight_layout(rect=[0, 0, 1, 0.96])  # Adjust rect to make space for suptitle

        # Save the standalone figure
        plot_file = os.path.join(
            output_dir, "roi-comparison-standalone.svg"
        )  # New name
        os.makedirs(output_dir, exist_ok=True)  # Ensure dir exists
        fig.savefig(plot_file, bbox_inches="tight", transparent=True)
        if verbose >= 1:
            print(f"Standalone ROI comparison plot saved to {plot_file}")
        plt.close(fig)  # Close the figure to free memory
    elif verbose >= 1:
        print("Skipping standalone ROI plot generation as no valid data was parsed.")

    if verbose >= 1:
        print("Finished standalone ROI report generation process.")


def get_ledger_years(ledger_file: str, verbose: int = 0) -> list[int]:
    """Extract the years covered by transactions in the ledger."""
    try:
        stats_output: str = run_command(
            f"hledger -f {ledger_file} stats", verbose=verbose
        )
    except subprocess.CalledProcessError:
        if verbose >= 1:
            print(
                f"Warning: Could not run 'hledger stats' on {ledger_file}. Cannot determine years.",
                file=sys.stderr,
            )
        return []  # Return empty list if stats fails

    for line in stats_output.split("\n"):
        # Handle different hledger versions/outputs for date span
        if line.startswith("Transactions span") or line.startswith("Date range"):
            span = line.split(":")[1].strip()
            start_year, end_year = map(
                lambda x: int(x.split("-")[0]), span.split(" to ")
            )
            return list(range(start_year, end_year + 1))
    return []  # Return an empty list if no span is found


def generate_combined_figure(
    ledger_file: str,
    data_dir: str,
    target_currency: str,
    conversion_args: list[str],
    output_dir: str,
    benchmark_tickers: list[str],  # Changed
    roi_investment_account: str = "investments",
    roi_pnl_account: str = "unrealized",
    roi_begin_date: str | None = None,
    verbose: int = 0,
):
    """Generate a combined figure with Asset Distribution, Evolution, and ROI."""
    if verbose >= 1:
        print("Generating combined overview figure...")

    # --- Create Figure and Subplots ---
    # Use constrained_layout for better automatic spacing
    # Adjust figsize for a potentially wider layout
    fig = plt.figure(figsize=(12, 8), constrained_layout=True)

    # Define a 3-row, 2-column grid
    # Row 0: Asset Evolution (spans 2 cols)
    # Row 1: Asset Distribution (col 0), Yearly TWR (col 1)
    # Row 2: Cumulative TWR (spans 2 cols)
    # Adjust height/width ratios for desired emphasis
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1], width_ratios=[1, 1.2])

    ax_evol = fig.add_subplot(gs[0, :])  # Top row, spans both columns
    ax_dist = fig.add_subplot(gs[1, 0])  # Middle row, left column
    ax_roi_bars = fig.add_subplot(gs[1, 1])  # Middle row, right column
    ax_roi_line = fig.add_subplot(gs[2, :])  # Bottom row, spans both columns

    # --- Plot Asset Distribution (Middle-Left) ---
    try:
        # Use period=0 for overall distribution
        generate_asset_distribution_graph(
            0, ledger_file, target_currency, conversion_args, ax=ax_dist
        )
    except Exception as e:
        print(f"Error generating asset distribution plot: {e}", file=sys.stderr)
        ax_dist.text(
            0.5, 0.5, "Error generating plot", ha="center", va="center", color="red"
        )
        ax_dist.set_title("Asset Distribution")  # Still add title

    # --- Plot Asset Evolution (Top-Right) ---
    try:
        # Use period=0 for overall evolution
        generate_asset_evolution_graph(
            0,
            ledger_file,
            target_currency,
            conversion_args,
            data_dir,
            ax=ax_evol,
            verbose=verbose,  # Pass data_dir and verbose
        )
    except Exception as e:
        print(f"Error generating asset evolution plot: {e}", file=sys.stderr)
        ax_evol.text(
            0.5, 0.5, "Error generating plot", ha="center", va="center", color="red"
        )
        ax_evol.set_title("Asset Evolution")  # Still add title

    # --- Get ROI Data (needed for both bottom plots) ---
    df_portfolio, dfs_benchmark = None, []  # Initialize
    try:
        df_portfolio, dfs_benchmark = get_roi_data(
            ledger_file,
            data_dir,
            target_currency,
            conversion_args,
            benchmark_tickers,  # Pass list
            roi_investment_account,
            roi_pnl_account,
            roi_begin_date,
            verbose,
        )
    except Exception as e:
        print(f"Error getting ROI data: {e}", file=sys.stderr)
        # Add error text to both ROI plots if data fetching fails
        ax_roi_line.text(
            0.5, 0.5, "Error getting ROI data", ha="center", va="center", color="red"
        )
        ax_roi_bars.text(
            0.5, 0.5, "Error getting ROI data", ha="center", va="center", color="red"
        )
        ax_roi_line.set_title("Cumulative Performance")
        ax_roi_bars.set_title("Yearly TWR Comparison")

    # --- Plot Cumulative ROI Comparison (Bottom-Left) ---
    try:
        plot_roi_comparison(
            ax_roi_line, df_portfolio, dfs_benchmark, benchmark_tickers, verbose
        )
    except Exception as e:
        print(f"Error generating cumulative ROI line plot: {e}", file=sys.stderr)
        ax_roi_line.text(
            0.5, 0.5, "Error generating plot", ha="center", va="center", color="red"
        )
        ax_roi_line.set_title("Cumulative Performance")  # Still add title

    # --- Plot Yearly TWR Bars (Bottom-Right) ---
    try:
        plot_yearly_twr_bars(
            ax_roi_bars, df_portfolio, dfs_benchmark, benchmark_tickers, verbose
        )
    except Exception as e:
        print(f"Error generating yearly TWR bar plot: {e}", file=sys.stderr)
        ax_roi_bars.text(
            0.5, 0.5, "Error generating plot", ha="center", va="center", color="red"
        )
        ax_roi_bars.set_title("Yearly TWR Comparison")  # Still add title

    # --- Final Figure Adjustments ---
    fig.suptitle("Portfolio Overview", fontsize=14)  # Slightly larger title

    # No automatic combined legend needed as each plot has its own now.
    # constrained_layout should handle spacing.

    # --- Save Figure ---
    plot_file = os.path.join(output_dir, "combined-overview.svg")
    os.makedirs(output_dir, exist_ok=True)  # Ensure dir exists
    try:
        fig.savefig(plot_file, bbox_inches="tight", transparent=True)
        if verbose >= 1:
            print(f"Combined overview figure saved to {plot_file}")
    except Exception as e:
        print(f"Error saving combined figure: {e}", file=sys.stderr)
    finally:
        plt.close(fig)  # Close the figure


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate reports based on hledger data."  # Updated description slightly
    )

    _ = parser.add_argument(
        "--ledger",
        help="Main ledger file",
        required=False,
        type=str,
        default="all.ledger",
    )
    _ = parser.add_argument(
        "--currency",
        help="Target currency symbol for valuation (e.g., €, $, R$)",
        required=False,
        type=str,
        default="€",  # Updated help, default is €
    )
    _ = parser.add_argument(
        "--data-dir",
        help="Directory containing commodity price (.ledger) files",
        required=False,
        type=str,
        default="./data",  # Default to ./data
    )
    _ = parser.add_argument(
        "--output-dir",
        help="Directory to save generated reports",
        required=False,
        type=str,
        default="./reports",  # Default to ./reports
    )
    _ = parser.add_argument(
        "--benchmark-ticker",
        help="Yahoo Finance ticker(s) for ROI benchmark (e.g., ^spx ^bvsp). Used in combined figure.",
        required=False,
        type=str,
        nargs="+",  # Accept one or more tickers
        default=["^spx"],  # Default to S&P 500 as a list
    )
    _ = parser.add_argument(
        "--roi-investment-account",
        help="Account name pattern for investments in ROI calculation",
        required=False,
        type=str,
        default="assets:investments",  # Default matches hledger's usual convention
    )
    _ = parser.add_argument(
        "--roi-pnl-account",
        help="Account name pattern for profit/loss in ROI calculation",
        required=False,
        type=str,
        default="income:financial",  # Default matches hledger's usual convention for unrealized gains
    )
    _ = parser.add_argument(
        "--roi-begin-date",
        help="Start date for ROI calculation (YYYY-MM-DD). Defaults to ledger start.",
        required=False,
        type=str,
        default=None,
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        help="Set output verbosity level (0=silent, 1=normal, 2=detailed).",
        type=int,
        default=0,
        choices=[0, 1, 2],
    )
    args: argparse.Namespace = parser.parse_args()

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # Detect all currencies in the main ledger
    all_currencies = get_ledger_currencies(args.ledger, verbose=args.verbose)
    if args.verbose >= 1:
        print(f"Detected currencies: {all_currencies}")

    # Identify currencies that need conversion to the target currency
    other_currencies = all_currencies - {args.currency}
    if args.verbose >= 1:
        print(f"Target currency: {args.currency}")
        print(f"Other currencies requiring conversion checks: {other_currencies}")

    # Find the necessary conversion files
    conversion_args = find_conversion_files(
        args.currency,
        other_currencies,
        args.data_dir,
        CURRENCY_SYMBOL_TO_CODE,
        verbose=args.verbose,
    )
    print(f"Conversion file arguments: {conversion_args}")

    # --- Generate Text Reports (Yearly and Summary) ---
    text_report_tasks: list[tuple] = []
    periods: list[int] = get_ledger_years(args.ledger, verbose=args.verbose)
    for period in periods:
        text_report_tasks.append(
            (
                generate_yearly_report,
                period,
                args.ledger,
                args.currency,
                conversion_args,
                args.data_dir,  # Pass data_dir
                args.verbose,
            )
        )
    text_report_tasks.append(
        (
            generate_summary_report,
            args.ledger,
            args.currency,
            conversion_args,
            args.output_dir,  # Pass output_dir
            args.verbose,
        )
    )

    # Execute text report tasks in parallel
    if args.verbose >= 1:
        print("Starting parallel generation of text reports...")
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures: list[Future[None]] = [
            executor.submit(task_func, *task_args)
            for task_func, *task_args in text_report_tasks
        ]
        # Wait for text reports to complete
        for future in as_completed(futures):
            try:
                future.result()  # Check for exceptions
            except Exception as exc:
                print(f"Text report generation task raised an exception: {exc}")
    if args.verbose >= 1:
        print("Finished text report generation.")

    # --- Generate Combined Figure (after text reports) ---
    # This runs sequentially after the parallel tasks above.
    # Could potentially be parallelized too, but might contend for CPU/memory with plotting.
    generate_combined_figure(
        ledger_file=args.ledger,
        data_dir=args.data_dir,
        target_currency=args.currency,
        conversion_args=conversion_args,
        output_dir=args.output_dir,
        benchmark_tickers=args.benchmark_ticker,  # Pass list of tickers
        roi_investment_account=args.roi_investment_account,
        roi_pnl_account=args.roi_pnl_account,
        roi_begin_date=args.roi_begin_date,
        verbose=args.verbose,
    )

    if args.verbose >= 1:
        print("All report generation finished.")

# Local Variables:
# jinx-local-words: "bs bs-"
# End:
