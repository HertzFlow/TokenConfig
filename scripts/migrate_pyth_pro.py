#!/usr/bin/env python3
"""
Add pyth_lazer_id (decimal) to an oracle config by matching the hermes feed_id (hex).
Fetches feeds from the Pyth Pro API and maps hermes_id -> pyth_lazer_id, then writes
pyth_lazer_id into each symbol of the input file in place. By default a symbol that
already has a pyth_lazer_id is left untouched; pass --override to replace it.
Usage: migrate_pyth_pro.py [--file FILE] [--dry-run] [--override]
Example: migrate_pyth_pro.py --file oracle/pyth.testnet.json --dry-run
"""

import argparse
import json
import sys
from urllib.request import Request, urlopen

# PYTH_CORE_PRICE_FEEDS_URL = "https://hermes.pyth.network/v2/price_feeds"
PYTH_PRO_PRICE_FEEDS_URL = "https://pyth.dourolabs.app/v1/symbols"


def normalize_hermes_id(hermes_id):
    """Normalize a hermes id to lowercase hex without the 0x prefix."""
    if hermes_id is None:
        return None
    hermes_id = hermes_id.strip().lower()
    if hermes_id.startswith("0x"):
        hermes_id = hermes_id[2:]
    return hermes_id


def fetch_pyth_pro_price_feeds():
    # [
    #   {
    #   "pyth_lazer_id": 641,
    #   "name": "ZEREBROUSD",
    #   "symbol": "Crypto.ZEREBRO/USD",
    #   "description": "ZEREBRO / US DOLLAR",
    #   "asset_type": "crypto",
    #   "instrument_type": "spot",
    #   "exponent": -8,
    #   "cmc_id": 34083,
    #   "interval": null,
    #   "min_publishers": 3,
    #   "min_channel": "fixed_rate@200ms",
    #   "state": "stable",
    #   "schedule": "America/New_York;O,O,O,O,O,O,O;",
    #   "market_session_schedule": {
    #     "regular": "America/New_York;O,O,O,O,O,O,O;"
    #   },
    #   "market_sessions": {
    #     "regular": {
    #       "min_pub": 3,
    #       "schedule": "America/New_York;O,O,O,O,O,O,O;",
    #       "state": "stable"
    #     }
    #   },
    #   "hermes_id": "3dd13bf483f196da0429b354db1fa4802ff6a5c19c559a6abdd9a92707f426dc",
    #   "nasdaq_symbol": null,
    #   "quote_currency": "USD"
    # },
    # ]
    """Fetch price feeds from Pyth Pro API"""
    req = Request(PYTH_PRO_PRICE_FEEDS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)


def build_hermes_to_lazer_map(feeds):
    """Build a map from normalized hermes_id -> pyth_lazer_id."""
    mapping = {}
    for feed in feeds:
        hermes_id = normalize_hermes_id(feed.get("hermes_id"))
        lazer_id = feed.get("pyth_lazer_id")
        if hermes_id is None or lazer_id is None:
            continue
        mapping[hermes_id] = lazer_id
    return mapping


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Add pyth_lazer_id to oracle config by matching hermes feed_id"
    )
    parser.add_argument(
        "--file", type=str, default="oracle/pyth.testnet.json", help="Input file path"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print symbol -> feed_id -> lazer_id without writing the file",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Overwrite pyth_lazer_id even if the symbol already has one (default: keep existing)",
    )

    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        config = json.load(f)

    feeds = fetch_pyth_pro_price_feeds()
    hermes_to_lazer = build_hermes_to_lazer_map(feeds)

    symbols = config.get("symbols", [])
    matched = 0
    skipped = 0
    missing = []
    for entry in symbols:
        feed_id = normalize_hermes_id(entry.get("feed_id"))
        lazer_id = hermes_to_lazer.get(feed_id)
        if args.dry_run:
            print(f"{entry.get('symbol')} -> {feed_id} -> {lazer_id}")
        if lazer_id is None:
            missing.append(entry.get("symbol"))
            continue
        if entry.get("pyth_lazer_id") is not None and not args.override:
            skipped += 1
            continue
        entry["pyth_lazer_id"] = lazer_id
        matched += 1

    if not args.dry_run:
        with open(args.file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
            f.write("\n")

    print(f"Matched {matched}/{len(symbols)} symbols.")
    if skipped:
        print(f"Kept existing pyth_lazer_id for {skipped} symbols (use --override to replace).")
    if missing:
        print(f"No pyth_lazer_id found for {len(missing)} symbols:", file=sys.stderr)
        for symbol in missing:
            print(f"  - {symbol}", file=sys.stderr)


if __name__ == "__main__":
    main()
