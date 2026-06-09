#!/usr/bin/env python3
"""
Generate TS synthetic-token config lines from an oracle/aggr.*.json file.

For each symbol it emits a line like:
    "MMT/USD": { synthetic: true, decimals: 18, oracleProvider: "gmOracle" },

Everything after the symbol is hard-coded. Output goes to stdout by default,
or to a file via --output.

Usage: gen_synthetic_ts.py [--input FILE] [--output FILE] [--indent N]
Example: gen_synthetic_ts.py --input oracle/aggr.local.json --output synthetic.ts
"""

import argparse
import json
import sys

LINE_TPL = (
    '{indent}"{symbol}": {{ synthetic: true, decimals: 18, '
    'oracleProvider: "gmOracle" }},'
)


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Generate TS synthetic-token config lines from an aggr config"
    )
    parser.add_argument(
        "--input", type=str, default="oracle/aggr.local.json", help="Input aggr config path"
    )
    parser.add_argument(
        "--output", type=str, default=None, help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--indent", type=int, default=4, help="Leading spaces per line (default: 4)"
    )
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        config = json.load(f)

    indent = " " * args.indent
    lines = [
        LINE_TPL.format(indent=indent, symbol=s["symbol"])
        for s in config.get("symbols", [])
        if s.get("symbol")
    ]
    text = "\n".join(lines) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {len(lines)} lines to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
