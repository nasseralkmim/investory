"""Generate reports with hledger."""

# ruff: noqa: E501
import datetime
import io
import logging
import os
import re
import subprocess
import sys
from concurrent.futures import Future, ProcessPoolExecutor, as_completed

import matplotlib.axes
import matplotlib.pyplot as plt
import pandas as pd
import plotext as plt_text

from . import prices, roi

logger = logging.getLogger(__name__)


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
            f"hledger -f {ledger_file} commodities", verbose=verbose
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


def _parse_hledger_price_amount(amount_str: str) -> tuple[float, str]:
    """
    Parses an hledger price amount string (e.g., "$123.45", "123.45 USD")
    into a float value and currency string.
    """
    # Normalize by removing commas used as thousands separators
    amount_str = amount_str.replace(",", "")

    # Case 1: Currency symbol prefix, e.g., $123.45, €123.45
    # Regex: currency symbols (common ones), optional whitespace, optional sign, value
    match_prefix = re.match(r"([$€£¥R]+)\s*(-?[\d\.]+)", amount_str, re.UNICODE)
    if match_prefix:
        currency = match_prefix.group(1).strip()
        value_str = match_prefix.group(2)
        return float(value_str), currency

    # Case 2: Currency code suffix, e.g., 123.45 USD, -10 EUR
    # Regex: optional sign, value, optional whitespace, 3-letter currency code
    match_suffix_code = re.match(r"(-?[\d\.]+)\s*([A-Z]{3})", amount_str)
    if match_suffix_code:
        value_str = match_suffix_code.group(1)
        currency = match_suffix_code.group(2).strip()
        return float(value_str), currency

    # Case 3: Currency symbol suffix (less common but possible), e.g., 123.45$, 10€
    # Regex: optional sign, value, optional whitespace, currency symbols
    match_suffix_symbol = re.match(r"(-?[\d\.]+)\s*([$€£¥R]+)", amount_str, re.UNICODE)
    if match_suffix_symbol:
        value_str = match_suffix_symbol.group(1)
        currency = match_suffix_symbol.group(2).strip()
        return float(value_str), currency

    raise ValueError(f"Could not parse hledger price amount string: '{amount_str}'")


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
        if line.startswith("Transactions span") or line.startswith("Date range") or line.startswith("Txns span"):
            span = line.split(":")[1].strip()
            # Extract the date range, ignoring the days count in parentheses
            date_range = span.split("(")[0].strip() if "(" in span else span
            parts = date_range.split(" to ")
            if len(parts) == 2:
                try:
                    start_year = int(parts[0].split("-")[0])
                    end_year = int(parts[1].split("-")[0])
                    return list(range(start_year, end_year + 1))
                except (ValueError, IndexError):
                    if verbose >= 1:
                        print(
                            f"Warning: Could not parse date range: {span}",
                            file=sys.stderr,
                        )
                    return []
    return []  # Return an empty list if no span is found


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

    # Also remove any other commodity symbols that might be present
    df = df.replace(r"\s*[A-Z€$₹£¥]+\s*$", "", regex=True)

    df["balance"] = pd.to_numeric(df["balance"], errors="coerce")

    # Avoid problems with negative values and filter out zero balances
    negative_filter: pd.Series = df["balance"] < 0
    negative_df: pd.DataFrame = df[negative_filter].copy()
    df_positive: pd.DataFrame = df[df["balance"] > 0]

    account_to_color = get_account_colors(ledger)
    colors: list[str] = [
        account_to_color[account] for account in df_positive["account"]
    ]

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
        # Plot pie chart with percentage labels on slices
        wedges, _, autotexts = ax.pie(
            df_positive["balance"],
            colors=colors,
            autopct="%1.1f%%",  # Add percentage labels
            startangle=90,  # Optional: Adjust start angle
            counterclock=False,  # Optional: Adjust direction
        )
        # Improve label appearance (optional, but good practice)
        for autotext in autotexts:
            autotext.set_color("white")  # Set text color for better contrast if needed
            autotext.set_fontsize(8)  # Adjust font size

        _ = ax.set_title("Asset Distribution")

        # Add legend to the side
        # ax.legend(
        #     wedges,
        #     df_positive["account"],
        #     title="Accounts",
        #     loc="center left",
        #     bbox_to_anchor=(1, 0, 0.5, 1),  # Position legend outside plot area
        #     fontsize="small",  # Adjust font size if needed
        # )

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
        "--transpose",  # Transpose so dates are in rows (index) not columns
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
    df_evo: pd.DataFrame = pd.read_csv(csv_data, index_col=0)

    if verbose >= 2:
        logger.info(f"Raw CSV data:\n{output[:500]}")  # Log first 500 chars

    # Update the currency symbol replacement
    # The replace operation can return Series or None, causing type issues. Ignore for now.
    df_evo = df_evo.replace(
        re.escape(target_currency) + r"\s*", "", regex=True
    )  # Use target_currency and escape it

    # Also remove any other commodity symbols that might be present (e.g., "BTC", "ETH", etc.)
    # This handles cases where conversion to target currency failed
    df_evo = df_evo.replace(r"\s*[A-Z€$₹£¥]+\s*$", "", regex=True)

    if verbose >= 2:
        logger.info(f"After currency replacement:\n{df_evo.head()}")

    df_evo = df_evo[df_evo.columns].apply(pd.to_numeric, errors="coerce")
    # convert index (dates) to datetime
    df_evo.index = pd.to_datetime(df_evo.index, format="%Y-%m")

    account_to_color = get_account_colors(ledger)

    # Avoid problems with negative balances in the plot.
    # Replace negative values with np.NaN
    df_evo = df_evo.where(df_evo >= 0)

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

    ax.legend(
        title="Accounts",
        fontsize="small",
    )
    return ax  # Return the axes


def generate_text_plots(
    ledger_file: str,
    data_dir: str,
    target_currency: str,
    conversion_args: list[str],
    benchmark_tickers: list[str],
    roi_investment_account: str = "investments",
    roi_pnl_account: str = "unrealized",
    roi_begin_date: str | None = None,
    verbose: int = 0,
    enable_roi_plots: bool = True,
    ticker_map: dict[str, str] | None = None,
    currency_map: dict[str, str] | None = None,
    output_dir: str | None = None,
):
    """Generate text-based plots using plotext and add them to summary.org."""
    logger.info("Generating text-based plots for summary.org...")

    # Setup output file path if output_dir is provided
    output_file_path = None
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.join(output_dir, "summary.org")

    def write_section(title: str, content: str = "", is_first: bool = False):
        """Write a section header and optional content to file or stdout."""
        # Org-mode heading - skip leading newline for first section
        if is_first:
            section_text = f"* {title}\n"
        else:
            section_text = f"\n* {title}\n"
        if content:
            section_text += content + "\n"

        if output_file_path:
            with open(output_file_path, "a", encoding="utf-8") as f:
                f.write(section_text)
        else:
            print(section_text, end="")

    def show_plot():
        """Show or save the plotext figure."""
        if output_file_path:
            plot_content = plt_text.build()
            # Remove ANSI escape codes for clean file output
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            plot_content_no_ansi = ansi_escape.sub("", plot_content)
            org_block = f"\n{plot_content_no_ansi}\n"
            with open(output_file_path, "a", encoding="utf-8") as f:
                f.write(org_block)
        else:
            plt_text.show()
        plt_text.clear_figure()

    # --- Ensure Price Data is Available ---
    logger.info("Ensuring price data is available...")
    try:
        price_files = prices.ensure_price_data(
            ledger_file,
            data_dir,
            ticker_map=ticker_map,
            currency_map=currency_map,
            target_currency=target_currency,
            only_nonzero_balance=True,
        )
        if verbose >= 1 and price_files:
            logger.info(f"Price data cached in: {', '.join(price_files)}")
    except Exception as e:
        logger.warning(f"Could not ensure price data: {e}")
        price_files = []

    # --- Plot Asset Distribution ---
    write_section("Asset Distribution", is_first=True)
    try:
        command: list[str] = [
            "hledger",
            "-f",
            ledger_file,
            *conversion_args,
            "bal",
            "acct:^assets:investments",
            "--drop",
            "2",
            "--depth",
            "3",
            f"--value=end,{target_currency}",
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
        df: pd.DataFrame = pd.read_csv(csv_data)

        df = df.replace(re.escape(target_currency) + r"\s*", "", regex=True)
        df = df.replace(r"\s*[A-Z€$₹£¥]+\s*$", "", regex=True)
        df["balance"] = pd.to_numeric(df["balance"], errors="coerce")

        df_positive: pd.DataFrame = df[df["balance"] > 0]

        if not df_positive.empty:
            # Calculate percentages
            total = df_positive["balance"].sum()
            percentages = (df_positive["balance"] / total * 100).round(1)
            
            # Create labels with percentages
            labels = [
                f"{account} ({pct}%)"
                for account, pct in zip(df_positive["account"].tolist(), percentages.tolist())
            ]
            
            plt_text.simple_bar(
                labels,
                df_positive["balance"].tolist(),
                width=50,
                title=f"Asset Distribution ({target_currency})",
            )
            show_plot()
        else:
            write_section("", "No data available for asset distribution")
    except Exception as e:
        logger.error(f"Error generating text asset distribution: {e}")
        write_section("", f"Error: {e}")

    # --- Plot Asset Evolution and Performance Side by Side ---
    write_section("Asset Evolution and Performance")
    
    # Generate Asset Evolution plot
    evolution_plot = None
    try:
        data_files_args: list[str] = []
        if os.path.isdir(data_dir):
            for f in os.listdir(data_dir):
                if f.endswith(".ledger"):
                    data_files_args.extend(["-f", os.path.join(data_dir, f)])

        command: list[str] = [
            "hledger",
            "-f",
            ledger_file,
            *data_files_args,
            *conversion_args,
            "bal",
            "acct:^assets:investments",
            "--historical",
            "--monthly",
            "--drop",
            "2",
            "--depth",
            "3",
            f"--value=end,{target_currency}",
            "--no-total",
            "--infer-market-prices",
            "-O",
            "csv",
            "--transpose",
        ]

        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, shell=False, universal_newlines=True
        )
        output, _ = process.communicate()
        csv_data = io.StringIO(output)
        df_evo: pd.DataFrame = pd.read_csv(csv_data, index_col=0)

        df_evo = df_evo.replace(re.escape(target_currency) + r"\s*", "", regex=True)
        df_evo = df_evo.replace(r"\s*[A-Z€$₹£¥]+\s*$", "", regex=True)
        df_evo = df_evo[df_evo.columns].apply(pd.to_numeric, errors="coerce")
        df_evo.index = pd.to_datetime(df_evo.index, format="%Y-%m")
        df_evo = df_evo.where(df_evo >= 0)

        if not df_evo.empty:
            plt_text.clear_figure()
            plt_text.date_form("Y-m")
            plt_text.plotsize(50, 15)

            total_value = df_evo.sum(axis=1)
            dates = [d.strftime("%Y-%m") for d in df_evo.index]

            plt_text.plot(dates, total_value.tolist(), label="Total Portfolio")
            plt_text.title(f"Asset Evolution ({target_currency})")
            plt_text.xlabel("Date")
            plt_text.ylabel(f"Value ({target_currency})")
            
            evolution_plot = plt_text.build()
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            evolution_plot = ansi_escape.sub("", evolution_plot)
            plt_text.clear_figure()
    except Exception as e:
        logger.error(f"Error generating text asset evolution: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        evolution_plot = f"Error: {e}"

    # --- Plot ROI if enabled ---
    performance_plot = None
    if enable_roi_plots:
        try:
            df_portfolio, benchmark_series = roi.get_roi_data(
                ledger_file=ledger_file,
                data_dir=data_dir,
                conversion_args=conversion_args,
                benchmark_tickers=benchmark_tickers,
                investment_account=roi_investment_account,
                pnl_account=roi_pnl_account,
                begin_date=roi_begin_date,
                currency=target_currency,
                price_files=price_files,
            )

            if df_portfolio is not None:
                if "date" in df_portfolio.columns:
                    df_portfolio["date"] = pd.to_datetime(df_portfolio["date"])
                    df_portfolio["year"] = df_portfolio["date"].dt.year
                else:
                    df_portfolio.index = pd.to_datetime(df_portfolio.index)
                    df_portfolio["year"] = df_portfolio.index.year

                yearly_returns = df_portfolio.groupby("year")["twr_factor"].apply(
                    lambda x: x.prod() - 1
                )

                years = yearly_returns.index.tolist()
                returns_pct = (yearly_returns * 100).tolist()

                plt_text.clear_figure()
                plt_text.date_form("")
                plt_text.plotsize(50, 15)

                plt_text.plot(
                    years,
                    returns_pct,
                    label="Portfolio",
                    marker="hd",
                    color="green+",
                    style="bold",
                )

                if benchmark_series:
                    benchmark_colors = ["cyan+", "magenta+", "yellow+", "blue+"]
                    benchmark_markers = ["braille", "braille", "braille", "braille"]

                    for i, bench_data in enumerate(benchmark_series):
                        if bench_data is not None and not bench_data.empty:
                            bench_name = (
                                benchmark_tickers[i]
                                if i < len(benchmark_tickers)
                                else f"Benchmark {i}"
                            )
                            bench_years = bench_data.index.tolist()
                            bench_returns_pct = bench_data.tolist()

                            bench_color = benchmark_colors[i % len(benchmark_colors)]
                            bench_marker = benchmark_markers[i % len(benchmark_markers)]

                            plt_text.plot(
                                bench_years,
                                bench_returns_pct,
                                label=bench_name,
                                marker=bench_marker,
                                color=bench_color,
                            )

                plt_text.title("Portfolio vs Benchmark Yearly TWR (%)")
                plt_text.xlabel("Year")
                plt_text.ylabel("Return (%)")
                plt_text.hline(0, color="gray")

                performance_plot = plt_text.build()
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                performance_plot = ansi_escape.sub("", performance_plot)
                plt_text.clear_figure()
        except Exception as e:
            logger.error(f"Error generating text ROI plots: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            performance_plot = f"Error: {e}"

    # Combine plots side by side
    if evolution_plot and performance_plot:
        evo_lines = evolution_plot.split("\n")
        perf_lines = performance_plot.split("\n")
        
        max_lines = max(len(evo_lines), len(perf_lines))
        evo_lines.extend([""] * (max_lines - len(evo_lines)))
        perf_lines.extend([""] * (max_lines - len(perf_lines)))
        
        combined = []
        for evo_line, perf_line in zip(evo_lines, perf_lines):
            # Pad evolution line to 52 chars (50 + 2 spacing)
            padded_evo = evo_line.ljust(52)
            combined.append(padded_evo + perf_line)
        
        combined_output = "\n".join(combined) + "\n"
        
        if output_file_path:
            with open(output_file_path, "a", encoding="utf-8") as f:
                f.write(combined_output)
        else:
            print(combined_output)
    elif evolution_plot:
        if output_file_path:
            with open(output_file_path, "a", encoding="utf-8") as f:
                f.write(evolution_plot + "\n")
        else:
            print(evolution_plot)
    elif performance_plot:
        if output_file_path:
            with open(output_file_path, "a", encoding="utf-8") as f:
                f.write(performance_plot + "\n")
        else:
            print(performance_plot)

    if output_file_path:
        logger.info(f"Summary saved to {output_file_path}")

    logger.info("Finished text-based plot generation.")


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
    data_dir: str,
    output_dir: str,
    verbose: int = 0,
):
    """Generate a yearly report with plots and tax information."""
    # Create single .org file for the year
    report_file = os.path.join(output_dir, f"{period}.org")
    
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

    # Prepare conversion args string
    conv_args_str = " ".join(conversion_args)

    # Temp files for cost basis
    bs1_temp_file = os.path.join(output_dir, f"bs1-{period}.tmp")
    bs2_temp_file = os.path.join(output_dir, f"bs2-{period}.tmp")

    commands = [
        # Create empty file
        f"echo -en '' > {report_file}",
    ]

    for command in commands:
        _ = run_command(command, verbose=verbose)
    
    if verbose >= 1:
        print(f"Generating yearly report for {period}...")


def add_yearly_plots(
    period: int,
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    data_dir: str,
    output_dir: str,
    verbose: int = 0,
):
    """Add side-by-side plots for asset evolution and income statement."""
    report_file = os.path.join(output_dir, f"{period}.org")
    
    # Find all .ledger files in data_dir
    data_files_args: list[str] = []
    if os.path.isdir(data_dir):
        for f in os.listdir(data_dir):
            if f.endswith(".ledger"):
                data_files_args.extend(["-f", os.path.join(data_dir, f)])
    
    # Generate Asset Evolution plot for the year - Top 3 assets
    evolution_plot = None
    try:
        command: list[str] = [
            "hledger",
            "-f",
            ledger,
            *data_files_args,
            *conversion_args,
            "bal",
            "acct:^assets:investments",
            "--historical",
            "--monthly",
            "--drop",
            "2",
            "--depth",
            "3",
            f"--value=end,{target_currency}",
            "--no-total",
            "--infer-market-prices",
            "-O",
            "csv",
            "--transpose",
            "--period",
            str(period),
        ]

        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, shell=False, universal_newlines=True
        )
        output, _ = process.communicate()
        csv_data = io.StringIO(output)
        df_evo: pd.DataFrame = pd.read_csv(csv_data, index_col=0)

        df_evo = df_evo.replace(re.escape(target_currency) + r"\s*", "", regex=True)
        df_evo = df_evo.replace(r"\s*[A-Z€$₹£¥]+\s*$", "", regex=True)
        df_evo = df_evo[df_evo.columns].apply(pd.to_numeric, errors="coerce")
        df_evo.index = pd.to_datetime(df_evo.index, format="%Y-%m")
        df_evo = df_evo.where(df_evo >= 0)

        if not df_evo.empty:
            plt_text.clear_figure()
            plt_text.date_form("Y-m")
            plt_text.plotsize(50, 15)

            # Find top 3 assets by final value
            final_values = df_evo.iloc[-1].sort_values(ascending=False)
            top_assets = final_values.head(3).index.tolist()
            
            dates = [d.strftime("%Y-%m") for d in df_evo.index]
            
            # Plot top assets as separate lines (up to 3) with very different markers
            # Note: Using markers that are visually distinct in terminal
            markers = ["braille", "dot", "fhd"]
            colors = ["green+", "cyan+", "yellow+"]
            
            for i, asset in enumerate(top_assets):
                # Extract short name (3rd level)
                short_name = asset.split(":")[-1] if ":" in asset else asset
                plt_text.plot(
                    dates, 
                    df_evo[asset].tolist(), 
                    label=short_name,
                    color=colors[i % len(colors)],
                    marker=markers[i % len(markers)]
                )
            
            plt_text.title(f"Asset Evolution {period} ({target_currency})")
            plt_text.xlabel("Month")
            plt_text.ylabel(f"Value ({target_currency})")
            
            evolution_plot = plt_text.build()
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            evolution_plot = ansi_escape.sub("", evolution_plot)
            plt_text.clear_figure()
        else:
            evolution_plot = "No asset evolution data for this period."
    except Exception as e:
        logger.error(f"Error generating asset evolution for {period}: {e}")
        evolution_plot = f"Error: {e}"

    # Generate Income Statement plot - Separate salary vs investment income
    income_plot = None
    try:
        command: list[str] = [
            "hledger",
            "-f",
            ledger,
            "bal",
            "acct:^income",
            "--monthly",
            "--drop",
            "1",
            "--depth",
            "2",
            "--no-total",
            "-O",
            "csv",
            "--transpose",
            "--period",
            str(period),
        ]

        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, shell=False, universal_newlines=True
        )
        output, _ = process.communicate()
        csv_data = io.StringIO(output)
        df_is: pd.DataFrame = pd.read_csv(csv_data, index_col=0)

        # Clean currency symbols and convert to numeric
        df_is = df_is.replace(r"\s*[A-Z€$₹£¥]+\s*", "", regex=True)
        df_is = df_is[df_is.columns].apply(pd.to_numeric, errors="coerce")
        df_is.index = pd.to_datetime(df_is.index, format="%Y-%m")

        if not df_is.empty:
            plt_text.clear_figure()
            plt_text.plotsize(50, 15)

            dates = [d.strftime("%Y-%m") for d in df_is.index]
            
            # Separate salary and investment income
            salary_cols = [col for col in df_is.columns if "salary" in col.lower()]
            investment_cols = [col for col in df_is.columns if "investment" in col.lower()]
            
            salary_income = df_is[salary_cols].sum(axis=1) if salary_cols else pd.Series([0] * len(df_is), index=df_is.index)
            investment_income = df_is[investment_cols].sum(axis=1) if investment_cols else pd.Series([0] * len(df_is), index=df_is.index)
            
            # Multiply by -1 to make income positive (hledger credits income accounts)
            salary_income = salary_income * -1
            investment_income = investment_income * -1
            
            # Reverse dates for top-to-bottom display
            dates_reversed = list(reversed(dates))
            salary_reversed = list(reversed(salary_income.tolist()))
            investment_reversed = list(reversed(investment_income.tolist()))
            
            # Plot as horizontal bars (months on Y-axis) with very different markers
            plt_text.bar(dates_reversed, salary_reversed, label="Salary", color="cyan+", orientation="h", marker="dot")
            plt_text.bar(dates_reversed, investment_reversed, label="Investments", color="yellow+", orientation="h", marker="fhd")
            
            plt_text.title(f"Income {period}")
            plt_text.xlabel("Amount")
            plt_text.ylabel("Month")
            
            income_plot = plt_text.build()
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            income_plot = ansi_escape.sub("", income_plot)
            plt_text.clear_figure()
        else:
            income_plot = "No income data for this period."
    except Exception as e:
        logger.error(f"Error generating income statement plot for {period}: {e}")
        income_plot = f"Error: {e}"

    # Write header and combine plots side by side
    with open(report_file, "a", encoding="utf-8") as f:
        f.write(f"* {period} Overview\n\n")
    
    if evolution_plot and income_plot:
        evo_lines = evolution_plot.split("\n")
        inc_lines = income_plot.split("\n")
        
        max_lines = max(len(evo_lines), len(inc_lines))
        evo_lines.extend([""] * (max_lines - len(evo_lines)))
        inc_lines.extend([""] * (max_lines - len(inc_lines)))
        
        combined = []
        for evo_line, inc_line in zip(evo_lines, inc_lines):
            padded_evo = evo_line.ljust(52)
            combined.append(padded_evo + inc_line)
        
        with open(report_file, "a", encoding="utf-8") as f:
            f.write("\n".join(combined) + "\n")
    
    if verbose >= 1:
        print(f"Added plots for {period}")


def add_yearly_tax_info(
    period: int,
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    data_dir: str,
    output_dir: str,
    verbose: int = 0,
):
    """Add tax information: investments in original currency and capital gains."""
    report_file = os.path.join(output_dir, f"{period}.org")
    
    # Find all .ledger files in data_dir
    data_files_args: list[str] = []
    if os.path.isdir(data_dir):
        data_files_args = [
            f"-f {os.path.join(data_dir, f)}"
            for f in os.listdir(data_dir)
            if f.endswith(".ledger")
        ]
    data_files_args_str = " ".join(data_files_args)

    # Temp files for cost basis
    bs1_temp_file = os.path.join(output_dir, f"bs1-{period}.tmp")
    bs2_temp_file = os.path.join(output_dir, f"bs2-{period}.tmp")

    # Section 1: Asset Holdings for Tax Purposes
    commands = [
        f"echo -en '\n* Asset Holdings (Volume and Cost in Original Currency)\n' >> {report_file}",
        f"hledger -f {ledger} bal type:AL investments --period 'to {period + 1}' --layout tall --tree --pretty=no --color=no --drop 5 --depth 5 --no-total > {bs1_temp_file}",
        f"hledger -f {ledger} {data_files_args_str} bal type:AL investments --period 'to {period + 1}' --pretty=no --infer-equity --cost --infer-cost --color=no --no-total --drop 3 | grep -v '                   0' > {bs2_temp_file}",
        f"paste {bs1_temp_file} {bs2_temp_file} | column -s $'\\t' -t >> {report_file}",
        f"rm -f {bs1_temp_file} {bs2_temp_file}",
    ]

    for command in commands:
        _ = run_command(command, verbose=verbose)
    
    # Section 2: Capital Gains
    with open(report_file, "a", encoding="utf-8") as f:
        f.write("\n* Capital Gains\n\n")
    
    # Find all .ledger files in data_dir for conversion
    data_files_args: list[str] = []
    if os.path.isdir(data_dir):
        for f in os.listdir(data_dir):
            if f.endswith(".ledger"):
                data_files_args.extend(["-f", os.path.join(data_dir, f)])
    
    try:
        # Get data in original currencies for the breakdown table
        command_gains_orig: list[str] = [
            "hledger",
            "-f",
            ledger,
            "bal",
            "acct:capital.gain",
            "--period",
            str(period),
            "--no-total",
            "-O",
            "csv",
        ]
        
        process = subprocess.Popen(
            command_gains_orig, stdout=subprocess.PIPE, shell=False, universal_newlines=True
        )
        output_orig, _ = process.communicate()
        
        # Get data converted to target currency for the plot
        command_gains: list[str] = [
            "hledger",
            "-f",
            ledger,
            *data_files_args,
            *conversion_args,
            "bal",
            "acct:capital.gain",
            f"--value=end,{target_currency}",
            "--infer-market-prices",
            "--monthly",
            "--no-total",
            "-O",
            "csv",
            "--transpose",
            "--period",
            str(period),
        ]
        
        process = subprocess.Popen(
            command_gains, stdout=subprocess.PIPE, shell=False, universal_newlines=True
        )
        output, _ = process.communicate()
        
        if output.strip():
            csv_data = io.StringIO(output)
            df_gains: pd.DataFrame = pd.read_csv(csv_data, index_col=0)
            
            # Clean currency symbols and convert to numeric
            df_gains = df_gains.replace(re.escape(target_currency) + r"\s*", "", regex=True)
            df_gains = df_gains.replace(r"\s*[A-Z€$₹£¥]+\s*", "", regex=True)
            df_gains = df_gains[df_gains.columns].apply(pd.to_numeric, errors="coerce")
            df_gains.index = pd.to_datetime(df_gains.index, format="%Y-%m")
            
            # Separate income and expenses accounts BEFORE sign conversion
            income_cols = [col for col in df_gains.columns if not col.startswith("expenses:")]
            expense_cols = [col for col in df_gains.columns if col.startswith("expenses:taxes")]
            
            # Multiply income by -1 to make gains positive (hledger credits income accounts)
            # Keep expenses positive (they are already debits, representing money spent)
            if income_cols:
                df_gains[income_cols] = df_gains[income_cols] * -1
            
            if not df_gains.empty:
                # Calculate totals
                if income_cols:
                    df_income_gains = df_gains[income_cols]
                    total_gains = df_income_gains.sum(axis=1)
                    total_gains_year = df_income_gains.sum().sum()
                else:
                    total_gains = pd.Series([0] * len(df_gains), index=df_gains.index)
                    total_gains_year = 0
                
                if expense_cols:
                    total_taxes_year = df_gains[expense_cols].sum().sum()
                else:
                    total_taxes_year = 0
                
                # Calculate tax rate
                if total_gains_year > 0:
                    tax_rate = (total_taxes_year / total_gains_year) * 100
                else:
                    tax_rate = 0
                
                # Create horizontal bar plot (months on Y-axis)
                plt_text.clear_figure()
                plt_text.plotsize(50, 10)
                
                dates = [d.strftime("%Y-%m") for d in df_income_gains.index]
                
                # Reverse for top-to-bottom display
                dates_reversed = list(reversed(dates))
                gains_reversed = list(reversed(total_gains.tolist()))
                
                plt_text.bar(dates_reversed, gains_reversed, color="green+", orientation="h")
                plt_text.title(f"Capital Gains {period} ({target_currency})")
                plt_text.xlabel("Amount")
                plt_text.ylabel("Month")
                plt_text.vline(0, color="gray")
                
                gains_plot = plt_text.build()
                ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                gains_plot = ansi_escape.sub("", gains_plot)
                plt_text.clear_figure()
                
                # Create summary box on the right
                summary_lines = [
                    "Summary:",
                    "",
                    f"Total Gains:  {total_gains_year:>12.2f} {target_currency}",
                    f"Total Taxes:  {total_taxes_year:>12.2f} {target_currency}",
                    f"Tax Rate:     {tax_rate:>12.1f} %",
                ]
                summary_str = "\n".join(summary_lines)
                
                # Combine plot and summary side by side
                plot_lines = gains_plot.split("\n")
                summary_lines_list = summary_str.split("\n")
                
                max_lines = max(len(plot_lines), len(summary_lines_list))
                plot_lines.extend([""] * (max_lines - len(plot_lines)))
                summary_lines_list.extend([""] * (max_lines - len(summary_lines_list)))
                
                combined = []
                for plot_line, summary_line in zip(plot_lines, summary_lines_list):
                    padded_plot = plot_line.ljust(52)
                    combined.append(padded_plot + summary_line)
                
                with open(report_file, "a", encoding="utf-8") as f:
                    f.write("\n".join(combined) + "\n\n")
                
                # Create breakdown table by account (below the plot) - with original currencies
                # Parse original currency data
                if output_orig.strip() and income_cols:
                    csv_data_orig = io.StringIO(output_orig)
                    df_orig = pd.read_csv(csv_data_orig)
                    
                    # Filter only income accounts (not expenses)
                    df_orig = df_orig[~df_orig['account'].str.startswith('expenses:')]
                    
                    # Find common account prefix for title
                    if not df_orig.empty:
                        first_account = df_orig['account'].iloc[0]
                        parts = first_account.split(":")
                        if len(parts) >= 3:
                            account_prefix = ":".join(parts[:3])  # e.g., "income:financial investments:capital gain"
                        else:
                            account_prefix = "capital gain"
                    else:
                        account_prefix = "capital gain"
                    
                    # Create table string with original currencies
                    table_lines = [f"Capital Gains by Source ({account_prefix}):", ""]
                    
                    if not df_orig.empty:
                        # Sort by account name for now (can't easily sort by value with mixed currencies)
                        for _, row in df_orig.iterrows():
                            account = row['account']
                            balance_str = str(row['balance'])
                            
                            # Skip if near zero
                            # Extract numeric value from balance string to check
                            numeric_val = 0
                            try:
                                # Try to extract first number
                                nums = re.findall(r'-?\d+\.?\d*', balance_str)
                                if nums:
                                    numeric_val = float(nums[0])
                            except:
                                pass
                            
                            if abs(numeric_val) < 0.01:
                                continue
                            
                            # Extract meaningful name from account (from 4th level onwards)
                            parts = account.split(":")
                            if len(parts) > 3:
                                account_short = ":".join(parts[3:])
                            else:
                                account_short = account
                            
                            # Format with proper alignment - account name left-aligned, value right-aligned
                            # Allow longer names (60 chars) and ensure value alignment
                            table_lines.append(f"  {account_short:60s} {balance_str:>20s}")
                    else:
                        table_lines.append("  No capital gains data")
                    
                    # Write breakdown table below the plot
                    with open(report_file, "a", encoding="utf-8") as f:
                        f.write("\n".join(table_lines) + "\n\n")
                else:
                    with open(report_file, "a", encoding="utf-8") as f:
                        f.write("No capital gains data\n\n")
            else:
                with open(report_file, "a", encoding="utf-8") as f:
                    f.write("No capital gains data for this period.\n\n")
        else:
            with open(report_file, "a", encoding="utf-8") as f:
                f.write("No capital gains data for this period.\n\n")
                
    except Exception as e:
        logger.error(f"Error generating capital gains for {period}: {e}")
        with open(report_file, "a", encoding="utf-8") as f:
            f.write(f"Error: {e}\n\n")
    
    if verbose >= 1:
        print(f"Added tax information for {period}")


def generate_summary_report(
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    output_dir: str,
    verbose: int = 0,
    include_svg_plot: bool = False,
):

    # Prepare conversion args string for f-string insertion
    conv_args_str = " ".join(conversion_args)
    summary_file_abs = os.path.abspath(
        os.path.join(output_dir, "summary.org")
    )  # Absolute path to summary file

    commands = []

    # Only include plot reference if SVG plots are being generated
    if include_svg_plot:
        combined_plot_abs = os.path.abspath(
            os.path.join(output_dir, "combined-overview.svg")
        )  # Absolute path to plot file
        # Calculate relative path from the directory containing summary.org to the plot file
        summary_dir = os.path.dirname(summary_file_abs)
        combined_plot_rel_path = os.path.relpath(combined_plot_abs, start=summary_dir)

        # Reference the combined plot using the calculated relative path
        commands.append(
            f"echo -en '* Portfolio Overview Graph\n[[file:{combined_plot_rel_path}]]\n' > {summary_file_abs}"
        )
    else:
        # Just create the file without plot reference - text plots will be added first
        commands.append(f"echo -en '' > {summary_file_abs}")

    for command in commands:
        _ = run_command(command, verbose=verbose)

    if verbose >= 1:
        print("Completed summary report header")


def add_balance_sheet_to_summary(
    ledger: str,
    target_currency: str,
    conversion_args: list[str],
    output_dir: str,
    verbose: int = 0,
):
    """Add balance sheet section to summary.org after plots."""
    conv_args_str = " ".join(conversion_args)
    summary_file_abs = os.path.abspath(os.path.join(output_dir, "summary.org"))

    commands = [
        f"echo -en '\n* Summary balance sheet last three years\n' >> {summary_file_abs}",
        f"hledger -f {ledger} {conv_args_str} bs --tree --pretty=no --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from 2 years ago to today' --infer-market-prices --value=end,{target_currency} --yearly --output-format txt >> {summary_file_abs}",
    ]

    for command in commands:
        _ = run_command(command, verbose=verbose)

    if verbose >= 1:
        print("Completed balance sheet section")


def generate_combined_figure(
    ledger_file: str,
    data_dir: str,
    target_currency: str,
    conversion_args: list[str],
    output_dir: str,
    benchmark_tickers: list[str],
    roi_investment_account: str = "investments",
    roi_pnl_account: str = "unrealized",
    roi_begin_date: str | None = None,
    verbose: int = 0,
    enable_roi_plots: bool = True,
    ticker_map: dict[str, str] | None = None,
    currency_map: dict[str, str] | None = None,
):
    """Generate a combined figure with Asset Distribution, Evolution, and ROI."""
    logger.info("Generating combined overview figure...")
    if not enable_roi_plots:
        logger.info("ROI plot generation is disabled.")

    # --- Ensure Price Data is Available ---
    # Fetch and cache price data for all commodities in the ledger
    # This needs to happen before generating any graphs that require prices
    # Note: price_files will be reused by roi.get_roi_data() to avoid re-fetching
    logger.info("Ensuring price data is available...")
    try:
        price_files = prices.ensure_price_data(
            ledger_file,
            data_dir,
            ticker_map=ticker_map,
            currency_map=currency_map,
            target_currency=target_currency,
            only_nonzero_balance=True,
        )
        if verbose >= 1 and price_files:
            logger.info(f"Price data cached in: {', '.join(price_files)}")
    except Exception as e:
        logger.warning(f"Could not ensure price data: {e}")

    # --- Create Figure and Subplots ---
    # Use constrained_layout for better automatic spacing
    # Adjust figsize for a potentially wider layout
    fig = plt.figure(figsize=(12, 8), constrained_layout=True)

    # Define a 3-row, 2-column grid
    # Row 0: Asset Evolution (spans 2 cols)
    # Row 1: Asset Distribution (col 0), Yearly TWR (col 1)
    # Adjust height/width ratios for desired emphasis
    gs = fig.add_gridspec(
        2, 2, height_ratios=[1, 1], width_ratios=[1, 1.2]
    )  # Adjusted to 2 rows

    ax_evol = fig.add_subplot(gs[0, :])  # Top row, spans both columns
    ax_dist = fig.add_subplot(gs[1, 0])  # Bottom row, left column
    ax_roi_bars = fig.add_subplot(gs[1, 1])  # Bottom row, right column

    # --- Plot Asset Distribution (Bottom-Left) ---
    try:
        # Use period=0 for overall distribution
        generate_asset_distribution_graph(
            0, ledger_file, target_currency, conversion_args, ax=ax_dist
        )
    except Exception as e:
        logger.error(f"Error generating asset distribution plot: {e}")
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
        logger.error(f"Error generating asset evolution plot: {e}")
        ax_evol.text(
            0.5, 0.5, "Error generating plot", ha="center", va="center", color="red"
        )
        ax_evol.set_title("Asset Evolution")  # Still add title

    # --- Get ROI Data (needed for both bottom plots) ---
    df_portfolio, benchmark_series = None, []  # Initialize
    if enable_roi_plots:
        try:
            df_portfolio, benchmark_series = roi.get_roi_data(
                ledger_file=ledger_file,
                data_dir=data_dir,
                conversion_args=conversion_args,
                benchmark_tickers=benchmark_tickers,
                investment_account=roi_investment_account,
                pnl_account=roi_pnl_account,
                begin_date=roi_begin_date,
                currency=target_currency,
                price_files=price_files,  # Reuse already-fetched price files
            )
        except Exception as e:
            logger.error(f"Error getting ROI data: {e}")
            # Add error text to ROI plot if data fetching fails
            ax_roi_bars.text(
                0.5,
                0.5,
                "Error getting ROI data",
                ha="center",
                va="center",
                color="red",
            )
            ax_roi_bars.set_title("Yearly TWR Comparison")
    else:
        ax_roi_bars.text(
            0.5, 0.5, "ROI reporting disabled.", ha="center", va="center", color="grey"
        )
        ax_roi_bars.set_title("Yearly TWR Comparison")

    # --- Plot Yearly TWR Bars (Bottom-Right) ---
    if enable_roi_plots and df_portfolio is not None:
        try:
            roi.plot_yearly_twr_bars(
                ax_roi_bars, df_portfolio, benchmark_series, benchmark_tickers
            )
        except Exception as e:
            logger.error(f"Error generating yearly TWR bar plot: {e}")
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
        logger.info(f"Combined overview figure saved to {plot_file}")
    except Exception as e:
        logger.error(f"Error saving combined figure: {e}")
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
        help="Directory for cached commodity price (.ledger) files. Price data fetched from Yahoo Finance will be stored here.",
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
        default=["spy"],  # Default to S&P 500 as a list
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
        "--roi-report",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable or disable ROI report generation (default: enabled). Use --roi-report or --no-roi-report.",
    )
    _ = parser.add_argument(
        "--ticker-map",
        help="Map commodity names to Yahoo Finance tickers (format: COMMODITY:TICKER, e.g., VWCE:VWCE.DE). Can be specified multiple times.",
        required=False,
        type=str,
        nargs="*",
        default=[],
    )
    _ = parser.add_argument(
        "--currency-map",
        help="Map commodity names to currency symbols (format: COMMODITY:SYMBOL, e.g., VWCE:€). Optional: currencies are auto-detected from Yahoo Finance if not specified.",
        required=False,
        type=str,
        nargs="*",
        default=[],
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        help="Set output verbosity level (0=silent, 1=normal, 2=detailed).",
        type=int,
        default=0,
        choices=[0, 1, 2],
    )
    _ = parser.add_argument(
        "--svg-plots",
        action="store_true",
        default=False,
        help="Generate SVG combined plot instead of text-based plots (default: text-based plots).",
    )
    args: argparse.Namespace = parser.parse_args()

    # Configure logging based on verbosity
    if args.verbose == 0:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    elif args.verbose == 1:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    else:  # verbose == 2
        logging.basicConfig(
            level=logging.DEBUG, format="%(levelname)s [%(name)s]: %(message)s"
        )

    # Suppress verbose logging from matplotlib and PIL
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)

    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # Parse ticker and currency mappings
    ticker_map = {}
    for mapping in args.ticker_map:
        if ":" in mapping:
            commodity, ticker = mapping.split(":", 1)
            ticker_map[commodity] = ticker

    currency_map = {}
    for mapping in args.currency_map:
        if ":" in mapping:
            commodity, currency = mapping.split(":", 1)
            currency_map[commodity] = currency

    # Detect all currencies in the main ledger
    all_currencies = get_ledger_currencies(args.ledger, verbose=args.verbose)
    logger.info(f"Detected currencies: {all_currencies}")

    # Identify currencies that need conversion to the target currency
    other_currencies = all_currencies - {args.currency}
    logger.info(f"Target currency: {args.currency}")
    logger.info(f"Other currencies requiring conversion checks: {other_currencies}")

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
                args.data_dir,
                args.output_dir,
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
            args.svg_plots,  # Pass whether SVG plots will be generated
        )
    )

    # Execute text report tasks in parallel
    logger.info("Starting parallel generation of text reports...")
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
                logger.error(f"Text report generation task raised an exception: {exc}")
    logger.info("Finished text report generation.")
    
    # --- Add plots and tax info to yearly reports ---
    logger.info("Adding plots and tax information to yearly reports...")
    for period in periods:
        try:
            add_yearly_plots(
                period=period,
                ledger=args.ledger,
                target_currency=args.currency,
                conversion_args=conversion_args,
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                verbose=args.verbose,
            )
            add_yearly_tax_info(
                period=period,
                ledger=args.ledger,
                target_currency=args.currency,
                conversion_args=conversion_args,
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                verbose=args.verbose,
            )
        except Exception as exc:
            logger.error(f"Error adding content to yearly report {period}: {exc}")
    logger.info("Finished yearly report details.")

    # --- Generate Plots (text-based by default, SVG if requested) ---
    if args.svg_plots:
        # Generate SVG combined figure
        generate_combined_figure(
            ledger_file=args.ledger,
            data_dir=args.data_dir,
            target_currency=args.currency,
            conversion_args=conversion_args,
            output_dir=args.output_dir,
            benchmark_tickers=args.benchmark_ticker,
            roi_investment_account=args.roi_investment_account,
            roi_pnl_account=args.roi_pnl_account,
            roi_begin_date=args.roi_begin_date,
            verbose=args.verbose,
            enable_roi_plots=args.roi_report,
            ticker_map=ticker_map,
            currency_map=currency_map,
        )
    else:
        # Generate text-based plots
        generate_text_plots(
            ledger_file=args.ledger,
            data_dir=args.data_dir,
            target_currency=args.currency,
            conversion_args=conversion_args,
            benchmark_tickers=args.benchmark_ticker,
            roi_investment_account=args.roi_investment_account,
            roi_pnl_account=args.roi_pnl_account,
            roi_begin_date=args.roi_begin_date,
            verbose=args.verbose,
            enable_roi_plots=args.roi_report,
            ticker_map=ticker_map,
            currency_map=currency_map,
            output_dir=args.output_dir,
        )

        # Add balance sheet to summary after plots
        add_balance_sheet_to_summary(
            ledger=args.ledger,
            target_currency=args.currency,
            conversion_args=conversion_args,
            output_dir=args.output_dir,
            verbose=args.verbose,
        )

    logger.info("All report generation finished.")

# Local Variables:
# jinx-local-words: "bs bs-"
# End:
