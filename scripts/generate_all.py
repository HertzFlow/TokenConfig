#!/usr/bin/env python3
"""
Generate all.xxx.json from aggr.xxx.json
Usage: generate_all.py [--version-major MAJOR] [--version-minor MINOR] [--version-patch PATCH]
Example: generate_all.py --version-major 1 --version-minor 0 --version-patch 0 --input oracle/aggr.testnet.json --output all.testnet.json
"""

import json
import datetime
import argparse
from pathlib import Path


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


def convert_aggr_to_all(aggr_data, version_major=1, version_minor=0, version_patch=0):
    """Convert aggr.testnet.json format to all.testnet.json format"""

    # Get current timestamp in ISO format
    current_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Convert symbols to tokens
    tokens = []
    for symbol_data in aggr_data.get("symbols", []):
        token = {
            "address": symbol_data["bsc_token_addr"],
            "chainId": 97,  # BSC Testnet
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
            "minor": version_minor + 1,  # Increment minor version by default
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

    args = parser.parse_args()

    try:
        # Determine version numbers
        version_major = args.version_major
        version_minor = args.version_minor
        version_patch = args.version_patch

        # If any version argument is not provided (using default values), try to read from existing file
        if version_major == 1 and version_minor == 0 and version_patch == 0:
            existing_version = read_version_from_file(args.output)
            if existing_version:
                version_major, version_minor, version_patch = existing_version
                print(
                    f"📖 Read version from existing file: {version_major}.{version_minor}.{version_patch}"
                )
            else:
                print(
                    f"⚠️  No existing version found, using defaults: {version_major}.{version_minor}.{version_patch}"
                )
        else:
            print(
                f"📌 Using provided version: {version_major}.{version_minor}.{version_patch}"
            )

        # Read aggr.testnet.json
        aggr_file = Path(args.input)
        if not aggr_file.exists():
            print(f"Error: {aggr_file} not found")
            return

        with open(aggr_file, "r", encoding="utf-8") as f:
            aggr_data = json.load(f)

        # Convert to all.testnet.json format
        output_data = convert_aggr_to_all(
            aggr_data, version_major, version_minor, version_patch
        )

        # Write to all.testnet.json
        output_file = Path(args.output)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)

        print(f"✅ Successfully generated {output_file}")
        print(f"📊 Generated {len(output_data['tokens'])} tokens")
        print(
            f"🔢 Version: {output_data['version']['major']}.{output_data['version']['minor']}.{output_data['version']['patch']}"
        )
        print(f"⏰ Timestamp: {output_data['timestamp']}")
        print("\n📋 Sample tokens:")
        for i, token in enumerate(output_data["tokens"][:5]):
            print(f"   {i+1}. {token['symbol']}: {token['name']}")
        if len(output_data["tokens"]) > 5:
            print(f"   ... and {len(output_data['tokens']) - 5} more tokens")

    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
