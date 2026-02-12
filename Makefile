.PHONY: gen-kline-local gen-kline-testnet
gen-kline-local:
	@./scripts/generate_kline.py --input ./oracle/aggr.local.json --output ./kline/kline.local.json

gen-kline-testnet:
	@./scripts/generate_kline.py --input ./oracle/aggr.testnet.json --output ./kline/kline.testnet.json

.PHONY: gen-all-local gen-all-testnet
gen-all-local:
	@./scripts/generate_all.py --input oracle/aggr.local.json --output all.local.json

gen-all-testnet:
	@./scripts/generate_all.py --input oracle/aggr.testnet.json --output all.testnet.json --testnet
