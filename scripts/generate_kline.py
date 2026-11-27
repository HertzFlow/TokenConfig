#!/usr/bin/env python3
"""
Generate kline.xxx.json from aggr.xxx.json
Usage: generate_kline.py [--input INPUT_FILE] [--output OUTPUT_FILE] [--testnet]
Example: generate_kline.py --input oracle/aggr.testnet.json --output kline/kline.testnet.json
"""

import json
import sys
import datetime
import argparse
from pathlib import Path

SYMBOL_BLACK_LIST = set(
    [
        "CRV/USD",
        "WETH/USD",
        "USDT/USD",
        "USDC/USD",
        "USD1/USD",
    ]
)

KP_RANGE = 3


def next_kp(last_kp):
    """Return the next kp value continuing the 0→1→…→(K-1) cycle."""
    return (last_kp + 1) % KP_RANGE


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
    last_new_kp = KP_RANGE - 1  # so first new symbol gets 0
    # DATA-INTEGRITY GUARD: this generator allocates kp in 0..KP_RANGE-1, but
    # sop/core.py (KpAllocator, MAX_KP) allocates 1..6 in the aggr configs, and
    # the committed aggr/kline files carry those different ranges. Rather than
    # silently "fixing" either side (which would renumber live markets), surface
    # the divergence so it is a decision, not an accident.
    aggr_kps = {
        s.get("symbol"): s.get("kp")
        for s in aggr_data.get("symbols", [])
        if isinstance(s.get("kp"), int)
    }
    out_of_range = {
        sym: kp for sym, kp in aggr_kps.items()
        if not (0 <= kp < KP_RANGE)
    }
    if out_of_range:
        sample = dict(list(sorted(out_of_range.items()))[:5])
        print(
            f"WARNING: {len(out_of_range)} aggr symbol(s) carry kp outside "
            f"0..{KP_RANGE - 1} (this generator's range). Sample: {sample}. "
            f"aggr is written by sop/core.py with MAX_KP=6. Confirm the kp "
            f"namespace is intentionally different before trusting kline kp.",
            file=sys.stderr,
        )
    for symbol_data in aggr_data.get("symbols", []):
        symbol = symbol_data["symbol"]

        if symbol in SYMBOL_BLACK_LIST:
            print(f"Skipping blacklisted symbol: {symbol}")
            continue

        if symbol in kline_kp_map:
            kp = kline_kp_map[symbol]
        else:
            kp = next_kp(last_new_kp)
            last_new_kp = kp

        symbol_cfg = {
            "symbol": symbol,
            "kp": kp,
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
        # Read aggr.xxx.json
        aggr_file = Path(args.input)
        if not aggr_file.exists():
            # Fail loudly: a bare `return` here exits 0, so `make gen-kline-*`
            # and any CI step using it would report SUCCESS while writing
            # nothing — a stale kline config would silently ship.
            print(f"Error: {aggr_file} not found", file=sys.stderr)
            sys.exit(1)

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

        # Convert to kline.xxx.json format
        output_data = convert_aggr_to_kline(aggr_data, kline_data)

        # Write to kline.xxx.json
        output_file = Path(args.output)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
            # Match sop/core.py dump_json: keep the trailing newline so the
            # committed files are byte-stable across regenerations.
            f.write("\n")

        # NOTE: no emoji in these messages. On a cp1252/cp1254 Windows console
        # (the default for many users of this repo) printing U+2705 raises
        # UnicodeEncodeError AFTER the file is already written, so the process
        # exits 1 on a fully successful run and `make` reports a false failure.
        print(f"Successfully generated {output_file}")

    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
