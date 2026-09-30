"""Read-only pre-flight for one step of evidence/mainnet_tx_plan.json.

Checks chain, nonce (latest == pending == planned), balance, fee caps, gas
estimate vs limit, and a non-reverting eth_call simulation of the EXACT
calldata, plus step-specific invariants. Prints the exact transaction for
operator review. It cannot sign or send: ArcRpc only allows read methods.

    python scripts/preflight.py --step 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_anchor import verify_file  # noqa: E402
from src.scanner import config  # noqa: E402
from src.scanner.rpc import ArcRpc, RpcError  # noqa: E402

SEL_AUTHORIZED_RECORDER = "0x1199060d"
SEL_PROOF_HASH_BY_ACTION = "0x828006bc"
ZERO32 = "0x" + "0" * 64

def run_preflight(step: int, rpc: ArcRpc, plan_path: Path = ROOT / "evidence" / "mainnet_tx_plan.json",
                  deploy_path: Path = ROOT / "evidence" / "mainnet_deployment_plan.json") -> dict:
    plan = json.loads(plan_path.read_text())
    deploy = json.loads(deploy_path.read_text())
    tx = next(t for t in plan["transactions"] if t["step"] == step)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), str(detail)))

    chain = rpc.chain_id()
    check("chain_id == 5042", chain == config.CHAIN_ID == tx["chain_id"], chain)
    sender = tx["from"]
    check("sender is not the builder wallet", sender.lower() != config.BUILDER_WALLET)
    check("sender is the owner-approved Pulse wallet", sender.lower() == config.PULSE_AGENT_WALLET)
    latest = int(rpc.call("eth_getTransactionCount", [sender, "latest"]), 16)
    pending = int(rpc.call("eth_getTransactionCount", [sender, "pending"]), 16)
    check("nonce latest == pending == planned", latest == pending == tx["nonce"],
          f"latest={latest} pending={pending} planned={tx['nonce']}")
    balance = int(rpc.call("eth_getBalance", [sender, "latest"]), 16)
    max_cost = tx["gas_limit"] * int(tx["max_fee_per_gas_wei"])
    check("balance >= max cost of this tx", balance >= max_cost, f"balance={balance} max_cost={max_cost}")
    block = rpc.call("eth_getBlockByNumber", ["latest", False])
    base_fee = int(block["baseFeePerGas"], 16)
    check("base fee <= maxFeePerGas cap", base_fee <= int(tx["max_fee_per_gas_wei"]), f"base_fee={base_fee}")
    check("value == 0", tx["value"] == "0")

    call = {"from": sender, "data": tx["data"], "value": "0x0"}
    if tx["to"]:
        call["to"] = tx["to"]
    try:
        est = int(rpc.call("eth_estimateGas", [call]), 16)
        check("eth_estimateGas <= gas_limit", est <= tx["gas_limit"], f"estimate={est} limit={tx['gas_limit']}")
    except RpcError as exc:
        est = None
        check("eth_estimateGas succeeds", False, exc)
    try:
        sim = rpc.call("eth_call", [call, "latest"])
        check("eth_call simulation does not revert", True)
    except RpcError as exc:
        sim = None
        check("eth_call simulation does not revert", False, exc)

    extra: dict = {}
    if tx["action"] == "DEPLOY_PulseActionProofRegistry":
        check("data == creation_bytecode || constructor(recorder)",
              tx["data"] == deploy["creation_bytecode"] + deploy["constructor_argument_slot"][2:])
        check("constructor recorder == sender", deploy["constructor_argument_slot"][-40:].lower() == sender[2:].lower())
        code = rpc.call("eth_getCode", [tx["creates"], "latest"])
        check("predicted registry address is unused", code in ("0x", "0x0"), tx["creates"])
        check("simulated runtime == expected onchain runtime",
              sim is not None and sim.lower() == deploy["expected_onchain_runtime"].lower())
        extra["predicted_registry_address"] = tx["creates"]
    elif tx["action"] == "ERC8004_REGISTER_PULSE_AGENT":
        check("to == ERC-8004 IdentityRegistry", tx["to"].lower() == config.IDENTITY_REGISTRY)
        check("argument == published registration URL", tx["argument"] == config.AGENT_METADATA_URL)
        extra["simulated_agent_id"] = int(sim, 16) if sim else None
    elif tx["action"] == "ANCHOR_SNAPSHOT":
        code = rpc.call("eth_getCode", [tx["to"], "latest"])
        check("registry runtime == expected", code.lower() == deploy["expected_onchain_runtime"].lower())
        rec = rpc.eth_call(tx["to"], SEL_AUTHORIZED_RECORDER)
        check("authorizedRecorder == sender", rec[-40:].lower() == sender[2:].lower(), rec)
        cur = rpc.eth_call(tx["to"], SEL_PROOF_HASH_BY_ACTION + tx["action_id"][2:])
        check("actionId not yet anchored", cur.lower() == ZERO32, cur)
        res = verify_file(ROOT / "snapshots" / f"{tx['snapshot_id']}.json", ROOT / "snapshots" / "index.json")
        check("snapshot re-verifies and matches proofHash/actionId",
              res["verified"] and res["raw_sha256"] == tx["proof_hash"] and res["action_id"] == tx["action_id"])

    ok = all(c[1] for c in checks)
    return {
        "step": step, "action": tx["action"], "result": "PREFLIGHT_PASS" if ok else "PREFLIGHT_FAIL",
        "checks": [{"check": n, "ok": o, "detail": d} for n, o, d in checks],
        "transaction_for_approval": {
            "chainId": tx["chain_id"], "type": tx["type"], "from": sender, "to": tx["to"], "nonce": tx["nonce"],
            "value": "0", "gasLimit": tx["gas_limit"], "maxFeePerGas": tx["max_fee_per_gas_wei"],
            "maxPriorityFeePerGas": tx["max_priority_fee_per_gas_wei"], "data_bytes": (len(tx["data"]) - 2) // 2,
            "data_sha256": "0x" + hashlib.sha256(bytes.fromhex(tx["data"][2:])).hexdigest(),
            "data_keccak256": tx.get("data_keccak256"), "method": tx.get("method"), "argument": tx.get("argument"),
        },
        "fee": {"gas_estimate_now": est, "base_fee_now_wei": base_fee, "balance_wei": balance,
                "max_cost_usdc": f"{max_cost / 1e18:.6f}",
                "expected_cost_usdc": f"{(est or tx['expected_gas']) * (base_fee + int(tx['max_priority_fee_per_gas_wei'])) / 1e18:.6f}"},
        **extra,
        "signed": False, "broadcast": False,
    }

def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--step", type=int, required=True)
    args = p.parse_args(argv)
    rpc = ArcRpc(min_interval=0.3, max_calls=40)
    out = run_preflight(args.step, rpc)
    print(json.dumps(out, indent=1))
    return 0 if out["result"] == "PREFLIGHT_PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
