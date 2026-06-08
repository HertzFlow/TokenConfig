#!/usr/bin/env python3
"""
Check an oracle pyth config for problems:
  - symbols missing feed_id or pyth_lazer_id
  - duplicate symbol across symbols
  - duplicate feed_id across symbols
  - duplicate pyth_lazer_id across symbols

Exits non-zero if any problem is found.
Usage: check_pyth_config.py [--file FILE]
Example: check_pyth_config.py --file oracle/pyth.local.json
"""

import argparse
import json
import sys
from collections import defaultdict


def is_empty(value):
    """A field is considered missing if it is None or an empty/blank string."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Check an oracle pyth config for missing/duplicate feed_id and pyth_lazer_id"
    )
    parser.add_argument(
        "--file", type=str, default="oracle/pyth.local.json", help="Config path to check"
    )
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        config = json.load(f)
    symbols = config.get("symbols", [])

    missing_feed = []
    missing_lazer = []
    symbol_to_indices = defaultdict(list)
    feed_to_symbols = defaultdict(list)
    lazer_to_symbols = defaultdict(list)

    for i, entry in enumerate(symbols):
        label = entry.get("symbol") or f"<index {i}>"
        feed_id = entry.get("feed_id")
        lazer_id = entry.get("pyth_lazer_id")

        if not is_empty(entry.get("symbol")):
            symbol_to_indices[entry.get("symbol")].append(i)

        if is_empty(feed_id):
            missing_feed.append(label)
        else:
            feed_to_symbols[str(feed_id).strip().lower()].append(label)

        if is_empty(lazer_id):
            missing_lazer.append(label)
        else:
            lazer_to_symbols[str(lazer_id)].append(label)

    dup_symbol = {k: v for k, v in symbol_to_indices.items() if len(v) > 1}
    dup_feed = {k: v for k, v in feed_to_symbols.items() if len(v) > 1}
    dup_lazer = {k: v for k, v in lazer_to_symbols.items() if len(v) > 1}

    print(f"Checked {len(symbols)} symbols in {args.file}.\n")

    ok = True

    if missing_feed:
        ok = False
        print(f"Missing feed_id ({len(missing_feed)}):")
        for label in missing_feed:
            print(f"  - {label}")
    else:
        print("feed_id: all present.")

    if missing_lazer:
        ok = False
        print(f"\nMissing pyth_lazer_id ({len(missing_lazer)}):")
        for label in missing_lazer:
            print(f"  - {label}")
    else:
        print("pyth_lazer_id: all present.")

    if dup_symbol:
        ok = False
        print(f"\nDuplicate symbol ({len(dup_symbol)}):")
        for symbol, indices in dup_symbol.items():
            print(f"  - {symbol}: indices {', '.join(str(i) for i in indices)}")
    else:
        print("\nsymbol: no duplicates.")

    if dup_feed:
        ok = False
        print(f"\nDuplicate feed_id ({len(dup_feed)}):")
        for feed_id, labels in dup_feed.items():
            print(f"  - {feed_id}: {', '.join(labels)}")
    else:
        print("feed_id: no duplicates.")

    if dup_lazer:
        ok = False
        print(f"\nDuplicate pyth_lazer_id ({len(dup_lazer)}):")
        for lazer_id, labels in dup_lazer.items():
            print(f"  - {lazer_id}: {', '.join(labels)}")
    else:
        print("pyth_lazer_id: no duplicates.")

    if not ok:
        print("\nFAIL: problems found.")
        sys.exit(1)
    print("\nOK: no problems found.")


if __name__ == "__main__":
    main()
