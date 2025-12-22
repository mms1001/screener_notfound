# src/run.py
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
from pathlib import Path


def run_cmd(cmd: list[str]) -> None:
    print("\n▶️  Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="End-to-end runner: universe -> fetch_prices -> screen."
    )

    # Universe
    ap.add_argument(
        "--universe-input",
        nargs="+",
        required=True,
        help="One or more raw CSVs with tickers/symbols to build the universe.",
    )
    ap.add_argument(
        "--universe-output",
        default="data/universe/tickers.csv",
        help="Canonical universe CSV output path.",
    )
    ap.add_argument(
        "--exclude-default-instruments",
        action="store_true",
        help="Exclude common non-common-stock instruments (warrants/rights-ish).",
    )
    ap.add_argument(
        "--keep-only-valid",
        action="store_true",
        help="Keep only tickers matching a basic US ticker regex (A-Z0-9 . -).",
    )

    # Prices
    ap.add_argument(
        "--prices-dir",
        default="data/prices",
        help="Directory to store per-ticker price CSVs.",
    )
    ap.add_argument(
        "--years-back",
        type=int,
        default=6,
        help="How many years back to fetch on first download.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit tickers for testing (applies to fetch + screen).",
    )

    # Screen params
    ap.add_argument("--window-5y", type=int, default=1260)
    ap.add_argument("--window-52w", type=int, default=252)
    ap.add_argument("--window-90d", type=int, default=63)
    ap.add_argument("--rebound-pct", type=float, default=0.30)
    ap.add_argument("--drawdown-pct", type=float, default=0.80)

    # Output
    ap.add_argument(
        "--results-dir",
        default="data/results",
        help="Directory to store screening outputs.",
    )

    return ap.parse_args()


def main():
    args = parse_args()

    # Create run_id and result path
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / f"screen_run_{run_id}.csv"

    universe_output = Path(args.universe_output)

    # 1) Build universe
    cmd_universe = [
        "python",
        "src/universe.py",
        "--input",
        *args.universe_input,
        "--output",
        str(universe_output),
    ]
    if args.exclude_default_instruments:
        cmd_universe.append("--exclude-default-instruments")
    if args.keep_only_valid:
        cmd_universe.append("--keep-only-valid")

    run_cmd(cmd_universe)

    # 2) Fetch/update prices
    cmd_fetch = [
        "python",
        "src/fetch_prices.py",
        "--universe",
        str(universe_output),
        "--prices-dir",
        args.prices_dir,
        "--years-back",
        str(args.years_back),
    ]
    if args.limit:
        cmd_fetch += ["--limit", str(args.limit)]

    run_cmd(cmd_fetch)

    # 3) Screen
    cmd_screen = [
        "python",
        "src/screen.py",
        "--universe",
        str(universe_output),
        "--prices-dir",
        args.prices_dir,
        "--output",
        str(results_path),
        "--window-5y",
        str(args.window_5y),
        "--window-52w",
        str(args.window_52w),
        "--window-90d",
        str(args.window_90d),
        "--rebound-pct",
        str(args.rebound_pct),
        "--drawdown-pct",
        str(args.drawdown_pct),
    ]
    if args.limit:
        cmd_screen += ["--limit", str(args.limit)]

    run_cmd(cmd_screen)

    print("\n✅ DONE")
    print(f"Universe: {universe_output.resolve()}")
    print(f"Prices:   {Path(args.prices_dir).resolve()}")
    print(f"Results:  {results_path.resolve()}")


if __name__ == "__main__":
    main()

