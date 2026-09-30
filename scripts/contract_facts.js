// Compile PulseActionProofRegistry and write UNSIGNED Arc mainnet plans.
// Never signs, never broadcasts, never reads a key, makes no network calls.
//
//   node scripts/contract_facts.js                        # deployer = Pulse wallet (owner decision), start nonce 0
//   node scripts/contract_facts.js --deployer 0x.. --nonce N
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const solc = require("solc");
const { keccak256, AbiCoder, getAddress, getCreateAddress, Interface } = require("ethers");
const { compile } = require("./compile_contract");

const CHAIN_ID = 5042;
const IDENTITY_REGISTRY = getAddress("0x8004a169fb4a3325136eb29fa0ceb6d2e539a432");
const PULSE_AGENT_WALLET = getAddress("0x765fb7e6a0bdddc29f57eece34aeda0fb318805d");
const BUILDER_WALLET = getAddress("0x6fa3659a15e9264e43ec67fe4bb5d31d38109a42");
const AGENT_URI = "https://chapaevv123.github.io/pulse-arc-radar/agent/erc8004-registration.json";
// Gas: Arc mainnet eth_estimateGas 2026-09-30 (recordProof from the byte-identical testnet twin).
const GAS_ESTIMATE = { deploy: 179515, register: 203545, recordProof: 47257 };
const GAS_BUFFER = 1.2;
const FEES = { maxFeePerGas: 40_000_000_000n, maxPriorityFeePerGas: 1_000_000_000n };   // 40 gwei cap, 1 gwei tip
const limit = (g) => Math.ceil((g * GAS_BUFFER) / 1000) * 1000;
const usdc = (wei) => (Number(wei) / 1e18).toFixed(6);

const arg = (name) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : null; };
const deployer = getAddress(arg("--deployer") || PULSE_AGENT_WALLET);
const startNonce = Number(arg("--nonce") || 0);
if (deployer === BUILDER_WALLET) throw new Error("BUILDER_WALLET_IS_ATTRIBUTION_ONLY");
if (!Number.isSafeInteger(startNonce) || startNonce < 0) throw new Error("NONCE_INVALID");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "contracts", "PulseActionProofRegistry.sol"), "utf8");
const compiled = compile();
const creation = "0x" + compiled.evm.bytecode.object;
const runtimeTemplate = "0x" + compiled.evm.deployedBytecode.object;

// Same settings as compile_contract.js, plus immutableReferences, to predict the exact onchain runtime.
const full = JSON.parse(solc.compile(JSON.stringify({
  language: "Solidity",
  sources: { "PulseActionProofRegistry.sol": { content: source } },
  settings: { optimizer: { enabled: true, runs: 200 }, evmVersion: "prague", metadata: { bytecodeHash: "ipfs" },
    outputSelection: { "*": { "*": ["evm.deployedBytecode.object", "evm.deployedBytecode.immutableReferences"] } } }
}))).contracts["PulseActionProofRegistry.sol"].PulseActionProofRegistry.evm.deployedBytecode;
if ("0x" + full.object !== runtimeTemplate) throw new Error("COMPILE_MISMATCH");
let runtimeHex = full.object;
const recorderWord = deployer.slice(2).toLowerCase().padStart(64, "0");
for (const refs of Object.values(full.immutableReferences)) {
  for (const { start, length } of refs) {
    if (length !== 32) throw new Error("IMMUTABLE_LENGTH");
    runtimeHex = runtimeHex.slice(0, start * 2) + recorderWord + runtimeHex.slice((start + length) * 2);
  }
}
const expectedRuntime = "0x" + runtimeHex;

const ctorArgs = AbiCoder.defaultAbiCoder().encode(["address"], [deployer]);
const initCode = creation + ctorArgs.slice(2);
const registryAddress = getCreateAddress({ from: deployer, nonce: startNonce });
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
  runtime_bytecode_keccak256: keccak256(runtimeTemplate),
  runtime_note: "template hash; the immutable authorizedRecorder is embedded at deploy time (see expected_onchain_runtime_keccak256)",
  testnet_predecessor_creation_bytecode_keccak256: testnet.creation_bytecode_keccak256,
  creation_bytecode_matches_testnet: keccak256(creation) === testnet.creation_bytecode_keccak256,
  deployer_recorder_wallet: deployer,
  deployer_role_note: "Owner decision 2026-09-30: the Pulse wallet is agent owner, deployer, recorder and anchor sender",
  deploy_nonce: startNonce,
  predicted_registry_address: registryAddress,
  constructor_argument_slot: ctorArgs,
  deployment_init_code: initCode,
  deployment_init_code_keccak256: keccak256(initCode),
  expected_onchain_runtime: expectedRuntime,
  expected_onchain_runtime_keccak256: keccak256(expectedRuntime),
  expected_deployment_gas: GAS_ESTIMATE.deploy,
  signed: false,
  broadcast: false,
  deployed_address: null
};

const identity = new Interface(["function register(string agentURI) returns (uint256 agentId)"]);
const registry = new Interface(compiled.abi);
const prepared = JSON.parse(fs.readFileSync(path.join(root, "snapshots", "anchors.json"), "utf8")).prepared_anchors || [];
const fee = (gasLimit, expectedGas) => ({
  gas_limit: gasLimit, max_fee_per_gas_wei: FEES.maxFeePerGas.toString(), max_priority_fee_per_gas_wei: FEES.maxPriorityFeePerGas.toString(),
  max_cost_usdc: usdc(BigInt(gasLimit) * FEES.maxFeePerGas),
  expected_cost_usdc_at_20_gwei_base: usdc(BigInt(expectedGas) * (20_000_000_000n + FEES.maxPriorityFeePerGas))
});

const txs = [
  { step: 1, action: "DEPLOY_PulseActionProofRegistry", type: 2, chain_id: CHAIN_ID, from: deployer, nonce: startNonce, to: null, value: "0",
    data: initCode, data_keccak256: keccak256(initCode), expected_gas: GAS_ESTIMATE.deploy, ...fee(limit(GAS_ESTIMATE.deploy), GAS_ESTIMATE.deploy),
    creates: registryAddress },
  { step: 2, action: "ERC8004_REGISTER_PULSE_AGENT", type: 2, chain_id: CHAIN_ID, from: deployer, nonce: startNonce + 1, to: IDENTITY_REGISTRY, value: "0",
    method: "register(string)", selector: identity.getFunction("register").selector, argument: AGENT_URI,
    data: identity.encodeFunctionData("register", [AGENT_URI]), expected_gas: GAS_ESTIMATE.register,
    ...fee(limit(GAS_ESTIMATE.register), GAS_ESTIMATE.register) },
  ...prepared.map((p, i) => ({
    step: 3 + i, action: "ANCHOR_SNAPSHOT", type: 2, chain_id: CHAIN_ID, snapshot_id: p.snapshot_id, from: deployer,
    nonce: startNonce + 2 + i, to: registryAddress, value: "0", method: "recordProof(bytes32,bytes32)",
    selector: registry.getFunction("recordProof").selector, action_id: p.action_id, proof_hash: p.proof_hash,
    data: registry.encodeFunctionData("recordProof", [p.action_id, p.proof_hash]), expected_gas: GAS_ESTIMATE.recordProof,
    ...fee(limit(GAS_ESTIMATE.recordProof), GAS_ESTIMATE.recordProof) }))
];
for (const tx of txs.filter((t) => t.action === "ANCHOR_SNAPSHOT")) {
  if (tx.data !== prepared.find((x) => x.snapshot_id === tx.snapshot_id).record_proof_calldata) throw new Error("CALLDATA_MISMATCH");
}
const totalExpected = txs.reduce((s, t) => s + t.expected_gas, 0);
const totalLimit = txs.reduce((s, t) => s + t.gas_limit, 0);
const txPlan = {
  classification: "UNSIGNED_MAINNET_TRANSACTION_PLAN",
  chain_id: CHAIN_ID,
  rpc: "https://rpc.mainnet.arc.io",
  roles: { builder_wallet: BUILDER_WALLET, pulse_agent_wallet: PULSE_AGENT_WALLET, deployer_recorder_wallet: deployer },
  ordering_rule: "Strict nonce order. The registry address depends on the deploy nonce; if the sender nonce is not exactly " + startNonce + " at step 1, STOP and regenerate the plan.",
  predicted_registry_address: registryAddress,
  transactions: txs,
  totals: { transactions: txs.length, expected_gas: totalExpected, gas_limit_sum: totalLimit,
    expected_cost_usdc_at_20_gwei_base: usdc(BigInt(totalExpected) * 21_000_000_000n),
    max_cost_usdc_at_caps: usdc(BigInt(totalLimit) * FEES.maxFeePerGas) },
  signed: false,
  broadcast: false
};

fs.writeFileSync(path.join(root, "evidence", "mainnet_deployment_plan.json"), JSON.stringify(deployPlan, null, 2) + "\n");
fs.writeFileSync(path.join(root, "evidence", "mainnet_tx_plan.json"), JSON.stringify(txPlan, null, 2) + "\n");
console.log(JSON.stringify({ deployer, deploy_nonce: startNonce, predicted_registry_address: registryAddress,
  creation_bytecode_keccak256: deployPlan.creation_bytecode_keccak256, init_code_keccak256: deployPlan.deployment_init_code_keccak256,
  expected_onchain_runtime_keccak256: deployPlan.expected_onchain_runtime_keccak256, matches_testnet: deployPlan.creation_bytecode_matches_testnet,
  totals: txPlan.totals }, null, 1));
