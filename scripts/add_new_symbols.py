#!/usr/bin/env python3
"""
Add new symbols from a CSV into an oracle pyth config (e.g. oracle/pyth.local.json).

The CSV columns are: index, asset_type, symbol, symbol_normalized, pyth_lazer_id.
For each new symbol we look up its feed_id (hermes_id, hex) from the Pyth Pro API by
matching pyth_lazer_id, then append it to the config. If the matched feed has no
hermes_id, the symbol is still appended with feed_id set to null (and reported at the
end). Symbols already present (by normalized symbol or pyth_lazer_id) are skipped. The
kp field is assigned by rotating through the kp values already used by the config (see
next_kp), continuing from the last entry so the new symbols keep the existing
round-robin balance.

Usage: add_new_symbols.py [--csv CSV] [--file FILE] [--dry-run]
Example: add_new_symbols.py --csv scripts/new_symbols.csv --file oracle/pyth.local.json --dry-run
"""

import argparse
import csv
import json
import sys
from urllib.request import Request, urlopen

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
    """Fetch price feeds from Pyth Pro API."""
    req = Request(PYTH_PRO_PRICE_FEEDS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)


def build_lazer_to_hermes_map(feeds):
    """Build a map from pyth_lazer_id -> normalized hermes_id (feed_id)."""
    mapping = {}
    for feed in feeds:
        lazer_id = feed.get("pyth_lazer_id")
        hermes_id = normalize_hermes_id(feed.get("hermes_id"))
        if lazer_id is None or hermes_id is None:
            continue
        mapping[lazer_id] = hermes_id
    return mapping


def kp_range(symbols):
    """Derive the rotating kp range from existing symbols.

    kp=0 is reserved (used by a few special symbols), so the rotation runs over
    1..max_kp. Returns max_kp, defaulting to 6 if nothing usable is found.
    """
    kps = [s.get("kp", 0) for s in symbols if isinstance(s.get("kp"), int)]
    max_kp = max(kps) if kps else 6
    return max(max_kp, 1)


def next_kp(last_kp, max_kp):
    """Return the next kp continuing the 1..max_kp cycle (kp=0 reserved)."""
    return last_kp % max_kp + 1


def read_new_symbols(csv_path):
    """Read rows of (symbol, pyth_lazer_id) from the CSV.

    Columns: index, asset_type, symbol, symbol_normalized, pyth_lazer_id.
    Uses the normalized symbol (col 4) to match the config's clean-symbol style.
    """
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        for raw in csv.reader(f):
            if not raw or not raw[0].strip():
                continue
            if len(raw) < 5:
                print(f"Skipping malformed row: {raw}", file=sys.stderr)
                continue
            symbol = raw[3].strip()
            lazer_id = int(raw[4].strip())
            rows.append((symbol, lazer_id))
    return rows


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Add new symbols from a CSV into an oracle pyth config"
    )
    parser.add_argument(
        "--csv", type=str, default="scripts/new_symbols.csv", help="New symbols CSV path"
    )
    parser.add_argument(
        "--file", type=str, default="oracle/pyth.local.json", help="Config path to update"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be added without writing the file",
    )
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        config = json.load(f)
    symbols = config.setdefault("symbols", [])

    existing_symbols = {s.get("symbol") for s in symbols}
    existing_lazer = {s.get("pyth_lazer_id") for s in symbols}

    new_rows = read_new_symbols(args.csv)

    feeds = fetch_pyth_pro_price_feeds()
    lazer_to_hermes = build_lazer_to_hermes_map(feeds)

    max_kp = kp_range(symbols)
    last_kp = symbols[-1].get("kp", 0) if symbols else 0

    added = 0
    skipped_dup = []
    missing_feed = []
    for symbol, lazer_id in new_rows:
        if symbol in existing_symbols or lazer_id in existing_lazer:
            skipped_dup.append(symbol)
            continue
        feed_id = lazer_to_hermes.get(lazer_id)
        if feed_id is None:
            missing_feed.append((symbol, lazer_id))

        kp = next_kp(last_kp, max_kp)
        last_kp = kp
        entry = {
            "symbol": symbol,
            "feed_id": feed_id,
            "kp": kp,
            "pyth_lazer_id": lazer_id,
        }
        symbols.append(entry)
        existing_symbols.add(symbol)
        existing_lazer.add(lazer_id)
        added += 1
        if args.dry_run:
            print(f"+ {symbol} -> feed_id={feed_id} kp={kp} pyth_lazer_id={lazer_id}")

    if not args.dry_run:
        with open(args.file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
            f.write("\n")

    print(f"\nAdded {added}/{len(new_rows)} new symbols to {args.file}.")
    if skipped_dup:
        print(f"Skipped {len(skipped_dup)} duplicates: {', '.join(skipped_dup)}")
    if missing_feed:
        print(f"No feed_id found for {len(missing_feed)} symbols:", file=sys.stderr)
        for symbol, lazer_id in missing_feed:
            print(f"  - {symbol} (pyth_lazer_id={lazer_id})", file=sys.stderr)


if __name__ == "__main__":
    main()
