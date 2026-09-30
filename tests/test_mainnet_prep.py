"""Phase 2A preparation: prepared anchors, retention pinning, Pulse agent metadata, role separation."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_anchors import SEL_RECORD_PROOF, prepare, record_proof_calldata
from src.scanner import config
from src.scanner import snapshot as snap_mod
from src.scanner.metadata import validate_registration
from src.scanner.snapshot import action_id

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def fake_snapshot(i: int) -> dict:
    return {"snapshot_id": f"s-{i}", "action_id": action_id(f"s-{i}"), "created_at": "2026-09-30T00:00:00Z",
            "block_range": {"from": i, "to": i, "to_block_time": None}, "stats": {"agents_observed": i}}


class PreparedAnchors(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_calldata_layout(self):
        aid, proof = "0x" + "11" * 32, "0x" + "22" * 32
        data = record_proof_calldata(aid, proof)
        self.assertEqual(data, SEL_RECORD_PROOF + "11" * 32 + "22" * 32)
        self.assertEqual(len(data), 2 + 8 + 128)
        with self.assertRaises(ValueError):
            record_proof_calldata("0x" + "00" * 32, proof)

    def test_prepare_and_pin_against_retention(self):
        for i in range(1, 6):
            snap_mod.publish(self.dir, fake_snapshot(i), {"agents": []}, retention=100)
        doc = prepare(["s-1", "s-2"], self.dir)
        self.assertEqual([p["snapshot_id"] for p in doc["prepared_anchors"]], ["s-1", "s-2"])
        p = doc["prepared_anchors"][0]
        self.assertEqual(p["status"], "PREPARED_NOT_SENT")
        self.assertEqual(p["action_id"], action_id("s-1"))
        self.assertEqual(p["record_proof_calldata"], record_proof_calldata(p["action_id"], p["proof_hash"]))
        self.assertIsNone(doc["registry"])
        self.assertIsNone(doc["recorder"])
        self.assertEqual(doc["anchors"], [])
        for i in range(6, 10):                       # retention of 2 would otherwise prune s-1, s-2
            snap_mod.publish(self.dir, fake_snapshot(i), {"agents": []}, retention=2)
        self.assertTrue((self.dir / "s-1.json").exists())
        self.assertTrue((self.dir / "s-2.json").exists())
        self.assertFalse((self.dir / "s-3.json").exists())

    def test_unverified_snapshot_refused(self):
        snap_mod.publish(self.dir, fake_snapshot(1), {"agents": []})
        path = self.dir / "s-1.json"
        path.write_bytes(path.read_bytes().replace(b'"agents_observed":1', b'"agents_observed":2'))
        with self.assertRaises(SystemExit):
            prepare(["s-1"], self.dir)


class RolesAndMetadata(unittest.TestCase):
    def test_roles_are_distinct_public_addresses(self):
        self.assertNotEqual(config.BUILDER_WALLET, config.PULSE_AGENT_WALLET)
        self.assertEqual(config.DEPLOYER_RECORDER_WALLET, config.PULSE_AGENT_WALLET)   # owner decision
        anchors = json.loads((ROOT / "snapshots" / "anchors.json").read_bytes())
        self.assertEqual(anchors["roles"]["pulse_agent_wallet"], config.PULSE_AGENT_WALLET)
        self.assertEqual(anchors["pulse_agent"]["owner"], config.PULSE_AGENT_WALLET)
        # registry/recorder are set only after a verified deployment (tx1, 2026-09-30)
        if anchors["registry"] is None:
            self.assertIsNone(anchors["recorder"])
        else:
            self.assertEqual(anchors["registry"], "0x1607af4e1da1c06362871443d2e95c367b984efa")
            self.assertEqual(anchors["registry_status"], "DEPLOYED_VERIFIED")
            self.assertEqual(anchors["recorder"], config.PULSE_AGENT_WALLET)
            receipt = json.loads((ROOT / "evidence" / "mainnet_tx1_deploy_receipt.json").read_bytes())
            self.assertEqual(receipt["result"], "TX1_DEPLOY_VERIFIED")
            self.assertEqual(anchors["registry_deployment"]["tx_hash"], receipt["transaction_hash"])
        self.assertEqual(anchors["roles"]["deployer_recorder_wallet"], config.PULSE_AGENT_WALLET)
        self.assertEqual(anchors["anchors"], [])
        self.assertGreaterEqual(len(anchors["prepared_anchors"]), 2)

    def test_pulse_agent_registration_file(self):
        raw = (ROOT / "public" / "agent" / "erc8004-registration.json").read_bytes()
        res = validate_registration(raw, 1360, "HTTP")                # mainnet agentId from the tx2 receipt
        self.assertEqual((res["status"], res["backlink"], res["issues"]), ("VALID", "MATCH", []))
        other = validate_registration(raw, 1, "HTTP")                 # backlink must not match another agent
        self.assertEqual((other["status"], other["backlink"]), ("INVALID", "MISMATCH"))
        self.assertIs(res["x402"], False)                          # Pulse does not claim x402
        doc = json.loads(raw)
        self.assertEqual([s["endpoint"] for s in doc["services"]],
                         [config.SITE_URL, config.SITE_URL + "snapshots/latest.json"])
        self.assertEqual(config.AGENT_METADATA_URL, config.SITE_URL + "agent/erc8004-registration.json")

    def test_tx_plan_is_unsigned_and_consistent(self):
        plan = json.loads((ROOT / "evidence" / "mainnet_tx_plan.json").read_bytes())
        self.assertEqual((plan["chain_id"], plan["signed"], plan["broadcast"]), (5042, False, False))
        actions = [t["action"] for t in plan["transactions"]]
        self.assertEqual(actions[:2], ["DEPLOY_PulseActionProofRegistry", "ERC8004_REGISTER_PULSE_AGENT"])
        reg = plan["transactions"][1]
        self.assertEqual(reg["from"].lower(), config.PULSE_AGENT_WALLET)
        self.assertEqual(reg["to"].lower(), config.IDENTITY_REGISTRY)
        self.assertTrue(reg["data"].startswith(reg["selector"]))
        self.assertIn(config.AGENT_METADATA_URL.encode().hex(), reg["data"])
        anchors = json.loads((ROOT / "snapshots" / "anchors.json").read_bytes())["prepared_anchors"]
        self.assertEqual([t["data"] for t in plan["transactions"][2:]], [p["record_proof_calldata"] for p in anchors])
        txs = plan["transactions"]
        self.assertEqual([t["nonce"] for t in txs], list(range(txs[0]["nonce"], txs[0]["nonce"] + len(txs))))
        self.assertTrue(all(t["from"].lower() == config.PULSE_AGENT_WALLET for t in txs))
        self.assertTrue(all(t["value"] == "0" for t in txs))
        self.assertTrue(all(t["to"] == plan["predicted_registry_address"] for t in txs[2:]))
        self.assertIsNone(txs[0]["to"])
        deploy = json.loads((ROOT / "evidence" / "mainnet_deployment_plan.json").read_bytes())
        self.assertEqual(txs[0]["data"], deploy["creation_bytecode"] + deploy["constructor_argument_slot"][2:])
        self.assertTrue(deploy["constructor_argument_slot"].lower().endswith(config.PULSE_AGENT_WALLET[2:]))
        self.assertTrue(all(t["gas_limit"] >= t["expected_gas"] for t in txs))


@unittest.skipUnless(NODE and (ROOT / "node_modules" / "solc").exists(), "contract deps not installed")
class DeployerSeparation(unittest.TestCase):
    def test_plan_refuses_builder_wallet_as_deployer(self):
        out = subprocess.run([NODE, "scripts/contract_facts.js", "--deployer", config.BUILDER_WALLET], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("BUILDER_WALLET_IS_ATTRIBUTION_ONLY", out.stderr)


if __name__ == "__main__":
    unittest.main()
