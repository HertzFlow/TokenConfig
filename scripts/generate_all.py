#!/usr/bin/env python3
"""
Generate all.xxx.json from aggr.xxx.json
Usage: generate_all.py [--version-major MAJOR] [--version-minor MINOR] [--version-patch PATCH] [--input INPUT_FILE] [--output OUTPUT_FILE] [--testnet]
Example: generate_all.py --version-major 1 --version-minor 0 --version-patch 0 --input oracle/aggr.testnet.json --output all.testnet.json --testnet
"""

import json
import re
import sys
import datetime
import argparse
from pathlib import Path

# Make stdout/stderr UTF-8 tolerant. This repo's output is full of emoji
# (📖 ⚠️ 📌 📊 🔢 ⏰ 📋 ✅ ❌) and Python defaults to the console's legacy
# code page (cp1252/cp1254 on Windows). Without this, printing any emoji
# raises UnicodeEncodeError — which, because these calls sit AFTER the
# output file is written, makes a fully successful run exit non-zero and
# `make` / the SOP report a false failure.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        # Very old / exotic stream; the ASCII-safe fallbacks below still work.
        pass
# Allow-list for token addresses emitted into all.*.json, which the trading
# backend consumes directly. Anything else is a config-integrity bug.
_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def generate_token_name(symbol):
    """Generate token name based on symbol"""
    return f"{symbol} synthetic market"


def read_version_from_file(file_path):
    """Read version from existing output file"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            version = data.get("version", {})
            return (
                version.get("major", 1),
                version.get("minor", 0),
                version.get("patch", 0),
            )
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def convert_aggr_to_all(
    aggr_data, chainId=56, version_major=1, version_minor=0, version_patch=0
):
    """Convert aggr.testnet.json format to all.testnet.json format"""

    # Get current timestamp in ISO format
    current_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Convert symbols to tokens
    tokens = []
    for symbol_data in aggr_data.get("symbols", []):
        token = {
            "address": symbol_data["bsc_token_addr"],
            # BSC chainId resolved from an explicit --chain-id/--testnet flag.
            # Comment corrected from a hardcoded "BSC Testnet" note, which was
            # wrong for every mainnet run.
            "chainId": chainId,
            "decimals": symbol_data["bsc_precision"],
            "symbol": symbol_data["symbol"],
            "name": generate_token_name(symbol_data["symbol"]),
        }
        tokens.append(token)

    # Create the output data structure
    output_data = {
        "name": "HertzFlow",
        "timestamp": current_timestamp,
        "version": {
            "major": version_major,
            "minor": version_minor,
            "patch": version_patch,
        },
        "keywords": ["HertzFlow", "default", "list"],
        "tokens": tokens,
    }

    return output_data


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Generate all.testnet.json from aggr.testnet.json"
    )
    parser.add_argument(
        "--version-major", type=int, default=1, help="Major version number"
    )
    parser.add_argument(
        "--version-minor",
        type=int,
        default=0,
        help="Minor version number (will be incremented by 1)",
    )
    parser.add_argument(
        "--version-patch", type=int, default=0, help="Patch version number"
    )
    parser.add_argument(
        "--input", type=str, default="oracle/aggr.testnet.json", help="Input file path"
    )
    parser.add_argument(
        "--output", type=str, default="all.testnet.json", help="Output file path"
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Flag to indicate testnet (shorthand for --chain-id 97)",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=None,
        help=(
            "Explicit BSC chainId (97 testnet / 56 mainnet). Overrides "
            "--testnet. Preferred: it cannot be silently forgotten."
        ),
    )

    args = parser.parse_args()

    # SECURITY / DATA-INTEGRITY: refuse to guess the network. The chainId used
    # to default to 56 (BSC MAINNET) whenever --testnet was absent, so
    # `generate_all.py --input oracle/aggr.local.json` (and the `gen-all-local`
    # Makefile target, which omitted the flag) emitted TESTNET-derived contract
    # addresses stamped with MAINNET chainId 56. Consumers that trust chainId
    # then resolve addresses that do not exist on mainnet. Chain id is a
    # security-relevant routing key, so it must be stated, never defaulted.
    _CHAIN_IDS = (97, 56)
    if args.chain_id is not None:
        if args.chain_id not in _CHAIN_IDS:
            print(f"Error: --chain-id must be one of {list(_CHAIN_IDS)}", file=sys.stderr)
            sys.exit(2)
        chainId = args.chain_id
    elif args.testnet:
        chainId = 97
    else:
        print(
            "Error: refusing to guess the network. Pass --chain-id 97 (BSC "
            "testnet) or --chain-id 56 (BSC mainnet) explicitly, or --testnet "
            "as shorthand for --chain-id 97. Defaulting to 56 previously "
            "stamped mainnet chainIds onto testnet addresses.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        # Determine version numbers
        version_major = args.version_major
        version_minor = args.version_minor
        version_patch = args.version_patch
        # chainId resolved above (explicit --chain-id / --testnet required).

        # If any version argument is not provided (using default values), try to read from existing file
        if version_major == 1 and version_minor == 0 and version_patch == 0:
            existing_version = read_version_from_file(args.output)
            if existing_version:
                version_major, version_minor, version_patch = existing_version
                version_minor += 1  # Regenerating from an existing file bumps minor
                print(
                    f"Read version from existing file, bumping minor: "
                    f"{version_major}.{version_minor}.{version_patch}"
                )
            else:
                print(
                    f"No existing version found, using defaults: {version_major}.{version_minor}.{version_patch}"
                )
        else:
            print(
                f"Using provided version: {version_major}.{version_minor}.{version_patch}"
            )

        # Read aggr.testnet.json
        aggr_file = Path(args.input)
        if not aggr_file.exists():
            # Exit non-zero: a bare `return` exits 0, so `make gen-all-*` and
            # the SOP's `subprocess.run(..., check=True)` would treat a missing
            # input as success and leave a stale all.*.json in place.
            print(f"Error: {aggr_file} not found", file=sys.stderr)
            sys.exit(1)

        with open(aggr_file, "r", encoding="utf-8") as f:
            aggr_data = json.load(f)

        # Convert to all.testnet.json format
        output_data = convert_aggr_to_all(
            aggr_data, chainId, version_major, version_minor, version_patch
        )

        # DATA-INTEGRITY GUARD: every token must carry a well-formed EVM
        # address and a sane integer decimals value. A malformed address here
        # becomes a market row consumed by the trading backend, so refuse to
        # emit a config rather than shipping a bad one.
        bad_tokens = [
            (t.get("symbol"), t.get("address"))
            for t in output_data["tokens"]
            if not _EVM_ADDR_RE.fullmatch(str(t.get("address", "")))
            or not isinstance(t.get("decimals"), int)
            or not (0 <= t["decimals"] <= 36)
        ]
        if bad_tokens:
            for sym, addr in bad_tokens[:10]:
                print(f"Error: invalid token entry symbol={sym!r} address={addr!r}", file=sys.stderr)
            print(f"Error: {len(bad_tokens)} invalid token entries; refusing to write {args.output}", file=sys.stderr)
            sys.exit(1)

        # Write to all.testnet.json
        output_file = Path(args.output)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
            # Keep the trailing newline so regenerated files are byte-stable
            # against the committed versions (avoids spurious diffs).
            f.write("\n")

        # NOTE: no emoji in these messages. Printing U+2705 and friends raises
        # UnicodeEncodeError on cp1252/cp1254 Windows consoles AFTER the file
        # has already been written, so a fully successful run exits 1 and
        # `make` / the SOP reports a false failure.
        print(f"Successfully generated {output_file}")
        print(f"Generated {len(output_data['tokens'])} tokens")
        print(
            f"Version: {output_data['version']['major']}.{output_data['version']['minor']}.{output_data['version']['patch']}"
        )
        print(f"Timestamp: {output_data['timestamp']}")
        print("\nSample tokens:")
        for i, token in enumerate(output_data["tokens"][:5]):
            print(f"   {i+1}. {token['symbol']}: {token['name']}")
        if len(output_data["tokens"]) > 5:
            print(f"   ... and {len(output_data['tokens']) - 5} more tokens")

    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
