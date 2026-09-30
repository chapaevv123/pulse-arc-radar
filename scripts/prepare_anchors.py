"""Prepare (never send) snapshot anchors for PulseActionProofRegistry.recordProof.

For each chosen snapshot: re-verify raw/canonical/index hashes, derive
actionId, and emit the exact recordProof calldata. The result is written to
snapshots/anchors.json -> prepared_anchors, which also pins those snapshot
files against retention. No network, no keys, no signing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_anchor import verify_file  # noqa: E402
from src.scanner.snapshot import _atomic_write, canonical_bytes, default_anchors  # noqa: E402

SEL_RECORD_PROOF = "0x43f09497"          # recordProof(bytes32,bytes32)


def record_proof_calldata(action_id: str, proof_hash: str) -> str:
    for v in (action_id, proof_hash):
        if not (v.startswith("0x") and len(v) == 66 and int(v, 16) != 0):
            raise ValueError("BYTES32_INVALID_OR_ZERO")
    return SEL_RECORD_PROOF + action_id[2:] + proof_hash[2:]


def prepare(snapshot_ids: list[str], snapshots_dir: Path = ROOT / "snapshots") -> dict:
    anchors_path = snapshots_dir / "anchors.json"
    current = json.loads(anchors_path.read_bytes()) if anchors_path.exists() else {}
    doc = default_anchors()
    for key in ("registry", "registry_status", "recorder", "recorder_status", "anchors"):
        if current.get(key) not in (None, [], ""):
            doc[key] = current[key]
    if (current.get("pulse_agent") or {}).get("agent_id") is not None:
        doc["pulse_agent"] = current["pulse_agent"]
    prepared = {p["snapshot_id"]: p for p in current.get("prepared_anchors", [])}
    for sid in snapshot_ids:
        path = snapshots_dir / f"{sid}.json"
        res = verify_file(path, snapshots_dir / "index.json")
        if not res["verified"] or not res["index_match"]:
            raise SystemExit(f"SNAPSHOT_NOT_VERIFIED:{sid}")
        snap = json.loads(path.read_bytes())
        prepared[sid] = {
            "snapshot_id": sid,
            "action_id": res["action_id"],
            "proof_hash": res["raw_sha256"],
            "canonical_sha256": res["canonical_sha256"],
            "from_block": snap["block_range"]["from"],
            "to_block": snap["block_range"]["to"],
            "to_block_time": snap["block_range"]["to_block_time"],
            "created_at": snap["created_at"],
            "agents_observed": snap["stats"]["agents_observed"],
            "record_proof_calldata": record_proof_calldata(res["action_id"], res["raw_sha256"]),
            "verify_command": f"python scripts/verify_anchor.py --snapshot snapshots/{sid}.json",
            "status": "PREPARED_NOT_SENT",
        }
    doc["prepared_anchors"] = sorted(prepared.values(), key=lambda p: p["to_block"])
    _atomic_write(anchors_path, canonical_bytes(doc))
    return doc


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("snapshot_ids", nargs="+")
    args = p.parse_args(argv)
    doc = prepare(args.snapshot_ids)
    print(json.dumps(doc["prepared_anchors"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
