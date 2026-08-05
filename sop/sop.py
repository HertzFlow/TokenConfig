#!/usr/bin/env python3
"""
New-market SOP — interactive entry point.

Run it with the repo's system Python; it bootstraps a local ./.venv (installing
sop/requirements.txt) and re-executes itself inside that venv, so the operator only
ever types `make sop` / `./sop/sop.py`.

The flow, per the SOP:
  1. Pick a request CSV (sop/new_market_template.csv format).
  2. Pick the write mode: add-only (fill) or overwrite (sync).
  3. Pick target environment(s): any of local / testnet / mainnet.
  4. Fill/sync the three source-of-truth oracle configs from the CSV:
        oracle/pyth.<env>.json, oracle/cex.<env>.json, oracle/aggr.<env>.json
  5. Regenerate the derived files with the existing, reused scripts:
        scripts/generate_kline.py  -> kline/kline.<env>.json
        scripts/generate_all.py    -> all.<env>.json   (--testnet for testnet)

Write modes:
  fill  Add-only. Symbols already in a config are left untouched; nothing is ever
        removed. Use this to append new markets.
  sync  Overwrite. The CSV becomes the authoritative market list: missing symbols
        are added, symbols absent from the CSV are REMOVED, and shared symbols get
        their CSV-governed fields refreshed. Existing records keep their `kp` and
        their already-derived `bsc_token_addr`. Infra symbols (WETH/CRV/USDT/USDC)
        are never removed.

Everything is previewed and confirmed before any file is written (dry-run first).
Sync additionally lists every symbol it would delete and asks a separate
confirmation for the deletions.
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
    SYNC_PROTECTED_SYMBOLS,
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
    sync_aggr,
    sync_cex,
    sync_pyth,
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


def _print_new_markets(rows, added: list[str], kp_alloc, lazer_to_feed, chain_id: int) -> None:
    """Detail lines for symbols being added (kp, price source, feed_id, synthetic addr)."""
    added_set = set(added)
    print("  new markets:")
    for row in rows:
        if row.symbol not in added_set:
            continue
        kp = kp_alloc.symbol_kp[row.symbol]
        src = f"binance:{row.binance_symbol}" if row.has_cex else "pyth-only"
        feed = lazer_to_feed.get(row.pyth_lazer_id) or "NULL"
        addr = derive_synthetic_addr(row.symbol, chain_id)
        print(f"    {row.symbol:14s} kp={kp} {src:24s} feed_id={feed}")
        print(f"    {'':14s} addr={addr}")


def process_env_fill(env: str, rows, lazer_to_feed, chain_id: int, paths, apply: bool) -> tuple[bool, list[str]]:
    """Add-only fill of the three configs. Returns (changed, removed=[])."""
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
        _print_new_markets(rows, res_aggr.added, kp_alloc, lazer_to_feed, chain_id)
    if res_aggr.missing_feed:
        print(f"  ⚠ NULL feed_id (no hermes feed for lazer id): {', '.join(res_aggr.missing_feed)}")

    changed = bool(res_aggr.added or res_cex.added or res_pyth.added)
    if apply:
        _write_configs(env, paths, pyth_cfg, cex_cfg, aggr_cfg)
    return changed, []


def process_env_sync(env: str, rows, lazer_to_feed, chain_id: int, paths, apply: bool) -> tuple[bool, list[str]]:
    """Overwrite sync of the three configs. Returns (changed, symbols removed from aggr)."""
    pyth_cfg = load_or_empty(paths["pyth"])
    cex_cfg = load_or_empty(paths["cex"])
    aggr_cfg = load_or_empty(paths["aggr"])

    # Seeded from the current aggr, so existing symbols keep their kp and only
    # genuinely new symbols consume the next slot in the rotation.
    kp_alloc = KpAllocator(aggr_cfg.get("symbols", []))

    res_pyth = sync_pyth(pyth_cfg, rows, lazer_to_feed, kp_alloc)
    res_cex = sync_cex(cex_cfg, rows, kp_alloc)
    res_aggr = sync_aggr(aggr_cfg, rows, lazer_to_feed, kp_alloc, chain_id)

    for res in (res_pyth, res_cex, res_aggr):
        line = f"  {res.file:5s}  +{len(res.added)} added, -{len(res.removed)} removed"
        line += f", ~{len(res.updated)} updated, {len(res.unchanged)} unchanged"
        if res.protected:
            line += f", {len(res.protected)} kept (protected)"
        if res.missing_feed:
            line += f", {len(res.missing_feed)} with NULL feed_id"
        print(line)

    if res_aggr.added:
        _print_new_markets(rows, res_aggr.added, kp_alloc, lazer_to_feed, chain_id)

    if res_aggr.removed:
        print(f"  markets to REMOVE ({len(res_aggr.removed)}):")
        for chunk in _chunk(sorted(res_aggr.removed), 6):
            print(f"    {', '.join(chunk)}")
    if res_cex.removed:
        print(f"  cex entries to remove ({len(res_cex.removed)}): {', '.join(sorted(res_cex.removed))}")
    if res_aggr.protected:
        print(f"  kept despite not being in the CSV (protected): {', '.join(sorted(res_aggr.protected))}")

    for res in (res_pyth, res_cex, res_aggr):
        if not res.updated:
            continue
        print(f"  {res.file} field updates ({len(res.updated)}), kp preserved:")
        for sym, diff in res.updated.items():
            for fname, (old, new) in diff.items():
                print(f"    {sym:14s} {fname}: {_fmt(old)} → {_fmt(new)}")

    if res_aggr.missing_feed:
        print(f"  ⚠ NULL feed_id (no hermes feed for lazer id): {', '.join(res_aggr.missing_feed)}")

    changed = res_pyth.changed or res_cex.changed or res_aggr.changed
    if apply:
        _write_configs(env, paths, pyth_cfg, cex_cfg, aggr_cfg)
    return changed, sorted(res_aggr.removed)


def _fmt(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _write_configs(env: str, paths, pyth_cfg: dict, cex_cfg: dict, aggr_cfg: dict) -> None:
    dump_json(str(paths["pyth"]), pyth_cfg)
    dump_json(str(paths["cex"]), cex_cfg)
    dump_json(str(paths["aggr"]), aggr_cfg)
    print(f"  ✓ wrote oracle/{{pyth,cex,aggr}}.{env}.json")
    regenerate(env, paths)


def process_env(env: str, rows, lazer_to_feed, apply: bool, mode: str = "fill") -> tuple[bool, list[str]]:
    """Fill or sync the three configs for one env.

    Returns (changed, removed_symbols); removed is always empty in fill mode.
    """
    paths = env_paths(env)
    label = "SYNC (overwrite)" if mode == "sync" else "FILL (add-only)"
    print(f"\n{'='*60}\n{env.upper()}  ·  {label}\n{'='*60}")

    absent = missing_configs(env)
    if absent:
        print(f"  ⚠ config(s) not present yet, will be created: {', '.join(absent)}")

    chain_id = synthetic_chain_id(env)
    print(f"  synthetic addr chainId: {chain_id}")

    handler = process_env_sync if mode == "sync" else process_env_fill
    return handler(env, rows, lazer_to_feed, chain_id, paths, apply)


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
            questionary.Choice("Add new markets — add-only, nothing removed (fill)", value="fill"),
            questionary.Choice("Overwrite with this CSV — add + REMOVE + update (sync)", value="sync"),
            questionary.Choice("Extract symbols → JSON array (no config changes)", value="symbols"),
        ],
    ).ask()
    if action is None:
        sys.exit(0)
    if action == "symbols":
        extract_symbols(csv_path, csv_choice)
        return

    mode = action
    if mode == "sync":
        print(
            "\nSync mode: this CSV becomes the authoritative market list.\n"
            "  • symbols missing from the configs are added\n"
            "  • symbols not in the CSV are REMOVED (except "
            f"{', '.join(sorted(SYNC_PROTECTED_SYMBOLS))})\n"
            "  • shared symbols keep their kp and bsc_token_addr; only feed_id,\n"
            "    pyth_lazer_id, pyth_only and cex_symbol_map are refreshed"
        )

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
    removals: dict[str, list[str]] = {}
    for env in selected:
        changed, removed = process_env(env, rows, lazer_to_feed, apply=False, mode=mode)
        any_changes = any_changes or changed
        if removed:
            removals[env] = removed

    if not any_changes:
        noun = "in sync with the CSV" if mode == "sync" else "already present"
        print(f"\nNothing to do — every symbol is {noun}. Done.")
        sys.exit(0)

    # 2) Confirm, then apply + regenerate.
    if not questionary.confirm(
        f"Apply these changes to {', '.join(selected)} and regenerate kline/all?", default=False
    ).ask():
        print("Aborted. No files changed.")
        sys.exit(0)

    # Deletions are the irreversible part of sync — confirm them on their own.
    if removals:
        total = sum(len(v) for v in removals.values())
        print(f"\n⚠ {total} market record(s) will be DELETED:")
        for env, syms in removals.items():
            print(f"  {env}: {len(syms)} — {', '.join(syms)}")
        print("  Their kline/all entries disappear too. Clients tracking them will stop getting prices.")
        if not questionary.confirm("Confirm these deletions?", default=False).ask():
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
        process_env(env, rows, lazer_to_feed, apply=True, mode=mode)

    print("\n✅ Done. Review `git diff` before committing.")


if __name__ == "__main__":
    main()
