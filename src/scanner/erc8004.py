"""ERC-8004 log decoding and the durable agent book.

Event signatures come from erc-8004/erc-8004-contracts and their topic0
hashes were matched against real Arc mainnet logs. Logs may arrive out of
order (forward scan + backward backfill), so every mutable field carries the
(block, logIndex) position that set it and only newer positions win.
"""

from __future__ import annotations

from . import config
from .metadata import VALIDATOR_VERSION, primary_endpoint, sanitize_text, summarize_uri, uri_digest

ZERO_ADDRESS = "0x" + "0" * 40

# Identity registry
TOPIC_REGISTERED = "0xca52e62c367d81bb2e328eb795f7c7ba24afb478408a26c0e201d155c449bc4a"
TOPIC_URI_UPDATED = "0x3a2c7fffc2cba7582c690e3b82c453ea02a308326a98a3ad7576c606336409fb"
TOPIC_METADATA_SET = "0x2c149ed548c6d2993cd73efe187df6eccabe4538091b33adbd25fafdb8a1468b"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# Reputation registry
TOPIC_NEW_FEEDBACK = "0x6a4a61743519c9d648a14e6493f47dbe3ff1aa29e7785c96c8326a205e58febc"
TOPIC_FEEDBACK_REVOKED = "0x25156fd3288212246d8b008d5921fde376c71ed14ac2e072a506eb06fde6d09d"
# Validation registry
TOPIC_VALIDATION_REQUEST = "0x530436c3634a98e1e626b0898be2f1e9980cc1bd2a78c07a0aba52d0a48a5059"
TOPIC_VALIDATION_RESPONSE = "0xafddf629e874ccc3963b6a888c477bd464a6c8525024fc88759ea3b2326349ae"

REGISTRY_ADDRESSES = [config.IDENTITY_REGISTRY, config.REPUTATION_REGISTRY, config.VALIDATION_REGISTRY]
MAX_METADATA_KEYS = 16
MAX_URI_STORED = 2048
MAX_DATA_URI_STORED = 16_384      # kept so summaries can be re-validated when rules change


def topic_int(topic: str) -> int:
    return int(topic, 16)


def topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def decode_string(data_hex: str, head_index: int = 0) -> str:
    """Decode the dynamic `string` whose offset sits in head word `head_index`."""
    raw = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)
    start = head_index * 32
    if len(raw) < start + 32:
        raise ValueError("ABI_HEAD_SHORT")
    off = int.from_bytes(raw[start:start + 32], "big")
    if off + 32 > len(raw):
        raise ValueError("ABI_OFFSET_OUT_OF_RANGE")
    length = int.from_bytes(raw[off:off + 32], "big")
    if off + 32 + length > len(raw):
        raise ValueError("ABI_LENGTH_OUT_OF_RANGE")
    return raw[off + 32: off + 32 + length].decode("utf-8", "replace")


def log_pos(log: dict) -> list[int]:
    return [int(log["blockNumber"], 16), int(log["logIndex"], 16)]


def _newer(pos: list[int], current) -> bool:
    return current is None or tuple(pos) > tuple(current)


class AgentBook:
    def __init__(self, agents: dict | None = None, counted_events: list | None = None):
        self.agents: dict[str, dict] = agents or {}
        self.counted = set(counted_events or [])
        self.added: set[int] = set()
        self.decode_errors = 0

    def _get(self, agent_id: int) -> dict:
        key = str(agent_id)
        if key not in self.agents:
            self.agents[key] = {
                "agent_id": agent_id, "owner": None, "owner_pos": None,
                "registered_block": None, "registered_tx": None, "registered_log_index": None,
                "uri_kind": None, "uri": None, "uri_sha256": None, "uri_bytes": 0, "uri_pos": None,
                "meta": None, "meta_checked_at": None, "metadata_keys": [],
                "feedback_count": 0, "validation_count": 0,
                "endpoint": {"url": None, "status": "NONE_DECLARED", "http_status": None, "checked_at": None},
            }
            self.added.add(agent_id)
        return self.agents[key]

    def _set_uri(self, agent: dict, uri: str, pos: list[int]) -> None:
        if not _newer(pos, agent["uri_pos"]):
            return
        kind, summary = summarize_uri(uri, agent["agent_id"])
        agent.update(uri_pos=pos, uri_kind=kind, uri_sha256=uri_digest(uri), uri_bytes=len(uri.encode("utf-8", "replace")),
                     uri=uri if (kind == "HTTP" and len(uri) <= MAX_URI_STORED)
                     or (kind == "DATA" and len(uri) <= MAX_DATA_URI_STORED) else None,
                     meta=summary, meta_checked_at=None)
        if kind == "HTTP" and len(uri) > MAX_URI_STORED:
            agent["meta"] = dict(summary, status="INVALID", issues=["URI_TOO_LONG"])
        self.refresh_endpoint(agent)

    def revalidate_stale(self) -> int:
        """Re-derive summaries produced by an older validator (no network for data: URIs)."""
        n = 0
        for agent in self.agents.values():
            meta = agent["meta"]
            if meta is None or meta.get("validator") == VALIDATOR_VERSION or agent["uri"] is None:
                continue
            if agent["uri_kind"] == "DATA":
                agent["meta"] = summarize_uri(agent["uri"], agent["agent_id"])[1]
            elif agent["uri_kind"] == "HTTP":
                agent["meta"] = summarize_uri(agent["uri"], agent["agent_id"])[1]   # -> PENDING_FETCH
                agent["meta_checked_at"] = None
            self.refresh_endpoint(agent)
            n += 1
        return n

    @staticmethod
    def refresh_endpoint(agent: dict) -> None:
        url = primary_endpoint(agent["meta"] or {})
        ep = agent["endpoint"]
        if url != ep["url"]:
            agent["endpoint"] = {"url": url, "status": "PENDING" if url else "NONE_DECLARED",
                                 "http_status": None, "checked_at": None}

    def apply_logs(self, logs: list[dict]) -> int:
        applied = 0
        for log in sorted(logs, key=log_pos):
            try:
                applied += self._apply(log)
            except (ValueError, KeyError, IndexError):
                self.decode_errors += 1
        return applied

    def _apply(self, log: dict) -> int:
        address = log["address"].lower()
        topics = log.get("topics") or []
        if not topics:
            return 0
        t0, pos = topics[0].lower(), log_pos(log)
        if address == config.IDENTITY_REGISTRY:
            if t0 == TOPIC_REGISTERED and len(topics) >= 3:
                agent = self._get(topic_int(topics[1]))
                if agent["registered_block"] is not None:
                    return 0                                   # duplicate delivery
                agent.update(registered_block=pos[0], registered_log_index=pos[1],
                             registered_tx=log["transactionHash"].lower())
                owner = topic_address(topics[2])
                if _newer(pos, agent["owner_pos"]):
                    agent.update(owner=owner, owner_pos=pos)
                self._set_uri(agent, decode_string(log["data"]), pos)
                return 1
            if t0 == TOPIC_URI_UPDATED and len(topics) >= 2:
                self._set_uri(self._get(topic_int(topics[1])), decode_string(log["data"]), pos)
                return 1
            if t0 == TOPIC_TRANSFER and len(topics) >= 4:
                to = topic_address(topics[2])
                agent = self._get(topic_int(topics[3]))
                if to != ZERO_ADDRESS and _newer(pos, agent["owner_pos"]):
                    agent.update(owner=to, owner_pos=pos)
                return 1
            if t0 == TOPIC_METADATA_SET and len(topics) >= 2:
                agent = self._get(topic_int(topics[1]))
                key = sanitize_text(decode_string(log["data"], 0), 40) or "?"
                if key not in agent["metadata_keys"] and len(agent["metadata_keys"]) < MAX_METADATA_KEYS:
                    agent["metadata_keys"] = sorted(agent["metadata_keys"] + [key])
                return 1
            return 0
        event_id = f"{pos[0]}:{pos[1]}"
        if address == config.REPUTATION_REGISTRY and t0 == TOPIC_NEW_FEEDBACK and len(topics) >= 2:
            if event_id in self.counted:
                return 0
            self.counted.add(event_id)
            self._get(topic_int(topics[1]))["feedback_count"] += 1
            return 1
        if address == config.VALIDATION_REGISTRY and t0 in (TOPIC_VALIDATION_REQUEST, TOPIC_VALIDATION_RESPONSE) \
                and len(topics) >= 3:
            if event_id in self.counted:
                return 0
            self.counted.add(event_id)
            self._get(topic_int(topics[2]))["validation_count"] += 1
            return 1
        return 0

    def owners(self) -> list[str]:
        return sorted({a["owner"] for a in self.agents.values() if a["owner"]})
