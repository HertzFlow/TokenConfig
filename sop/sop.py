#!/usr/bin/env python3
"""
New-market SOP — interactive entry point.

Run it with the repo's system Python; it bootstraps a local ./.venv (installing
sop/requirements.txt) and re-executes itself inside that venv, so the operator only
ever types `make sop` / `./sop/sop.py`.

The flow, per the SOP:
  1. Pick a request CSV (sop/new_market_template.csv format).
  2. Pick target environment(s): any of local / testnet / mainnet.
  3. Fill the three source-of-truth oracle configs from the CSV:
        oracle/pyth.<env>.json, oracle/cex.<env>.json, oracle/aggr.<env>.json
  4. Regenerate the derived files with the existing, reused scripts:
        scripts/generate_kline.py  -> kline/kline.<env>.json
        scripts/generate_all.py    -> all.<env>.json   (--testnet for testnet)

Everything is previewed and confirmed before any file is written (dry-run first).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOP_DIR = REPO_ROOT / "sop"
VENV_DIR = REPO_ROOT / ".venv"
REQUIREMENTS = SOP_DIR / "requirements.txt"


# --------------------------------------------------------------------------- #
# venv bootstrap — runs under the system interpreter, then re-execs under .venv
# --------------------------------------------------------------------------- #
def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv() -> None:
    """Create ./.venv + install deps if needed, then re-exec inside it.

    A sentinel env var prevents infinite re-exec. Dependency presence is checked
    by import; install only runs when something is missing.
    """
    venv_py = _venv_python()
    running_in_venv = Path(sys.prefix).resolve() == VENV_DIR.resolve()

    if not running_in_venv:
        if not venv_py.exists():
            print(f"• creating venv at {VENV_DIR} ...")
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
            _pip_install(venv_py)
        elif not _deps_ok(venv_py):
            _pip_install(venv_py)
        # Re-exec inside the venv.
        os.execv(str(venv_py), [str(venv_py), __file__, *sys.argv[1:]])

    # Already in the venv: make sure deps are present (e.g. requirements changed).
    if not _deps_importable():
        _pip_install(venv_py)
        os.execv(str(venv_py), [str(venv_py), __file__, *sys.argv[1:]])


def _deps_importable() -> bool:
    try:
        import eth_abi  # noqa: F401
        import eth_utils  # noqa: F401
        import questionary  # noqa: F401

        return True
    except ImportError:
        return False


def _deps_ok(venv_py: Path) -> bool:
    probe = "import eth_abi, eth_utils, questionary, eth_hash.auto"
    return subprocess.run([str(venv_py), "-c", probe], capture_output=True).returncode == 0


def _pip_install(venv_py: Path) -> None:
    print("• installing sop dependencies ...")
    subprocess.run([str(venv_py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run([str(venv_py), "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)], check=True)


ensure_venv()

# ---- below here we are guaranteed to be inside .venv with deps installed ---- #
import questionary  # noqa: E402

from core import (  # noqa: E402
    KpAllocator,
    build_lazer_to_feed,
    derive_synthetic_addr,
    dump_json,
    fetch_pyth_pro_feeds,
    fill_aggr,
    fill_cex,
    fill_pyth,
    load_json,
    parse_csv,
    synthetic_chain_id,
)

ENVS = ("local", "testnet", "mainnet")

# Environments that are production and get an extra explicit confirmation.
PROD_ENVS = frozenset({"mainnet"})


def env_paths(env: str) -> dict[str, Path]:
    return {
        "pyth": REPO_ROOT / "oracle" / f"pyth.{env}.json",
        "cex": REPO_ROOT / "oracle" / f"cex.{env}.json",
        "aggr": REPO_ROOT / "oracle" / f"aggr.{env}.json",
        "kline": REPO_ROOT / "kline" / f"kline.{env}.json",
        "all": REPO_ROOT / f"all.{env}.json",
    }


def discover_csvs() -> list[str]:
    """List candidate request CSVs (sop/*.csv), template last."""
    csvs = sorted(str(p.relative_to(REPO_ROOT)) for p in SOP_DIR.glob("*.csv"))
    csvs.sort(key=lambda p: p.endswith("new_market_template.csv"))
    return csvs


def regenerate(env: str, paths: dict[str, Path]) -> None:
    """Reuse the existing downstream generators (kline + all) for one env."""
    py = sys.executable
    kline_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "generate_kline.py"),
        "--input",
        str(paths["aggr"]),
        "--output",
        str(paths["kline"]),
    ]
    all_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "generate_all.py"),
        "--input",
        str(paths["aggr"]),
        "--output",
        str(paths["all"]),
    ]
    # chainId: 97 for testnet, 56 otherwise (local and mainnet both use BSC mainnet id).
    if env == "testnet":
        all_cmd.append("--testnet")
    print(f"\n→ regenerating kline.{env}.json")
    subprocess.run(kline_cmd, check=True, cwd=REPO_ROOT)
    print(f"→ regenerating all.{env}.json")
    subprocess.run(all_cmd, check=True, cwd=REPO_ROOT)


def load_or_empty(path: Path) -> dict:
    """Load a config, treating a not-yet-existing file as an empty symbol list.

    A brand-new environment (e.g. the first mainnet run) has no oracle configs yet;
    the SOP creates them from scratch instead of failing.
    """
    if not path.exists():
        return {"symbols": []}
    return load_json(str(path))


def missing_configs(env: str) -> list[str]:
    """Names of the source-of-truth configs that don't exist yet for this env."""
    paths = env_paths(env)
    return [str(paths[k].relative_to(REPO_ROOT)) for k in ("pyth", "cex", "aggr") if not paths[k].exists()]


def process_env(env: str, rows, lazer_to_feed, apply: bool) -> bool:
    """Fill the three configs for one env. Returns True if anything was added."""
    paths = env_paths(env)
    print(f"\n{'='*60}\n{env.upper()}\n{'='*60}")

    absent = missing_configs(env)
    if absent:
        print(f"  ⚠ config(s) not present yet, will be created: {', '.join(absent)}")

    chain_id = synthetic_chain_id(env)
    print(f"  synthetic addr chainId: {chain_id}")

    pyth_cfg = load_or_empty(paths["pyth"])
    cex_cfg = load_or_empty(paths["cex"])
    aggr_cfg = load_or_empty(paths["aggr"])

    # Single kp allocator seeded from the master (aggr) distribution; the same kp
    # is then written to pyth/cex/aggr for each symbol.
    kp_alloc = KpAllocator(aggr_cfg.get("symbols", []))

    res_pyth = fill_pyth(pyth_cfg, rows, lazer_to_feed, kp_alloc)
    res_cex = fill_cex(cex_cfg, rows, kp_alloc)
    res_aggr = fill_aggr(aggr_cfg, rows, lazer_to_feed, kp_alloc, chain_id)

    for res in (res_pyth, res_cex, res_aggr):
        line = f"  {res.file:5s}  +{len(res.added)} added"
        if res.skipped:
            line += f", {len(res.skipped)} already present"
        if res.missing_feed:
            line += f", {len(res.missing_feed)} with NULL feed_id"
        print(line)
    if res_aggr.added:
        print("  new markets:")
        for row in rows:
            if row.symbol in res_aggr.added:
                kp = kp_alloc.symbol_kp[row.symbol]
                src = f"binance:{row.binance_symbol}" if row.has_cex else "pyth-only"
                feed = lazer_to_feed.get(row.pyth_lazer_id) or "NULL"
                addr = derive_synthetic_addr(row.symbol, chain_id)
                print(f"    {row.symbol:14s} kp={kp} {src:24s} feed_id={feed}")
                print(f"    {'':14s} addr={addr}")

    if res_aggr.missing_feed:
        print(f"  ⚠ NULL feed_id (no hermes feed for lazer id): {', '.join(res_aggr.missing_feed)}")

    if not apply:
        return bool(res_aggr.added or res_cex.added or res_pyth.added)

    dump_json(str(paths["pyth"]), pyth_cfg)
    dump_json(str(paths["cex"]), cex_cfg)
    dump_json(str(paths["aggr"]), aggr_cfg)
    print(f"  ✓ wrote oracle/{{pyth,cex,aggr}}.{env}.json")
    regenerate(env, paths)
    return bool(res_aggr.added or res_cex.added or res_pyth.added)


def copy_to_clipboard(text: str) -> bool:
    """Copy text to the macOS clipboard via pbcopy. Returns True on success."""
    pbcopy = shutil.which("pbcopy")
    if pbcopy is None:
        return False
    try:
        subprocess.run([pbcopy], input=text.encode("utf-8"), check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def extract_symbols(csv_path: str, csv_choice: str) -> None:
    """Print the CSV's symbols as a JSON array; offer to copy it to the clipboard.

    Read-only: touches no config files. Output is e.g. ["AERO/USD", "BILL/USD"].
    """
    try:
        rows = parse_csv(csv_path)
    except ValueError as e:
        print(f"CSV error: {e}")
        sys.exit(1)
    if not rows:
        print("CSV has no data rows.")
        sys.exit(1)

    payload = json.dumps([row.symbol for row in rows], ensure_ascii=False)
    print(f"\n{len(rows)} symbol(s) from {csv_choice}:\n")
    print(payload)

    if shutil.which("pbcopy") is None:
        print("\n(pbcopy not found — skipping clipboard copy.)")
        return
    if questionary.confirm("Copy to clipboard (pbcopy)?", default=True).ask():
        print("✓ Copied to clipboard." if copy_to_clipboard(payload) else "Failed to copy to clipboard.")


def main() -> None:
    print("HertzFlow — New Market SOP\n")

    csvs = discover_csvs()
    if not csvs:
        print("No CSV found under sop/. Copy sop/new_market_template.csv and fill it in.")
        sys.exit(1)

    csv_choice = questionary.select("Request CSV:", choices=csvs).ask()
    if csv_choice is None:
        sys.exit(0)
    csv_path = str(REPO_ROOT / csv_choice)

    action = questionary.select(
        "Action:",
        choices=[
            questionary.Choice("Fill configs + regenerate (full SOP)", value="fill"),
            questionary.Choice("Extract symbols → JSON array (no config changes)", value="symbols"),
        ],
    ).ask()
    if action is None:
        sys.exit(0)
    if action == "symbols":
        extract_symbols(csv_path, csv_choice)
        return

    # mainnet is left unchecked by default — it is production, opt in explicitly.
    env_choice = questionary.checkbox(
        "Target environment(s):",
        choices=[
            questionary.Choice("local", checked=True),
            questionary.Choice("testnet", checked=True),
            questionary.Choice("mainnet", checked=False),
        ],
    ).ask()
    if not env_choice:
        print("No environment selected, aborting.")
        sys.exit(0)

    try:
        rows = parse_csv(csv_path)
    except ValueError as e:
        print(f"CSV error: {e}")
        sys.exit(1)
    if not rows:
        print("CSV has no data rows.")
        sys.exit(1)
    print(f"\nParsed {len(rows)} market(s) from {csv_choice}.")

    print("Fetching Pyth Pro feeds to resolve feed_id by pyth_lazer_id ...")
    try:
        lazer_to_feed = build_lazer_to_feed(fetch_pyth_pro_feeds())
    except Exception as e:  # network/API failure — surface it, don't write blindly
        print(f"Failed to fetch Pyth Pro feeds: {e}")
        sys.exit(1)

    selected = [e for e in ENVS if e in env_choice]

    # 1) Dry-run preview for all selected envs.
    print("\n── DRY RUN (no files written) ──")
    any_changes = False
    for env in selected:
        if process_env(env, rows, lazer_to_feed, apply=False):
            any_changes = True

    if not any_changes:
        print("\nNothing to add — every symbol already present. Done.")
        sys.exit(0)

    # 2) Confirm, then apply + regenerate.
    if not questionary.confirm(
        f"Apply these changes to {', '.join(selected)} and regenerate kline/all?", default=False
    ).ask():
        print("Aborted. No files changed.")
        sys.exit(0)

    # Production envs get a second, explicit confirmation.
    prod = [e for e in selected if e in PROD_ENVS]
    if prod and not questionary.confirm(
        f"⚠ {', '.join(prod)} is PRODUCTION. Really write {', '.join(prod)} configs?", default=False
    ).ask():
        print("Aborted. No files changed.")
        sys.exit(0)

    print("\n── APPLYING ──")
    for env in selected:
        process_env(env, rows, lazer_to_feed, apply=True)

    print("\n✅ Done. Review `git diff` before committing.")


if __name__ == "__main__":
    main()
