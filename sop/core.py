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

Design notes (intentionally a fresh implementation, not an import of scripts/*):
  - feed_id is looked up from the Pyth Pro API by pyth_lazer_id (hermes_id, hex).
  - kp is assigned ONCE per symbol and written identically to pyth/cex/aggr, so a
    market lands on the same partition everywhere. kp=0 is reserved for specials;
    new symbols rotate over 1..MAX_KP by filling the least-loaded partition first
    (deterministic, keeps the existing balance). A symbol already in aggr reuses
    its current kp.
  - Every fill is idempotent: a symbol already present in a file is left untouched.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from urllib.request import Request, urlopen

# Synthetic BSC token address derivation. Must match scripts/get_synthetic_token_addr.ts:
#   "0x" + keccak256(abi.encode(["uint256","string"], [chainId, symbol]))[12:]
# chainId is hard-coded to BSC testnet (97) there and the on-chain addresses in
# aggr.*.json were derived with 97, so we use 97 for BOTH local and testnet.
from eth_abi import encode as abi_encode
from eth_utils import keccak

SYNTHETIC_CHAIN_ID = 97

PYTH_PRO_PRICE_FEEDS_URL = "https://pyth.dourolabs.app/v1/symbols"

# kp rotation: kp=0 is reserved for special symbols (e.g. WETH/USD, CRV/USD); new
# markets rotate over 1..MAX_KP.
MAX_KP = 6

# aggr per-symbol defaults for a freshly added market (mirrors the existing records).
AGGR_DEFAULT_PRECISION = 18
AGGR_DEFAULT_ORACLE_TYPE = "one-percent-per-minute"


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


def fill_aggr(aggr_cfg: dict, rows: list[MarketRow], lazer_to_feed: dict, kp_alloc: KpAllocator) -> FillResult:
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
                "bsc_token_addr": derive_synthetic_addr(row.symbol),
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


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: str, data: dict) -> None:
    """Write JSON matching the repo style: 4-space indent, no ASCII escaping, trailing newline."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")
