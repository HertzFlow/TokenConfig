import { ethers } from "ethers";

// The chainId is part of the hash preimage, so it must match the chain the market
// lives on: BSC testnet (97) for local/testnet, BSC mainnet (56) for mainnet.
// Must stay in agreement with derive_synthetic_addr / synthetic_chain_id in sop/core.py.
const BSC_TESTNET_CHAIN_ID = 97;
const BSC_MAINNET_CHAIN_ID = 56;

const CHAIN_ID_BY_ENV: Record<string, number> = {
  local: BSC_TESTNET_CHAIN_ID,
  testnet: BSC_TESTNET_CHAIN_ID,
  mainnet: BSC_MAINNET_CHAIN_ID,
};

function getSyntheticTokenAddress(chainId: number, tokenSymbol: string) {
  return "0x" + hashData(["uint256", "string"], [chainId, tokenSymbol]).substring(26);
}

function hashData(dataTypes: any, dataValues: any) {
  const bytes = ethers.utils.defaultAbiCoder.encode(dataTypes, dataValues);
  const hash = ethers.utils.keccak256(ethers.utils.arrayify(bytes));

  return hash;
}

// Usage: yarn ts-node scripts/get_synthetic_token_addr.ts <SYMBOL> [env]
// env defaults to testnet (chainId 97), preserving the previous behaviour.
const symbol = process.argv[2];
const env = (process.argv[3] || "testnet").toLowerCase();

if (!symbol) {
  console.error("usage: get_synthetic_token_addr.ts <SYMBOL> [local|testnet|mainnet]");
  process.exit(1);
}

const chainId = CHAIN_ID_BY_ENV[env];
if (chainId === undefined) {
  console.error(`unknown env "${env}"; expected one of ${Object.keys(CHAIN_ID_BY_ENV).join(", ")}`);
  process.exit(1);
}

console.log(getSyntheticTokenAddress(chainId, symbol));
