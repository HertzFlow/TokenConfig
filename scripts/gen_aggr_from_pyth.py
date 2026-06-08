#!/usr/bin/env python3
"""
Generate / update oracle/aggr.local.json from oracle/pyth.local.json.

- Every aggr symbol gets a pyth_lazer_id field, read from pyth.local.json by symbol.
- Symbols present in pyth but missing from aggr are appended (in pyth order).
- For each appended symbol:
    bsc_precision          = 18
    bsc_token_oracle_type  = "one-percent-per-minute"
    feed_id                = pyth feed_id (may be null)
    pyth_lazer_id          = pyth pyth_lazer_id
    kp                     = rotated (continues the existing 1..max_kp cycle)
    bsc_token_addr         = `yarn ts-node scripts/get_synthetic_token_addr.ts <symbol>`

Usage: gen_aggr_from_pyth.py [--pyth FILE] [--aggr FILE] [--dry-run]
Example: gen_aggr_from_pyth.py --dry-run
"""

import argparse
import json
import subprocess
import sys

TOKEN_ADDR_CMD = [
    "yarn",
    "--silent",
    "ts-node",
    "./scripts/get_synthetic_token_addr.ts",
]


def kp_range(symbols):
    """Derive the rotating kp range (1..max_kp); kp=0 is reserved for specials."""
    kps = [s.get("kp", 0) for s in symbols if isinstance(s.get("kp"), int)]
    max_kp = max(kps) if kps else 6
    return max(max_kp, 1)


def next_kp(last_kp, max_kp):
    """Return the next kp continuing the 1..max_kp cycle (kp=0 reserved)."""
    return last_kp % max_kp + 1


def get_token_addr(symbol):
    """Call the ts-node helper to derive the synthetic bsc token address."""
    out = subprocess.run(
        TOKEN_ADDR_CMD + [symbol],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Generate/update aggr config from pyth config"
    )
    parser.add_argument(
        "--pyth", type=str, default="oracle/pyth.local.json", help="Pyth config path"
    )
    parser.add_argument(
        "--aggr", type=str, default="oracle/aggr.local.json", help="Aggr config path"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing the file",
    )
    args = parser.parse_args()

    with open(args.pyth, "r", encoding="utf-8") as f:
        pyth = json.load(f)
    with open(args.aggr, "r", encoding="utf-8") as f:
        aggr = json.load(f)

    pyth_symbols = pyth.get("symbols", [])
    aggr_symbols = aggr.setdefault("symbols", [])

    # symbol -> pyth entry (for feed_id / pyth_lazer_id lookup)
    pyth_by_symbol = {s["symbol"]: s for s in pyth_symbols}

    # 1) Backfill pyth_lazer_id onto every existing aggr entry.
    backfilled = 0
    for entry in aggr_symbols:
        p = pyth_by_symbol.get(entry["symbol"])
        if p is not None:
            entry["pyth_lazer_id"] = p.get("pyth_lazer_id")
            backfilled += 1

    existing = {s["symbol"] for s in aggr_symbols}

    # 2) Append pyth symbols missing from aggr, in pyth order.
    max_kp = kp_range(aggr_symbols)
    last_kp = aggr_symbols[-1].get("kp", 0) if aggr_symbols else 0

    added = 0
    for p in pyth_symbols:
        symbol = p["symbol"]
        if symbol in existing:
            continue

        kp = next_kp(last_kp, max_kp)
        last_kp = kp
        token_addr = get_token_addr(symbol)

        entry = {
            "symbol": symbol,
            "kp": kp,
            "bsc_precision": 18,
            "bsc_token_addr": token_addr,
            "bsc_token_addr_env_map": {},
            "bsc_token_oracle_type": "one-percent-per-minute",
            "pyth_only": True,
            "feed_id": p.get("feed_id"),
            "pyth_lazer_id": p.get("pyth_lazer_id"),
            "cex_symbol_map": {},
            "need_sign": True,
        }
        aggr_symbols.append(entry)
        existing.add(symbol)
        added += 1
        if args.dry_run:
            print(
                f"+ {symbol} kp={kp} addr={token_addr} "
                f"feed_id={p.get('feed_id')} pyth_lazer_id={p.get('pyth_lazer_id')}"
            )

    if not args.dry_run:
        with open(args.aggr, "w", encoding="utf-8") as f:
            json.dump(aggr, f, indent=4, ensure_ascii=False)
            f.write("\n")

    print(
        f"\nBackfilled pyth_lazer_id on {backfilled} existing symbols; "
        f"added {added} new symbols to {args.aggr}."
    )


if __name__ == "__main__":
    main()
