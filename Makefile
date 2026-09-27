.PHONY: gen-kline-local gen-kline-testnet gen-kline-mainnet
gen-kline-local:
	@./scripts/generate_kline.py --input ./oracle/aggr.local.json --output ./kline/kline.local.json

gen-kline-testnet:
	@./scripts/generate_kline.py --input ./oracle/aggr.testnet.json --output ./kline/kline.testnet.json

gen-kline-mainnet:
	@./scripts/generate_kline.py --input ./oracle/aggr.mainnet.json --output ./kline/kline.mainnet.json

.PHONY: gen-all-local gen-all-testnet gen-all-mainnet
# SECURITY / DATA-INTEGRITY: chainId is stated EXPLICITLY for every target.
# `gen-all-local` previously omitted --testnet, so it fell through to the old
# `chainId = 97 if args.testnet else 56` default and stamped BSC MAINNET (56)
# onto the addresses in oracle/aggr.local.json — which are testnet-derived
# (core.py derives local with chainId 97). The published all.local.json
# therefore carried 65 testnet addresses under mainnet chainId. Consumers
# resolving by chainId pointed at contracts that do not exist on mainnet.
gen-all-local:
	@./scripts/generate_all.py --input oracle/aggr.local.json --output all.local.json --chain-id 97

gen-all-testnet:
	@./scripts/generate_all.py --input oracle/aggr.testnet.json --output all.testnet.json --chain-id 97

# mainnet is BSC chainId 56 (stated explicitly, not inferred from a missing flag).
gen-all-mainnet:
	@./scripts/generate_all.py --input oracle/aggr.mainnet.json --output all.mainnet.json --chain-id 56

.PHONY: check-pyth-config-local check-pyth-config-testnet check-pyth-config-mainnet
check-pyth-config-local:
	@./scripts/check_pyth_config.py --file oracle/pyth.local.json

check-pyth-config-testnet:
	@./scripts/check_pyth_config.py --file oracle/pyth.testnet.json

check-pyth-config-mainnet:
	@./scripts/check_pyth_config.py --file oracle/pyth.mainnet.json

# New-market SOP: interactive TUI that fills pyth/cex/aggr from a request CSV
# and regenerates kline/all. Bootstraps its own ./.venv on first run.
.PHONY: sop
sop:
	@./sop/sop.py
