import { ethers } from "ethers";

const BSC_TESTNET_CHAIN_ID = 97;

function getSyntheticTokenAddress(chainId: number, tokenSymbol: string) {
  return "0x" + hashData(["uint256", "string"], [chainId, tokenSymbol]).substring(26);
}

function hashData(dataTypes: any, dataValues: any) {
  const bytes = ethers.utils.defaultAbiCoder.encode(dataTypes, dataValues);
  const hash = ethers.utils.keccak256(ethers.utils.arrayify(bytes));

  return hash;
}

// read from args
var symbol = process.argv[2];

const syntheticTokenAddress = getSyntheticTokenAddress(BSC_TESTNET_CHAIN_ID, symbol);
console.log(syntheticTokenAddress);
