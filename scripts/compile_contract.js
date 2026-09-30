const fs = require("node:fs");
const path = require("node:path");
const solc = require("solc");

function compile() {
  const contractPath = path.join(__dirname, "..", "contracts", "PulseActionProofRegistry.sol");
  const source = fs.readFileSync(contractPath, "utf8");
  const input = {
    language: "Solidity",
    sources: { "PulseActionProofRegistry.sol": { content: source } },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      evmVersion: "prague",
      metadata: { bytecodeHash: "ipfs" },
      outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } }
    }
  };
  const output = JSON.parse(solc.compile(JSON.stringify(input)));
  const errors = (output.errors || []).filter((x) => x.severity === "error");
  if (errors.length) throw new Error(errors.map((x) => x.formattedMessage).join("\n"));
  return output.contracts["PulseActionProofRegistry.sol"].PulseActionProofRegistry;
}

module.exports = { compile };
