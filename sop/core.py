#!/usr/bin/env python3
"""
Core logic for the new-market SOP. Pure, dependency-light, and TUI-free so it can
be unit-tested and reused.

Given a request CSV (see sop/new_market_template.csv), this module fills the three
oracle source-of-truth configs for one environment:

    oracle/pyth.<env>.json    symbol + feed_id + pyth_lazer_id + kp
    oracle/cex.<env>.json     symbol + binance pair + kp   (only for rows with a binance_symbol)
    oracle/aggr.<env>.json    full master record (synthetic bsc_token_addr, cex_symbol_map, ...)

The downstream files (kline/kline.<env>.json and all.<env>.json) are NOT written here;
they are regenerated from aggr by the existing scripts/generate_kline.py and
scripts/generate_all.py, which sop.py invokes after these fills succeed.

Two modes are supported:

  fill  (add-only)  `fill_pyth` / `fill_cex` / `fill_aggr`
        Append symbols from the CSV that are not present yet. Existing records are
        never touched. This is the historical behaviour.

  sync  (overwrite)  `sync_pyth` / `sync_cex` / `sync_aggr`
        Treat the CSV as the authoritative market list: add what is missing, drop
        what is no longer requested, and bring the CSV-governed fields of existing
        records up to date. An existing record ALWAYS keeps its `kp` (and its
        already-derived `bsc_token_addr`) so partitioning and on-chain addresses
        stay stable. Protected infra symbols are never dropped (see
        SYNC_PROTECTED_SYMBOLS).

Design notes (intentionally a fresh implementation, not an import of scripts/*):
  - feed_id is looked up from the Pyth Pro API by pyth_lazer_id (hermes_id, hex).
  - kp is assigned ONCE per symbol and written identically to pyth/cex/aggr, so a
    market lands on the same partition everywhere. kp=0 is reserved for specials;
    new symbols rotate over 1..MAX_KP by filling the least-loaded partition first
    (deterministic, keeps the existing balance). A symbol already in aggr reuses
    its current kp.
  - Every fill is idempotent: a symbol already present in a file is left untouched.
  - The synthetic bsc_token_addr hashes the chainId, so it is env-dependent: local and
    testnet derive with 97 (BSC testnet), mainnet with 56 (BSC mainnet). The same symbol
    therefore has a DIFFERENT address on mainnet than on local/testnet — by design.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from urllib.request import Request, urlopen

# Synthetic BSC token address derivation. Must match scripts/get_synthetic_token_addr.ts:
#   "0x" + keccak256(abi.encode(["uint256","string"], [chainId, symbol]))[12:]
# The chainId is part of the preimage, so it must match the chain the market lives on:
# BSC testnet (97) for local/testnet, BSC mainnet (56) for mainnet. The existing
# local/testnet addresses in aggr.*.json were derived with 97, so those stay as they are.
from eth_abi import encode as abi_encode
from eth_utils import keccak

BSC_TESTNET_CHAIN_ID = 97
BSC_MAINNET_CHAIN_ID = 56

# Default kept at 97 for backwards compatibility with existing local/testnet records.
SYNTHETIC_CHAIN_ID = BSC_TESTNET_CHAIN_ID

SYNTHETIC_CHAIN_ID_BY_ENV = {
    "local": BSC_TESTNET_CHAIN_ID,
    "testnet": BSC_TESTNET_CHAIN_ID,
    "mainnet": BSC_MAINNET_CHAIN_ID,
}


def synthetic_chain_id(env: str) -> int:
    """chainId used to derive synthetic token addresses for an environment."""
    try:
        return SYNTHETIC_CHAIN_ID_BY_ENV[env]
    except KeyError:
        raise ValueError(f"unknown env {env!r}; expected one of {sorted(SYNTHETIC_CHAIN_ID_BY_ENV)}") from None

PYTH_PRO_PRICE_FEEDS_URL = "https://pyth.dourolabs.app/v1/symbols"

# kp rotation: kp=0 is reserved for special symbols (e.g. WETH/USD, CRV/USD); new
# markets rotate over 1..MAX_KP.
MAX_KP = 6

# aggr per-symbol defaults for a freshly added market (mirrors the existing records).
AGGR_DEFAULT_PRECISION = 18
AGGR_DEFAULT_ORACLE_TYPE = "one-percent-per-minute"

# Symbols sync mode never removes, even when the request CSV omits them. These are
# infrastructure/reference feeds rather than tradable markets: the kp=0 specials
# (WETH/USD, CRV/USD) and the stablecoin references. The first two are also the
# reserved kp=0 partition, and all four are blacklisted in scripts/generate_kline.py.
SYNC_PROTECTED_SYMBOLS = frozenset({"WETH/USD", "CRV/USD", "USDT/USD", "USDC/USD"})


# --------------------------------------------------------------------------- #
# CSV parsing
# --------------------------------------------------------------------------- #
@dataclass
class MarketRow:
    """One requested market, parsed from the request CSV."""

    index: str
    asset_type: str
    symbol: str
    pyth_lazer_id: int
    binance_symbol: str = ""  # empty => pyth-only, no CEX source

    @property
    def has_cex(self) -> bool:
        return bool(self.binance_symbol)


def parse_csv(path: str) -> list[MarketRow]:
    """Parse the request CSV into MarketRow objects.

    Columns: index, asset_type, symbol, pyth_lazer_id, binance_symbol(optional).
    Blank lines and comment/header rows (first cell starts with '#' or equals
    'index') are skipped. Raises ValueError on a malformed row so the operator
    fixes the CSV rather than silently importing garbage.
    """
    rows: list[MarketRow] = []
    seen: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(csv.reader(f), start=1):
            if not raw or not raw[0].strip():
                continue
            first = raw[0].strip().lower()
            if first.startswith("#") or first == "index":
                continue
            if len(raw) < 4:
                raise ValueError(f"line {lineno}: expected >=4 columns, got {len(raw)}: {raw}")
            symbol = raw[2].strip()
            if not symbol:
                raise ValueError(f"line {lineno}: empty symbol")
            try:
                lazer_id = int(raw[3].strip())
            except ValueError as e:
                raise ValueError(f"line {lineno}: bad pyth_lazer_id {raw[3]!r}: {e}") from e
            binance = raw[4].strip() if len(raw) > 4 else ""
            if symbol in seen:
                raise ValueError(f"line {lineno}: duplicate symbol {symbol!r} in CSV")
            seen.add(symbol)
            rows.append(
                MarketRow(
                    index=raw[0].strip(),
                    asset_type=raw[1].strip(),
                    symbol=symbol,
                    pyth_lazer_id=lazer_id,
                    binance_symbol=binance,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# Pyth Pro feed lookup
# --------------------------------------------------------------------------- #
def normalize_hermes_id(hermes_id):
    """Normalize a hermes id to lowercase hex without the 0x prefix (or None)."""
    if hermes_id is None:
        return None
    hermes_id = hermes_id.strip().lower()
    if hermes_id.startswith("0x"):
        hermes_id = hermes_id[2:]
    return hermes_id or None


def fetch_pyth_pro_feeds(url: str = PYTH_PRO_PRICE_FEEDS_URL):
    """Fetch the Pyth Pro symbol list (raw JSON list)."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)


def build_lazer_to_feed(feeds) -> dict[int, str]:
    """Map pyth_lazer_id -> normalized hermes feed_id from the Pyth Pro feed list."""
    mapping: dict[int, str] = {}
    for feed in feeds:
        lazer_id = feed.get("pyth_lazer_id")
        feed_id = normalize_hermes_id(feed.get("hermes_id"))
        if lazer_id is None or feed_id is None:
            continue
        mapping[lazer_id] = feed_id
    return mapping


# --------------------------------------------------------------------------- #
# kp allocation
# --------------------------------------------------------------------------- #
class KpAllocator:
    """Assigns kp values over 1..MAX_KP by continuing the existing rotation.

    Seeded from the master (aggr) config: the next new symbol gets `last_kp % MAX_KP + 1`
    where `last_kp` is the kp of aggr's last symbol — so successive runs append in a
    predictable 1→2→…→MAX_KP→1→… cycle, just like the older scripts/* helpers.
    Symbols already present reuse their existing kp (idempotent re-runs).
    """

    def __init__(self, existing_symbols: list[dict], max_kp: int = MAX_KP):
        self.max_kp = max_kp
        # Remember the kp already assigned to a symbol so re-runs stay stable.
        self.symbol_kp: dict[str, int] = {}
        for s in existing_symbols:
            kp = s.get("kp")
            sym = s.get("symbol")
            if sym is not None and isinstance(kp, int):
                self.symbol_kp[sym] = kp
        # Continue from the kp of aggr's last symbol; ignore reserved kp=0 specials
        # so the rotation lands in 1..MAX_KP even if the tail of aggr is a kp=0 row.
        self._last_kp = 0
        for s in reversed(existing_symbols):
            kp = s.get("kp")
            if isinstance(kp, int) and 1 <= kp <= max_kp:
                self._last_kp = kp
                break

    def kp_for(self, symbol: str) -> int:
        """Return the kp for a symbol, allocating the next in the cycle if needed."""
        if symbol in self.symbol_kp:
            return self.symbol_kp[symbol]
        kp = self._last_kp % self.max_kp + 1
        self._last_kp = kp
        self.symbol_kp[symbol] = kp
        return kp


# --------------------------------------------------------------------------- #
# Synthetic address
# --------------------------------------------------------------------------- #
def derive_synthetic_addr(symbol: str, chain_id: int = SYNTHETIC_CHAIN_ID) -> str:
    """Derive the deterministic synthetic BSC token address for a symbol."""
    return "0x" + keccak(abi_encode(["uint256", "string"], [chain_id, symbol]))[12:].hex()


# --------------------------------------------------------------------------- #
# Config fills (in-place on the loaded dict); each returns added/skipped symbols.
# --------------------------------------------------------------------------- #
@dataclass
class FillResult:
    file: str
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # symbols added with a null feed_id (lazer_id had no hermes feed) — pyth/aggr only
    missing_feed: list[str] = field(default_factory=list)


def fill_pyth(pyth_cfg: dict, rows: list[MarketRow], lazer_to_feed: dict, kp_alloc: KpAllocator) -> FillResult:
    symbols = pyth_cfg.setdefault("symbols", [])
    existing = {s.get("symbol") for s in symbols}
    res = FillResult(file="pyth")
    for row in rows:
        if row.symbol in existing:
            res.skipped.append(row.symbol)
            continue
        feed_id = lazer_to_feed.get(row.pyth_lazer_id)
        if feed_id is None:
            res.missing_feed.append(row.symbol)
        symbols.append(
            {
                "symbol": row.symbol,
                "feed_id": feed_id,
                "kp": kp_alloc.kp_for(row.symbol),
                "pyth_lazer_id": row.pyth_lazer_id,
            }
        )
        existing.add(row.symbol)
        res.added.append(row.symbol)
    return res


def fill_cex(cex_cfg: dict, rows: list[MarketRow], kp_alloc: KpAllocator) -> FillResult:
    symbols = cex_cfg.setdefault("symbols", [])
    existing = {s.get("symbol") for s in symbols}
    res = FillResult(file="cex")
    for row in rows:
        if not row.has_cex:
            continue
        if row.symbol in existing:
            res.skipped.append(row.symbol)
            continue
        symbols.append(
            {
                "symbol": row.symbol,
                "binance": {"symbol": row.binance_symbol, "enabled": True},
                "kp": kp_alloc.kp_for(row.symbol),
            }
        )
        existing.add(row.symbol)
        res.added.append(row.symbol)
    return res


def fill_aggr(
    aggr_cfg: dict,
    rows: list[MarketRow],
    lazer_to_feed: dict,
    kp_alloc: KpAllocator,
    chain_id: int = SYNTHETIC_CHAIN_ID,
) -> FillResult:
    """Fill aggr for one env. `chain_id` selects the synthetic-address chain (97 vs 56)."""
    symbols = aggr_cfg.setdefault("symbols", [])
    existing = {s.get("symbol") for s in symbols}
    res = FillResult(file="aggr")
    for row in rows:
        if row.symbol in existing:
            res.skipped.append(row.symbol)
            continue
        feed_id = lazer_to_feed.get(row.pyth_lazer_id)
        if feed_id is None:
            res.missing_feed.append(row.symbol)
        cex_symbol_map = {"binance": row.symbol} if row.has_cex else {}
        symbols.append(
            {
                "symbol": row.symbol,
                "kp": kp_alloc.kp_for(row.symbol),
                "bsc_precision": AGGR_DEFAULT_PRECISION,
                "bsc_token_addr": derive_synthetic_addr(row.symbol, chain_id),
                "bsc_token_addr_env_map": {},
                "bsc_token_oracle_type": AGGR_DEFAULT_ORACLE_TYPE,
                "pyth_only": not row.has_cex,
                "feed_id": feed_id,
                "cex_symbol_map": cex_symbol_map,
                "need_sign": True,
                "pyth_lazer_id": row.pyth_lazer_id,
            }
        )
        existing.add(row.symbol)
        res.added.append(row.symbol)
    return res


# --------------------------------------------------------------------------- #
# Config sync / overwrite (in-place on the loaded dict)
#
# Sync treats the request CSV as the authoritative market list for an env:
#   - symbols in the CSV but not in the config  -> added
#   - symbols in the config but not in the CSV  -> removed (except SYNC_PROTECTED_SYMBOLS)
#   - symbols in both                           -> CSV-governed fields updated in place
#
# What sync NEVER changes on an existing record:
#   kp                     partition key — rewriting it would move a live market
#   bsc_token_addr         already-derived (or hand-set) synthetic address
#   bsc_precision, bsc_token_addr_env_map, bsc_token_oracle_type, need_sign
#   cex `enabled`          operational toggle, not present in the CSV
#
# Existing records keep their position in the file so the diff stays small; new
# symbols are appended in CSV order.
# --------------------------------------------------------------------------- #
@dataclass
class SyncResult:
    file: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    # symbol -> {field: (old, new)} for records whose CSV-governed fields changed
    updated: dict[str, dict[str, tuple]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    # symbols kept despite being absent from the CSV (SYNC_PROTECTED_SYMBOLS)
    protected: list[str] = field(default_factory=list)
    # symbols written with a null feed_id (lazer_id had no hermes feed) — pyth/aggr only
    missing_feed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.updated)


def _partition_existing(symbols: list[dict], wanted: set[str], protected: frozenset[str]):
    """Split existing records into (kept, removed_symbols, protected_symbols).

    `kept` preserves file order and holds the same dict objects, so callers can
    mutate them in place. Records with no/blank symbol are kept untouched.
    """
    kept: list[dict] = []
    removed: list[str] = []
    kept_protected: list[str] = []
    for rec in symbols:
        sym = rec.get("symbol")
        if not sym:
            kept.append(rec)
            continue
        if sym in wanted:
            kept.append(rec)
        elif sym in protected:
            kept.append(rec)
            kept_protected.append(sym)
        else:
            removed.append(sym)
    return kept, removed, kept_protected


def _apply_updates(rec: dict, desired: dict, res: SyncResult, symbol: str) -> None:
    """Set CSV-governed fields on an existing record, recording what changed."""
    diff: dict[str, tuple] = {}
    for key, new in desired.items():
        old = rec.get(key)
        if old != new:
            diff[key] = (old, new)
            rec[key] = new
    if diff:
        res.updated[symbol] = diff
    else:
        res.unchanged.append(symbol)


def sync_pyth(pyth_cfg: dict, rows: list[MarketRow], lazer_to_feed: dict, kp_alloc: KpAllocator) -> SyncResult:
    """Make oracle/pyth.<env>.json match the CSV. Existing records keep their kp."""
    symbols = pyth_cfg.setdefault("symbols", [])
    wanted = {row.symbol for row in rows}
    res = SyncResult(file="pyth")

    kept, res.removed, res.protected = _partition_existing(symbols, wanted, SYNC_PROTECTED_SYMBOLS)
    by_symbol = {rec["symbol"]: rec for rec in kept if rec.get("symbol")}

    for row in rows:
        feed_id = lazer_to_feed.get(row.pyth_lazer_id)
        if feed_id is None:
            res.missing_feed.append(row.symbol)
        rec = by_symbol.get(row.symbol)
        if rec is None:
            kept.append(
                {
                    "symbol": row.symbol,
                    "feed_id": feed_id,
                    "kp": kp_alloc.kp_for(row.symbol),
                    "pyth_lazer_id": row.pyth_lazer_id,
                }
            )
            res.added.append(row.symbol)
            continue
        # kp is deliberately absent from the desired dict — never rewritten.
        _apply_updates(rec, {"feed_id": feed_id, "pyth_lazer_id": row.pyth_lazer_id}, res, row.symbol)

    pyth_cfg["symbols"] = kept
    return res


def sync_cex(cex_cfg: dict, rows: list[MarketRow], kp_alloc: KpAllocator) -> SyncResult:
    """Make oracle/cex.<env>.json match the CSV's rows that carry a binance_symbol.

    A row that lost its binance_symbol (now pyth-only) has its cex record removed.
    The `enabled` flag on an existing record is left alone — it is an operational
    toggle the CSV has no column for.
    """
    symbols = cex_cfg.setdefault("symbols", [])
    wanted = {row.symbol for row in rows if row.has_cex}
    res = SyncResult(file="cex")

    kept, res.removed, res.protected = _partition_existing(symbols, wanted, SYNC_PROTECTED_SYMBOLS)
    by_symbol = {rec["symbol"]: rec for rec in kept if rec.get("symbol")}

    for row in rows:
        if not row.has_cex:
            continue
        rec = by_symbol.get(row.symbol)
        if rec is None:
            kept.append(
                {
                    "symbol": row.symbol,
                    "binance": {"symbol": row.binance_symbol, "enabled": True},
                    "kp": kp_alloc.kp_for(row.symbol),
                }
            )
            res.added.append(row.symbol)
            continue
        binance = rec.setdefault("binance", {})
        old_pair = binance.get("symbol")
        if old_pair != row.binance_symbol:
            binance["symbol"] = row.binance_symbol
            binance.setdefault("enabled", True)
            res.updated[row.symbol] = {"binance.symbol": (old_pair, row.binance_symbol)}
        else:
            res.unchanged.append(row.symbol)

    cex_cfg["symbols"] = kept
    return res


def sync_aggr(
    aggr_cfg: dict,
    rows: list[MarketRow],
    lazer_to_feed: dict,
    kp_alloc: KpAllocator,
    chain_id: int = SYNTHETIC_CHAIN_ID,
) -> SyncResult:
    """Make oracle/aggr.<env>.json match the CSV. `chain_id` selects the synthetic chain.

    Existing records keep kp, bsc_token_addr and the other non-CSV fields; only
    feed_id, pyth_lazer_id, pyth_only and cex_symbol_map are brought up to date.
    """
    symbols = aggr_cfg.setdefault("symbols", [])
    wanted = {row.symbol for row in rows}
    res = SyncResult(file="aggr")

    kept, res.removed, res.protected = _partition_existing(symbols, wanted, SYNC_PROTECTED_SYMBOLS)
    by_symbol = {rec["symbol"]: rec for rec in kept if rec.get("symbol")}

    for row in rows:
        feed_id = lazer_to_feed.get(row.pyth_lazer_id)
        if feed_id is None:
            res.missing_feed.append(row.symbol)
        cex_symbol_map = {"binance": row.symbol} if row.has_cex else {}
        rec = by_symbol.get(row.symbol)
        if rec is None:
            kept.append(
                {
                    "symbol": row.symbol,
                    "kp": kp_alloc.kp_for(row.symbol),
                    "bsc_precision": AGGR_DEFAULT_PRECISION,
                    "bsc_token_addr": derive_synthetic_addr(row.symbol, chain_id),
                    "bsc_token_addr_env_map": {},
                    "bsc_token_oracle_type": AGGR_DEFAULT_ORACLE_TYPE,
                    "pyth_only": not row.has_cex,
                    "feed_id": feed_id,
                    "cex_symbol_map": cex_symbol_map,
                    "need_sign": True,
                    "pyth_lazer_id": row.pyth_lazer_id,
                }
            )
            res.added.append(row.symbol)
            continue
        _apply_updates(
            rec,
            {
                "pyth_only": not row.has_cex,
                "feed_id": feed_id,
                "cex_symbol_map": cex_symbol_map,
                "pyth_lazer_id": row.pyth_lazer_id,
            },
            res,
            row.symbol,
        )

    aggr_cfg["symbols"] = kept
    return res


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: str, data: dict) -> None:
    """Write JSON matching the repo style: 4-space indent, no ASCII escaping, trailing newline."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")
