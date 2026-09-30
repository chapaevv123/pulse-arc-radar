"""ERC-8004 registration-file validation for untrusted agent metadata.

All strings leaving this module are sanitized (control and bidi-override
characters removed, length-capped). They are still untrusted: the dashboard
renders them with textContent only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import unicodedata
from urllib.parse import unquote_to_bytes

from . import config

REGISTRATION_TYPE = "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"
EIP_8004_PREFIX = "https://eips.ethereum.org/eips/eip-8004"
# Bump when validation rules change; stale summaries are re-validated automatically.
VALIDATOR_VERSION = 2
OUR_REGISTRY_REF = f"eip155:{config.CHAIN_ID}:{config.IDENTITY_REGISTRY}"
MAX_DATA_URI_CHARS = 262_144
BIDI_CONTROLS = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F)}

# Issues that make the registration file INVALID; everything else is a warning.
BLOCKING_ISSUES = frozenset({
    "NOT_JSON", "NOT_JSON_OBJECT", "TYPE_MISMATCH", "NAME_MISSING", "SERVICES_MALFORMED",
    "URI_EMPTY", "DATA_URI_MALFORMED", "TOO_LARGE", "BACKLINK_MISMATCH",
})


def sanitize_text(value, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    out = []
    for ch in value:
        if ch in BIDI_CONTROLS or unicodedata.category(ch) in ("Cc", "Cs"):
            out.append(" " if ch in "\t\n\r" else "")
        else:
            out.append(ch)
    text = " ".join("".join(out).split())
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def uri_kind(uri: str) -> str:
    if not uri:
        return "EMPTY"
    low = uri[:16].lower()
    if low.startswith("data:"):
        return "DATA"
    if low.startswith("https://") or low.startswith("http://"):
        return "HTTP"
    if low.startswith("ipfs://"):
        return "IPFS"
    return "OTHER"


def decode_data_uri(uri: str) -> bytes:
    if len(uri) > MAX_DATA_URI_CHARS:
        raise ValueError("TOO_LARGE")
    head, sep, payload = uri[5:].partition(",")
    if not sep:
        raise ValueError("DATA_URI_MALFORMED")
    try:
        if head.lower().endswith(";base64"):
            raw = base64.b64decode(payload + "=" * (-len(payload) % 4), validate=False)
        else:
            raw = unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        raise ValueError("DATA_URI_MALFORMED")
    if len(raw) > config.METADATA_MAX_BYTES:
        raise ValueError("TOO_LARGE")
    return raw


def empty_summary(status: str, source: str, issues=()) -> dict:
    return {"status": status, "source": source, "issues": sorted(set(issues)), "validator": VALIDATOR_VERSION, "name": None,
            "description": None, "services": [], "x402": None, "active": None,
            "image_declared": False, "trust": [], "backlink": None}


def _agent_id_matches(value, agent_id: int) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == agent_id
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) == agent_id
    return False


def validate_registration(raw: bytes, agent_id: int, source: str) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return empty_summary("INVALID", source, ["NOT_JSON"])
    if not isinstance(doc, dict):
        return empty_summary("INVALID", source, ["NOT_JSON_OBJECT"])
    s = empty_summary("VALID", source)
    issues: set[str] = set()
    doc_type = doc.get("type")
    if doc_type != REGISTRATION_TYPE:
        # The bare EIP URL (no #registration-v1 fragment) is a near-miss, not a different format.
        if isinstance(doc_type, str) and doc_type.strip().rstrip("/").lower().split("#")[0] == EIP_8004_PREFIX:
            issues.add("TYPE_NONCANONICAL")
        else:
            issues.add("TYPE_MISMATCH")
    s["name"] = sanitize_text(doc.get("name"), 80)
    if not s["name"]:
        issues.add("NAME_MISSING")
    s["description"] = sanitize_text(doc.get("description"), 160)
    if not s["description"]:
        issues.add("DESCRIPTION_MISSING")
    services = doc.get("services")
    if services is None and "endpoints" in doc:
        services = doc.get("endpoints")
        issues.add("LEGACY_ENDPOINTS_KEY")
    if services is None:
        issues.add("SERVICES_ABSENT")
    elif not isinstance(services, list) or not all(
            isinstance(x, dict) and isinstance(x.get("endpoint"), str) and isinstance(x.get("name"), str)
            for x in services):
        issues.add("SERVICES_MALFORMED")
    else:
        s["services"] = [{"name": sanitize_text(x["name"], 40) or "", "endpoint": sanitize_text(x["endpoint"], 300) or ""}
                         for x in services[:8]]
    if "x402Support" in doc:
        if isinstance(doc["x402Support"], bool):
            s["x402"] = doc["x402Support"]
        else:
            issues.add("X402_FIELD_MALFORMED")
    if isinstance(doc.get("active"), bool):
        s["active"] = doc["active"]
    s["image_declared"] = isinstance(doc.get("image"), str) and bool(doc["image"].strip())
    trust = doc.get("supportedTrust")
    if isinstance(trust, list):
        s["trust"] = sorted({t for t in (sanitize_text(x, 40) for x in trust[:8]) if t})
    regs = doc.get("registrations")
    ours = [r for r in regs if isinstance(r, dict) and str(r.get("agentRegistry", "")).lower() == OUR_REGISTRY_REF] \
        if isinstance(regs, list) else []
    if not ours:
        s["backlink"] = "ABSENT"
        issues.add("BACKLINK_ABSENT")
    elif any(_agent_id_matches(r.get("agentId"), agent_id) for r in ours):
        s["backlink"] = "MATCH"
    elif all(r.get("agentId") in (None, "") for r in ours):
        s["backlink"] = "MATCH_NO_AGENT_ID"
        issues.add("BACKLINK_NO_AGENT_ID")
    else:
        s["backlink"] = "MISMATCH"
        issues.add("BACKLINK_MISMATCH")
    s["issues"] = sorted(issues)
    s["status"] = "INVALID" if issues & BLOCKING_ISSUES else "VALID"
    return s


def summarize_uri(uri: str, agent_id: int) -> tuple[str, dict]:
    """Return (kind, summary) without any network access."""
    kind = uri_kind(uri)
    if kind == "EMPTY":
        return kind, empty_summary("INVALID", "NONE", ["URI_EMPTY"])
    if kind == "DATA":
        try:
            raw = decode_data_uri(uri)
        except ValueError as exc:
            return kind, empty_summary("INVALID", "DATA_URI", [str(exc)])
        return kind, validate_registration(raw, agent_id, "DATA_URI")
    if kind == "HTTP":
        return kind, empty_summary("PENDING_FETCH", "HTTP")
    if kind == "IPFS":
        return kind, empty_summary("UNVERIFIED", "IPFS", ["IPFS_NOT_FETCHED"])
    return kind, empty_summary("UNVERIFIED", "OTHER", ["UNSUPPORTED_SCHEME"])


def uri_digest(uri: str) -> str:
    return "0x" + hashlib.sha256(uri.encode("utf-8", "surrogatepass")).hexdigest()


def primary_endpoint(summary: dict) -> str | None:
    """The one endpoint probed per agent: an x402-named service first (so a real
    402 Payment Required can be observed), otherwise the first http(s) service."""
    http = [s for s in summary.get("services") or []
            if (s.get("endpoint") or "").lower().startswith(("https://", "http://"))]
    x402 = [s for s in http if (s.get("name") or "").lower().startswith("x402")]
    return (x402 or http)[0]["endpoint"] if http else None
