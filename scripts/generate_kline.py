#!/usr/bin/env python3
"""
Generate kline.xxx.json from aggr.xxx.json
Usage: generate_kline.py [--input INPUT_FILE] [--output OUTPUT_FILE] [--testnet]
Example: generate_kline.py --input oracle/aggr.testnet.json --output kline/kline.testnet.json
"""

import json
import datetime
import argparse
from pathlib import Path

SYMBOL_BLACK_LIST = set(
    [
        "CRV/USD",
        "WETH/USD",
        "USDT/USD",
        "USDC/USD",
    ]
)


def convert_aggr_to_kline(
    aggr_data,
    kline_data=None,
):
    kline_kp_map = {}
    if kline_data:
        for kline_symbol in kline_data.get("symbols", []):
            symbol = kline_symbol.get("symbol")
            kp = kline_symbol.get("kp", 0)
            if symbol:
                kline_kp_map[symbol] = kp

    # Convert symbols to tokens
    symbol_cfgs = []
    for symbol_data in aggr_data.get("symbols", []):
        symbol = symbol_data["symbol"]

        if SYMBOL_BLACK_LIST.__contains__(symbol):
            print(f"Skipping blacklisted symbol: {symbol}")
            continue

        symbol_cfg = {
            "symbol": symbol,
            "kp": kline_kp_map.get(symbol, 0),  # Use kp from kline data if available
        }

        symbol_cfgs.append(symbol_cfg)

    # Create the output data structure
    output_data = {
        "symbols": symbol_cfgs,
    }

    return output_data


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Generate all.testnet.json from aggr.testnet.json"
    )
    parser.add_argument(
        "--input", type=str, default="oracle/aggr.testnet.json", help="Input file path"
    )
    parser.add_argument(
        "--output", type=str, default="all.testnet.json", help="Output file path"
    )

    args = parser.parse_args()

    try:
        # Read aggr.testnet.json
        aggr_file = Path(args.input)
        if not aggr_file.exists():
            print(f"Error: {aggr_file} not found")
            return

        with open(aggr_file, "r", encoding="utf-8") as f:
            aggr_data = json.load(f)

        # Read kline.xxx.json version if exists
        kline_data = None
        kline_file = Path(args.output)
        if not kline_file.exists():
            print(f"{kline_file} not found, generate it")
        else:
            with open(kline_file, "r", encoding="utf-8") as f:
                kline_data = json.load(f)

        # Convert to all.testnet.json format
        output_data = convert_aggr_to_kline(aggr_data, kline_data)

        # Write to all.testnet.json
        output_file = Path(args.output)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        print(f"✅ Successfully generated {output_file}")

    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
