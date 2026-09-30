"""Canonical snapshot serialization, hashing, and publication.

Canonical form (identical in public/verify.js):
  * JSON with keys sorted, separators "," and ":", no whitespace
  * pure ASCII: every non-ASCII code unit escaped as lowercase \\uXXXX
  * only objects, arrays, strings, booleans, null and safe integers
    (|n| <= 2**53 - 1); floats are rejected, big amounts are strings
  * object keys must be ASCII
The file on disk IS the canonical byte string, so the SHA-256 of the raw file
equals the SHA-256 of the re-canonicalized content.

Hash algorithm: SHA-256 (the same one PulseActionProofRegistry anchors as
`proofHash`, matching the predecessor pulse-arc canonicalizer).
  proofHash = sha256(canonical snapshot bytes)
  actionId  = sha256("pulse-arc-radar/v1/snapshot/" + snapshot_id)
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import config

MAX_SAFE_INT = 2**53 - 1
ACTION_DOMAIN = "pulse-arc-radar/v1/snapshot/"
SNAPSHOT_SCHEMA = "pulse_arc_radar_snapshot_v1"
DIRECTORY_SCHEMA = "pulse_arc_radar_directory_v1"
INDEX_SCHEMA = "pulse_arc_radar_index_v1"
ANCHORS_SCHEMA = "pulse_arc_radar_anchors_v1"


def _check(value, path="$"):
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INT:
            raise ValueError(f"UNSAFE_INTEGER:{path}")
        return
    if isinstance(value, float):
        raise ValueError(f"FLOAT_NOT_ALLOWED:{path}")
    if isinstance(value, list):
        for i, v in enumerate(value):
            _check(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or not k.isascii():
                raise ValueError(f"KEY_NOT_ASCII_STRING:{path}")
            _check(v, f"{path}.{k}")
        return
    raise ValueError(f"TYPE_NOT_ALLOWED:{path}:{type(value).__name__}")


def canonical_bytes(obj) -> bytes:
    _check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return "0x" + hashlib.sha256(data).hexdigest()


def snapshot_hash(obj) -> str:
    return sha256_hex(canonical_bytes(obj))


def action_id(snapshot_id: str) -> str:
    return sha256_hex((ACTION_DOMAIN + snapshot_id).encode("utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def default_anchors() -> dict:
    return {
        "schema": ANCHORS_SCHEMA,
        "chain_id": config.CHAIN_ID,
        "hash_algorithm": "sha256",
        "action_id_domain": ACTION_DOMAIN,
        "registry": None,
        "registry_status": "PENDING_MAINNET_DEPLOYMENT",
        "recorder": None,                      # set only after the registry is deployed and verified
        "recorder_status": "PLANNED_NOT_DEPLOYED",
        "roles": {"builder_wallet": config.BUILDER_WALLET, "pulse_agent_wallet": config.PULSE_AGENT_WALLET,
                  "deployer_recorder_wallet": config.DEPLOYER_RECORDER_WALLET},
        "pulse_agent": {
            "status": "PENDING_MAINNET_REGISTRATION",
            "identity_registry": config.IDENTITY_REGISTRY,
            "owner": config.PULSE_AGENT_WALLET,
            "metadata_uri": config.AGENT_METADATA_URL,
            "agent_id": None,
            "testnet_predecessor": {"chain_id": 5042002, "agent_id": 894567,
                                     "identity_registry": "0x8004a818bfb912233c491871b3d84c89a494bd9e",
                                     "repo": "https://github.com/chapaevv123/pulse-arc"},
        },
        "prepared_anchors": [],
        "anchors": [],
    }


def publish(out_dir: Path, snapshot: dict, directory: dict, *,
            retention: int = config.SNAPSHOT_RETENTION) -> dict:
    """Write snapshot, latest, directory and index. Returns the index entry."""
    out_dir = Path(out_dir)
    dir_bytes = canonical_bytes(directory)
    snapshot = dict(snapshot)
    snapshot["directory"] = {"file": "directory.json", "sha256": sha256_hex(dir_bytes),
                             "agents": len(directory["agents"])}
    snap_bytes = canonical_bytes(snapshot)
    digest = sha256_hex(snap_bytes)
    sid = snapshot["snapshot_id"]
    _atomic_write(out_dir / f"{sid}.json", snap_bytes)
    _atomic_write(out_dir / "latest.json", snap_bytes)
    _atomic_write(out_dir / "directory.json", dir_bytes)

    anchors_path = out_dir / "anchors.json"
    if not anchors_path.exists():
        _atomic_write(anchors_path, canonical_bytes(default_anchors()))
    anchors_doc = json.loads(anchors_path.read_bytes())
    # Anchored AND prepared-for-anchoring snapshots are pinned: retention never deletes them.
    anchored = {a.get("snapshot_id") for a in anchors_doc.get("anchors", []) + anchors_doc.get("prepared_anchors", [])}

    index_path = out_dir / "index.json"
    index = json.loads(index_path.read_bytes()) if index_path.exists() else \
        {"schema": INDEX_SCHEMA, "chain_id": config.CHAIN_ID, "snapshots": []}
    entry = {"id": sid, "file": f"{sid}.json", "sha256": digest, "action_id": snapshot["action_id"],
             "from_block": snapshot["block_range"]["from"], "to_block": snapshot["block_range"]["to"],
             "created_at": snapshot["created_at"], "agents_observed": snapshot["stats"]["agents_observed"]}
    index["snapshots"] = [e for e in index["snapshots"] if e["id"] != sid] + [entry]
    index["snapshots"].sort(key=lambda e: e["to_block"], reverse=True)
    for i, e in enumerate(index["snapshots"]):          # retention: keep recent + anchored files
        if i >= retention and e["id"] not in anchored and e.get("file"):
            p = out_dir / e["file"]
            if p.exists():
                p.unlink()
            e["file"] = None
    index["latest"] = sid
    _atomic_write(index_path, canonical_bytes(index))
    return entry
