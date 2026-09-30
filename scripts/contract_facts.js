// Compile PulseActionProofRegistry and write UNSIGNED Arc mainnet plans.
// Never signs, never broadcasts, never reads a key, makes no network calls.
//
//   node scripts/contract_facts.js                      # deployer pending -> constructor slot template
//   node scripts/contract_facts.js --deployer 0xABC...  # fills the constructor slot (public address only)
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const solc = require("solc");
const { keccak256, AbiCoder, getAddress, Interface } = require("ethers");
const { compile } = require("./compile_contract");

const CHAIN_ID = 5042;
const IDENTITY_REGISTRY = getAddress("0x8004a169fb4a3325136eb29fa0ceb6d2e539a432");
const PULSE_AGENT_WALLET = getAddress("0x765fb7e6a0bdddc29f57eece34aeda0fb318805d");
const BUILDER_WALLET = getAddress("0x6fa3659a15e9264e43ec67fe4bb5d31d38109a42");
const AGENT_URI = "https://chapaevv123.github.io/pulse-arc-radar/agent/erc8004-registration.json";
const SLOT = "000000000000000000000000<DEPLOYER_ADDRESS_20_BYTES_NO_0x>";

const root = path.join(__dirname, "..");
const argIdx = process.argv.indexOf("--deployer");
const deployer = argIdx > 0 ? getAddress(process.argv[argIdx + 1]) : null;
if (deployer && [PULSE_AGENT_WALLET, BUILDER_WALLET].includes(deployer)) {
  throw new Error("DEPLOYER_MUST_BE_SEPARATE_WALLET");
}

const source = fs.readFileSync(path.join(root, "contracts", "PulseActionProofRegistry.sol"));
const compiled = compile();
const creation = "0x" + compiled.evm.bytecode.object;
const runtime = "0x" + compiled.evm.deployedBytecode.object;
const ctorArgs = deployer ? AbiCoder.defaultAbiCoder().encode(["address"], [deployer]) : null;
const initCode = deployer ? creation + ctorArgs.slice(2) : null;
const testnet = JSON.parse(fs.readFileSync(path.join(root, "evidence", "testnet_predecessor", "deployment_preparation.json"), "utf8"));

const deployPlan = {
  classification: "UNSIGNED_MAINNET_DEPLOYMENT_PLAN",
  network: "arc_mainnet",
  chain_id: CHAIN_ID,
  contract_name: "PulseActionProofRegistry",
  source_file: "contracts/PulseActionProofRegistry.sol",
  source_sha256: crypto.createHash("sha256").update(source).digest("hex"),
  compiler_version_full: solc.version(),
  optimizer: { enabled: true, runs: 200 },
  evm_version: "prague",
  metadata_bytecode_hash: "ipfs",
  constructor_abi: compiled.abi.find((x) => x.type === "constructor"),
  contract_abi: compiled.abi,
  creation_bytecode: creation,
  creation_bytecode_keccak256: keccak256(creation),
  creation_bytecode_length_bytes: (creation.length - 2) / 2,
  runtime_bytecode_keccak256: keccak256(runtime),
  runtime_bytecode_length_bytes: (runtime.length - 2) / 2,
  runtime_note: "runtime hash is the template; immutable authorizedRecorder is embedded at deploy time, so onchain runtime differs by the recorder address",
  testnet_predecessor_creation_bytecode_keccak256: testnet.creation_bytecode_keccak256,
  creation_bytecode_matches_testnet: keccak256(creation) === testnet.creation_bytecode_keccak256,
  deployer_recorder_wallet: deployer || "PENDING_OWNER",
  constructor_argument_slot: deployer ? ctorArgs : "0x" + SLOT,
  deployment_data_template: "creation_bytecode || " + SLOT,
  deployment_init_code: initCode,
  deployment_init_code_keccak256: initCode ? keccak256(initCode) : null,
  expected_deployment_gas: 179515,
  expected_deployment_gas_source: "Arc mainnet eth_estimateGas 2026-09-30 (placeholder recorder; testnet actual 177,171). Re-estimate in pre-flight",
  signed: false,
  broadcast: false,
  deployed_address: null
};

const identity = new Interface(["function register(string agentURI) returns (uint256 agentId)"]);
const registry = new Interface(compiled.abi);
const anchorsDoc = JSON.parse(fs.readFileSync(path.join(root, "snapshots", "anchors.json"), "utf8"));
const prepared = anchorsDoc.prepared_anchors || [];

const txPlan = {
  classification: "UNSIGNED_MAINNET_TRANSACTION_PLAN",
  chain_id: CHAIN_ID,
  rpc: "https://rpc.mainnet.arc.io",
  roles: { builder_wallet: BUILDER_WALLET, pulse_agent_wallet: PULSE_AGENT_WALLET, deployer_recorder_wallet: deployer || "PENDING_OWNER" },
  transactions: [
    {
      step: 1, action: "DEPLOY_PulseActionProofRegistry", from: deployer || "PENDING_OWNER(separate deployer)", to: null, value: "0",
      data: initCode || ("creation_bytecode || " + SLOT),
      creation_bytecode_keccak256: deployPlan.creation_bytecode_keccak256,
      init_code_keccak256: deployPlan.deployment_init_code_keccak256,
      expected_gas: 179515
    },
    {
      step: 2, action: "ERC8004_REGISTER_PULSE_AGENT", from: PULSE_AGENT_WALLET, to: IDENTITY_REGISTRY, value: "0",
      method: "register(string)", selector: identity.getFunction("register").selector, argument: AGENT_URI,
      data: identity.encodeFunctionData("register", [AGENT_URI]),
      expected_gas: 203545,
      expected_gas_source: "Arc mainnet eth_estimateGas from the Pulse wallet 2026-09-30; eth_call simulation succeeded (would mint agentId 1355 at that block)"
    },
    ...prepared.map((p, i) => ({
      step: 3 + i, action: "ANCHOR_SNAPSHOT", snapshot_id: p.snapshot_id, from: deployer || "PENDING_OWNER(recorder = deployer)",
      to: "PENDING_DEPLOYED_REGISTRY_ADDRESS", value: "0", method: "recordProof(bytes32,bytes32)",
      selector: registry.getFunction("recordProof").selector,
      action_id: p.action_id, proof_hash: p.proof_hash,
      data: registry.encodeFunctionData("recordProof", [p.action_id, p.proof_hash]),
      expected_gas: 47257,
      expected_gas_source: "eth_estimateGas on the byte-identical Arc Testnet twin 0x1607...4Efa (first anchor for an actionId)"
    }))
  ],
  fee_estimate: { base_fee_gwei: 20, total_gas: 179515 + 203545 + 47257 * prepared.length,
    total_usdc_at_20_gwei: ((179515 + 203545 + 47257 * prepared.length) * 20e9 / 1e18).toFixed(6),
    note: "USDC is Arc's native gas token; budget 2x for fee headroom" },
  signed: false,
  broadcast: false
};
for (const tx of txPlan.transactions.filter((t) => t.action === "ANCHOR_SNAPSHOT")) {
  const p = prepared.find((x) => x.snapshot_id === tx.snapshot_id);
  if (tx.data !== p.record_proof_calldata) throw new Error("CALLDATA_MISMATCH:" + tx.snapshot_id);
}

fs.writeFileSync(path.join(root, "evidence", "mainnet_deployment_plan.json"), JSON.stringify(deployPlan, null, 2) + "\n");
fs.writeFileSync(path.join(root, "evidence", "mainnet_tx_plan.json"), JSON.stringify(txPlan, null, 2) + "\n");
console.log(JSON.stringify({
  creation_bytecode_keccak256: deployPlan.creation_bytecode_keccak256,
  runtime_bytecode_keccak256: deployPlan.runtime_bytecode_keccak256,
  matches_testnet: deployPlan.creation_bytecode_matches_testnet,
  deployer: deployPlan.deployer_recorder_wallet,
  register_calldata_bytes: (txPlan.transactions[1].data.length - 2) / 2,
  anchors_prepared: prepared.length
}, null, 1));
