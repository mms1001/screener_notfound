#!/usr/bin/env python3
"""
Master script to update all universes (US, Europe, Global, B3) and generate category matches.

This script will:
1. Build/update universe files for US, Europe, Global, and B3
2. Fetch/update prices for each universe
3. Run screening for each universe
4. Export category A, B, and C matches for each universe
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and print it."""
    print("\n" + "=" * 80)
    print("▶️  Running:", " ".join(cmd))
    print("=" * 80)
    result = subprocess.run(cmd, check=check)
    return result


def build_universe_us(output_path: Path) -> None:
    """Build US universe from SEC data."""
    print("\n📊 Building US Universe from SEC...")
    cmd = [
        "python",
        "-m",
        "src.universe",
        "--source",
        "sec",
        "--output",
        str(output_path),
        "--keep-only-valid",
    ]
    run_cmd(cmd)
    print(f"✅ US universe saved to: {output_path}")


def filter_universe_by_market_cap(
    input_path: Path,
    output_path: Path,
    min_mcap: int = 400_000_000,
    min_price_usd: float = 1.0,
    limit: int | None = None,
) -> None:
    """Filter universe by minimum market cap and price (both in USD)."""
    print(f"\n💰 Filtering universe by market cap (>= ${min_mcap/1e6:.0f}M USD) and price (>= ${min_price_usd:.2f} USD)...")
    cmd = [
        "python",
        "-m",
        "src.filter_market_cap",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--min-mcap",
        str(min_mcap),
        "--min-price-usd",
        str(min_price_usd),
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])
    run_cmd(cmd)
    print(f"✅ Filtered universe saved to: {output_path}")


def build_universe_europe(output_path: Path, limit: int | None = None, min_mcap: int = 400_000_000, min_price_usd: float = 1.0) -> None:
    """Build European universe from Wikidata."""
    print("\n📊 Building European Universe from Wikidata...")
    cmd = [
        "python",
        "scripts/build_universe_europe.py",
        "--output-all",
        str(output_path.parent / "universe_europe_all.txt"),
        "--output-filtered",
        str(output_path.parent / "universe_europe_filtered.txt"),
        "--min-mcap-usd",
        str(min_mcap),
        "--min-price-usd",
        str(min_price_usd),
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])
    run_cmd(cmd)
    
    # Convert to CSV format (raw universe, before USD filtering)
    print("\n📝 Converting European universe to CSV...")
    import pandas as pd
    txt_path = output_path.parent / "universe_europe_filtered.txt"
    raw_csv_path = output_path.parent / "universe_europe_raw.csv"
    if txt_path.exists():
        df = pd.read_csv(txt_path, header=None, names=["ticker"])
        df.to_csv(raw_csv_path, index=False)
        print(f"✅ European raw universe saved to: {raw_csv_path}")
    else:
        print(f"⚠️  Warning: {txt_path} not found, skipping conversion")
        return
    
    # Apply unified investible filter (market cap >= 400M USD, price >= 1 USD)
    print("\n💰 Applying investible universe filter to Europe...")
    filter_universe_by_market_cap(
        input_path=raw_csv_path,
        output_path=output_path,
        min_mcap=400_000_000,
        limit=limit,
    )
    print(f"✅ Filtered European universe saved to: {output_path}")


def build_universe_global(output_path: Path) -> None:
    """Build Global universe by combining US, Europe, and B3 (all filtered by investible criteria)."""
    print("\n📊 Building Global Universe (US + Europe + B3, all filtered)...")
    import pandas as pd
    
    us_path = Path("data/universe_us_filtered.csv")  # Use filtered US universe
    europe_path = Path("data/universe_europe_filtered.csv")  # Use filtered Europe universe
    b3_path = Path("data/universe_b3_filtered.csv")  # Use filtered B3 universe
    
    if not us_path.exists():
        print(f"❌ Error: {us_path} not found. Please build US universe first.")
        return
    
    # Start with US
    df_us = pd.read_csv(us_path)
    if "ticker" not in df_us.columns:
        df_us = df_us.rename(columns={df_us.columns[0]: "ticker"})
    
    dfs_to_combine = [df_us[["ticker"]]]
    
    # Add Europe if exists
    if europe_path.exists():
        df_europe = pd.read_csv(europe_path)
        if "ticker" not in df_europe.columns:
            df_europe = df_europe.rename(columns={df_europe.columns[0]: "ticker"})
        dfs_to_combine.append(df_europe[["ticker"]])
    else:
        print(f"⚠️  Warning: {europe_path} not found. Proceeding without Europe.")
    
    # Add B3 if exists
    if b3_path.exists():
        df_b3 = pd.read_csv(b3_path)
        if "ticker" not in df_b3.columns:
            df_b3 = df_b3.rename(columns={df_b3.columns[0]: "ticker"})
        dfs_to_combine.append(df_b3[["ticker"]])
    else:
        print(f"⚠️  Warning: {b3_path} not found. Proceeding without B3.")
    
    # Combine and deduplicate
    df_global = pd.concat(dfs_to_combine, ignore_index=True)
    df_global = df_global.drop_duplicates(subset=["ticker"], keep="first")
    df_global = df_global.sort_values("ticker").reset_index(drop=True)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df_global.to_csv(output_path, index=False)
    print(f"✅ Global universe saved to: {output_path} ({len(df_global):,} tickers)")


def build_universe_b3(output_path: Path, limit: int | None = None) -> None:
    """Build B3 universe from seed CSV."""
    print("\n📊 Building B3 Universe from seed CSV...")
    cmd = [
        "python",
        "scripts/build_universe_b3.py",
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])
    run_cmd(cmd)
    
    # The script writes to data/universe_b3.csv, which is our input
    # We just need to verify it exists
    input_path = Path("data/universe_b3.csv")
    if input_path.exists():
        print(f"✅ B3 universe saved to: {input_path}")
    else:
        print(f"⚠️  Warning: {input_path} not found after build")


def run_pipeline_for_universe(
    universe_name: str,
    universe_input: Path,
    universe_output: Path,
    prices_dir: Path,
    results_dir: Path,
    years_back: int = 6,
    limit: int | None = None,
) -> Path:
    """Run the full pipeline (universe -> fetch -> screen) for a universe."""
    print(f"\n🚀 Running pipeline for {universe_name} universe...")
    
    # Use run.py to do everything
    cmd = [
        "python",
        "-m",
        "src.run",
        "--universe-input",
        str(universe_input),
        "--universe-output",
        str(universe_output),
        "--prices-dir",
        str(prices_dir),
        "--years-back",
        str(years_back),
        "--results-dir",
        str(results_dir),
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])
    
    run_cmd(cmd)
    
    # Find the most recent screen result
    result_files = sorted(results_dir.glob(f"screen_run_*.csv"), reverse=True)
    if result_files:
        return result_files[0]
    else:
        raise FileNotFoundError(f"No screen results found in {results_dir}")


def export_candidates(
    screen_result: Path,
    output_dir: Path,
    universe_name: str,
) -> None:
    """Export category A, B, and C candidates."""
    print(f"\n📤 Exporting candidates for {universe_name}...")
    
    cmd = [
        "python",
        "-m",
        "src.export_candidates",
        "--input",
        str(screen_result),
        "--outdir",
        str(output_dir),
        "--b-only",
    ]
    run_cmd(cmd)
    
    # Rename files to include universe name
    for cat in ["A", "B", "B_only", "C"]:
        old_path = output_dir / f"candidates_{cat}.csv"
        if old_path.exists():
            new_path = output_dir / f"candidates_{cat}_{universe_name.lower()}.csv"
            old_path.rename(new_path)
            print(f"✅ Renamed: {old_path.name} -> {new_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Update all universes and generate category matches"
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip building universes (use existing files)",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip fetching prices (use existing data)",
    )
    parser.add_argument(
        "--skip-screen",
        action="store_true",
        help="Skip screening (use existing results)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit tickers for testing (applies to all steps)",
    )
    parser.add_argument(
        "--years-back",
        type=int,
        default=6,
        help="Years of price history to fetch",
    )
    parser.add_argument(
        "--universe",
        choices=["us", "europe", "global", "b3", "all"],
        default="all",
        help="Which universe(s) to process",
    )
    
    args = parser.parse_args()
    
    # Base directories
    data_dir = Path("data")
    universe_dir = data_dir / "universe"
    prices_base = data_dir / "prices"
    results_base = data_dir / "results"
    
    # Universe file paths
    universe_files = {
        "us": {
            "raw": data_dir / "universe_us.csv",
            "filtered": data_dir / "universe_us_filtered.csv",
            "input": data_dir / "universe_us_filtered.csv",  # Use filtered for pipeline
            "output": universe_dir / "tickers_us.csv",
        },
        "europe": {
            "input": data_dir / "universe_europe_filtered.csv",
            "output": universe_dir / "tickers_europe.csv",
        },
        "global": {
            "input": data_dir / "universe_global.csv",
            "output": universe_dir / "tickers_global.csv",
        },
        "b3": {
            "raw": data_dir / "universe_b3.csv",
            "filtered": data_dir / "universe_b3_filtered.csv",
            "input": data_dir / "universe_b3_filtered.csv",  # Use filtered for pipeline
            "output": universe_dir / "tickers_b3.csv",
        },
    }
    
    # Determine which universes to process
    if args.universe == "all":
        universes_to_process = ["us", "europe", "global", "b3"]
    else:
        universes_to_process = [args.universe]
    
    # Step 1: Build universes
    if not args.skip_build:
        if "us" in universes_to_process:
            # Build raw US universe
            build_universe_us(universe_files["us"]["raw"])
            # Filter by market cap (400M USD) and price (1 USD minimum)
            filter_universe_by_market_cap(
                input_path=universe_files["us"]["raw"],
                output_path=universe_files["us"]["filtered"],
                min_mcap=400_000_000,
                min_price_usd=1.0,
                limit=args.limit,
            )
        
        if "europe" in universes_to_process:
            build_universe_europe(
                universe_files["europe"]["input"],
                limit=args.limit,
                min_mcap=400_000_000,
                min_price_usd=1.0,
            )
        
        if "global" in universes_to_process:
            build_universe_global(universe_files["global"]["input"])
        
        if "b3" in universes_to_process:
            # Build raw B3 universe
            build_universe_b3(universe_files["b3"]["raw"], limit=args.limit)
            # Filter by market cap (400M USD) and price (1 USD minimum)
            filter_universe_by_market_cap(
                input_path=universe_files["b3"]["raw"],
                output_path=universe_files["b3"]["filtered"],
                min_mcap=400_000_000,
                min_price_usd=1.0,
                limit=args.limit,
            )
    
    # Step 2: Run pipeline for each universe
    for universe_name in universes_to_process:
        universe_input = universe_files[universe_name]["input"]
        universe_output = universe_files[universe_name]["output"]
        prices_dir = prices_base / universe_name
        results_dir = results_base / universe_name
        
        # Create directories
        prices_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)
        
        if not universe_input.exists():
            print(f"⚠️  Skipping {universe_name}: {universe_input} not found")
            continue
        
        # Run pipeline
        if not args.skip_fetch and not args.skip_screen:
            screen_result = run_pipeline_for_universe(
                universe_name=universe_name,
                universe_input=universe_input,
                universe_output=universe_output,
                prices_dir=prices_dir,
                results_dir=results_dir,
                years_back=args.years_back,
                limit=args.limit,
            )
        elif not args.skip_screen:
            # Only run screening (prices already exist)
            print(f"\n🔍 Running screening for {universe_name}...")
            from src.screen import run_screen, ScreenConfig
            
            cfg = ScreenConfig()
            results_dir.mkdir(parents=True, exist_ok=True)
            screen_result = results_dir / f"screen_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            # Use universe_output if it exists, otherwise use input
            universe_csv = universe_output if universe_output.exists() else universe_input
            if not universe_csv.exists():
                print(f"⚠️  Universe file not found: {universe_csv}, skipping")
                continue
            
            run_screen(
                universe_csv=universe_csv,
                prices_dir=prices_dir,
                output_csv=screen_result,
                cfg=cfg,
                limit=args.limit,
            )
        else:
            # Find most recent screen result
            screen_result = sorted(results_dir.glob("screen_run_*.csv"), reverse=True)
            if not screen_result:
                print(f"⚠️  No screen results found for {universe_name}, skipping export")
                continue
            screen_result = screen_result[0]
        
        # Step 3: Export candidates
        if screen_result.exists():
            export_candidates(screen_result, results_dir, universe_name)
    
    print("\n" + "=" * 80)
    print("✅ ALL DONE!")
    print("=" * 80)
    print("\nResults are in:")
    for universe_name in universes_to_process:
        results_dir = results_base / universe_name
        if results_dir.exists():
            print(f"  {universe_name.upper()}: {results_dir.resolve()}")
            for cat in ["A", "B", "B_only", "C"]:
                cat_file = results_dir / f"candidates_{cat}_{universe_name.lower()}.csv"
                if cat_file.exists():
                    print(f"    - {cat_file.name}")


if __name__ == "__main__":
    main()

