"""Verify a published snapshot: raw hash, canonical hash, index entry,
actionId, and (once a registry exists) the onchain anchor. Read-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.scanner import config  # noqa: E402
from src.scanner.rpc import ArcRpc  # noqa: E402
from src.scanner.snapshot import action_id, canonical_bytes, sha256_hex  # noqa: E402

SEL_PROOF_HASH_BY_ACTION = "0x828006bc"
ZERO32 = "0x" + "0" * 64


def verify_file(snapshot_path: Path, index_path: Path | None) -> dict:
    raw = snapshot_path.read_bytes()
    parsed = json.loads(raw)
    raw_hash, canon_hash = sha256_hex(raw), sha256_hex(canonical_bytes(parsed))
    aid = action_id(parsed["snapshot_id"])
    entry = None
    if index_path and index_path.exists():
        entry = next((e for e in json.loads(index_path.read_bytes())["snapshots"]
                      if e["id"] == parsed["snapshot_id"]), None)
    return {"snapshot_id": parsed["snapshot_id"], "raw_sha256": raw_hash, "canonical_sha256": canon_hash,
            "canonical_matches_raw": raw_hash == canon_hash, "action_id": aid,
            "action_id_match": parsed.get("action_id") == aid,
            "index_sha256": entry["sha256"] if entry else None,
            "index_match": (entry["sha256"] == raw_hash) if entry else None,
            "verified": raw_hash == canon_hash and parsed.get("action_id") == aid
            and (entry is None or entry["sha256"] == raw_hash)}


def anchor_status(anchors: dict, aid: str, proof_hash: str, rpc: ArcRpc | None = None) -> dict:
    if not anchors.get("registry"):
        return {"status": "NOT_YET_ANCHORED", "detail": anchors.get("registry_status", "PENDING_MAINNET_DEPLOYMENT")}
    rpc = rpc or ArcRpc()
    rpc.require_chain(config.CHAIN_ID)
    onchain = rpc.eth_call(anchors["registry"], SEL_PROOF_HASH_BY_ACTION + aid[2:]).lower()
    if onchain == ZERO32:
        return {"status": "NOT_ANCHORED", "onchain": onchain}
    return {"status": "ANCHORED_MATCH" if onchain == proof_hash.lower() else "ANCHOR_MISMATCH", "onchain": onchain}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", default=str(ROOT / "snapshots" / "latest.json"))
    p.add_argument("--index", default=str(ROOT / "snapshots" / "index.json"))
    p.add_argument("--anchors", default=str(ROOT / "snapshots" / "anchors.json"))
    args = p.parse_args(argv)
    result = verify_file(Path(args.snapshot), Path(args.index))
    anchors_path = Path(args.anchors)
    anchors = json.loads(anchors_path.read_bytes()) if anchors_path.exists() else {}
    result["anchor"] = anchor_status(anchors, result["action_id"], result["raw_sha256"])
    print(json.dumps(result, indent=1))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
