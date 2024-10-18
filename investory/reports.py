"""Generate reports with hledger."""
# ruff: noqa: E501
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed, Future

from typing import List

DATA = "/home/nasser/Sync/documents/admin/finances/data/"


def run_command(command: str) -> str:
    result: subprocess.CompletedProcess = subprocess.run(
        command, shell=True, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def generate_report(period, ledger):
    os.makedirs(f"reports/{period}", exist_ok=True)

    commands = [
        # Income statement
        f"echo -en '* Monthly income graph\n[[file:monthly-in-{period}.svg]]\n' > reports/{period}/is-{period}.org",
        f"echo -en '* Summary income statement\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --depth 1 --period 'from {period - 2} to {period + 1}' --tree --pretty >> reports/{period}/is-{period}.org",
        f"echo -en '* Income statement\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --depth 2 --monthly --average --period {period} --tree --pretty >> reports/{period}/is-{period}.org",
        f"echo -en '* Full Income statement\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --yearly --period {period} --tree --pretty --layout tall >> reports/{period}/is-{period}.org",
        f"echo -en '* Full Income statement monthly\n' >> reports/{period}/is-{period}.org",
        f"hledger -f {ledger} is --sort --monthly --average --row-total --period {period} --tree --pretty --layout tall >> reports/{period}/is-{period}.org",
        # Balance sheet
        f"echo -en '* Monthly investments evolution graph\n[[file:investments-{period}.svg]]\n' > reports/{period}/bs-{period}.org",
        f"echo -en '* Summary balance sheet last three years\n' >> reports/{period}/bs-{period}.org",
        f"hledger -f {ledger} bs --tree --pretty --depth 1 --alias '/^(income|expenses)\b/=equity:retained earnings' --period 'from {period - 2} to {period + 1}' --infer-market-prices --value=end,€ -f {DATA}/prices/EURUSD=X.ledger -f {DATA}/prices/BRLUSD=X.ledger --yearly >> reports/{period}/bs-{period}.org",
        f"echo -en '* Balance sheet valued at period ends\n' >> reports/{period}/bs-{period}.org",
        f"hledger -f {ledger} bs --depth 3 --infer-market-prices --value=end --tree --pretty --no-total --period {period} >> reports/{period}/bs-{period}.org",
        f"echo -en '* Investments converted to cost in R$\n' >> reports/{period}/bs-{period}.org",
        f"hledger -f {ledger} bal type:AL --historical investments --period {period} --layout tall --tree --pretty --drop 5 --depth 5 --no-total >> reports/{period}/bs1-{period}.org",
        f"hledger -f {ledger} bal type:AL --historical investments -f {DATA}/prices/EURUSD=X.ledger SD.ledger -f {DATA}/prices/BRLUSD=X.ledger --period {period} --infer-equity --cost --infer-cost --infer-market-prices --exchange=R$ --drop 3 | grep -v '                   0' >> reports/{period}/bs2-{period}.org",
        f"echo -en 'Investments {period}, converted to cost in R$ \n' >> reports/{period}/bs-{period}.org",
        f"paste reports/{period}/bs1-{period}.org reports/{period}/bs2-{period}.org | column -s $'\t' -t >> reports/{period}/bs-{period}.org",
        f"rm reports/{period}/bs1-{period}.org reports/{period}/bs2-{period}.org",
    ]

    for command in commands:
        run_command(command)

    print(f"Completed report for period: {period}")


def get_ledger_years(ledger_file: str) -> List[int]:
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
    args = parser.parse_args()
    periods = get_ledger_years(args.ledger)

    # Each process (CPU) runs the function for a period simultaneously
    with ProcessPoolExecutor(max_workers=len(periods)) as executor:
        futures: List[Future] = [executor.submit(generate_report, period, args.ledger) for period in periods]
        for future in as_completed(futures):
            future.result()

# Local Variables:
# jinx-local-words: "bs bs-"
# End:
