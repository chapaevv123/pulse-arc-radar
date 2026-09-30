const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ganache = require("ganache");
const { BrowserProvider, ContractFactory, id, keccak256, toUtf8Bytes, ZeroHash } = require("ethers");
const { compile } = require("../scripts/compile_contract");

async function fixture() {
  const eip1193 = ganache.provider({ logging: { quiet: true }, chain: { chainId: 1337 } });
  const provider = new BrowserProvider(eip1193);
  const recorder = await provider.getSigner(0);
  const outsider = await provider.getSigner(1);
  const compiled = compile();
  const factory = new ContractFactory(compiled.abi, "0x" + compiled.evm.bytecode.object, recorder);
  const registry = await factory.deploy(await recorder.getAddress());
  await registry.waitForDeployment();
  return { provider, recorder, outsider, registry, compiled };
}

test("deploys locally with immutable recorder and no custody", async () => {
  const { provider, recorder, registry } = await fixture();
  assert.equal(await registry.authorizedRecorder(), await recorder.getAddress());
  assert.equal(await provider.getBalance(await registry.getAddress()), 0n);
});

test("records one proof, stores lookup, and emits exact event", async () => {
  const { recorder, registry } = await fixture();
  const actionId = id("canonical-action");
  const proofHash = id("canonical-proof");
  const receipt = await (await registry.recordProof(actionId, proofHash)).wait();
  const event = receipt.logs.map((x) => { try { return registry.interface.parseLog(x); } catch { return null; } }).find(Boolean);
  assert.equal(event.name, "PulseActionProofRecorded");
  assert.equal(event.args.actionId, actionId);
  assert.equal(event.args.proofHash, proofHash);
  assert.equal(event.args.recorder, await recorder.getAddress());
  assert.ok(event.args.timestamp > 0n);
  assert.equal(await registry.proofHashByAction(actionId), proofHash);
});

test("rejects duplicate, zero values, unauthorized recorder, value, and unknown calls", async () => {
  const { outsider, registry } = await fixture();
  const actionId = id("once");
  const proofHash = id("proof");
  await (await registry.recordProof(actionId, proofHash)).wait();
  await assert.rejects(registry.recordProof(actionId, id("other")));
  await assert.rejects(registry.recordProof(ZeroHash, proofHash));
  await assert.rejects(registry.recordProof(id("new"), ZeroHash));
  await assert.rejects(registry.connect(outsider).recordProof(id("outsider"), proofHash));
  await assert.rejects(outsider.sendTransaction({ to: await registry.getAddress(), value: 1n }));
  await assert.rejects(outsider.sendTransaction({ to: await registry.getAddress(), data: keccak256(toUtf8Bytes("unknown")) }));
});

test("runtime exposes only intended ABI surface", async () => {
  const { compiled } = await fixture();
  const functions = compiled.abi.filter((x) => x.type === "function").map((x) => x.name).sort();
  assert.deepEqual(functions, ["authorizedRecorder", "proofHashByAction", "recordProof"]);
  assert.equal(compiled.abi.some((x) => x.type === "receive" || x.type === "fallback"), false);
});

test("compiled creation/runtime bytecode matches unsigned deployment evidence", () => {
  // Adapted for pulse-arc-radar: the contract is copied unchanged, so its bytecode must equal the
  // testnet-verified predecessor build AND the unsigned mainnet plan (which is never signed here).
  const compiled = compile();
  const testnet = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "evidence", "testnet_predecessor", "deployment_preparation.json"), "utf8"));
  assert.equal(keccak256("0x" + compiled.evm.bytecode.object), testnet.creation_bytecode_keccak256);
  assert.equal(keccak256("0x" + compiled.evm.deployedBytecode.object), testnet.runtime_bytecode_keccak256);
  assert.equal(testnet.chain_id, 5042002);
  const plan = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "evidence", "mainnet_deployment_plan.json"), "utf8"));
  assert.equal(plan.chain_id, 5042);
  assert.equal(plan.creation_bytecode_keccak256, testnet.creation_bytecode_keccak256);
  assert.equal(plan.creation_bytecode, "0x" + compiled.evm.bytecode.object);
  if (plan.deployer_recorder_wallet === "PENDING_OWNER") {
    assert.equal(plan.deployment_init_code, null);            // separate deployer not supplied yet
  } else {
    assert.equal(keccak256(plan.deployment_init_code), plan.deployment_init_code_keccak256);
    assert.equal(plan.deployment_init_code, plan.creation_bytecode + plan.constructor_argument_slot.slice(2));
  }
  assert.equal(plan.signed, false);
  assert.equal(plan.broadcast, false);
  assert.equal(plan.deployed_address, null);
});
