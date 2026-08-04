.PHONY: gen-kline-local gen-kline-testnet gen-kline-mainnet
gen-kline-local:
	@./scripts/generate_kline.py --input ./oracle/aggr.local.json --output ./kline/kline.local.json

gen-kline-testnet:
	@./scripts/generate_kline.py --input ./oracle/aggr.testnet.json --output ./kline/kline.testnet.json

gen-kline-mainnet:
	@./scripts/generate_kline.py --input ./oracle/aggr.mainnet.json --output ./kline/kline.mainnet.json

.PHONY: gen-all-local gen-all-testnet gen-all-mainnet
gen-all-local:
	@./scripts/generate_all.py --input oracle/aggr.local.json --output all.local.json

gen-all-testnet:
	@./scripts/generate_all.py --input oracle/aggr.testnet.json --output all.testnet.json --testnet

# mainnet omits --testnet, so tokens get chainId 56 (BSC mainnet).
gen-all-mainnet:
	@./scripts/generate_all.py --input oracle/aggr.mainnet.json --output all.mainnet.json

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
