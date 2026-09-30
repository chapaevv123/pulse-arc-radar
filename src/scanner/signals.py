"""Explainable, deterministic Pulse signals.

EVIDENCE signals restate something directly observed (a log, a decoded
registration file, an HTTP status). HEURISTIC signals are patterns that are
worth a human look; they are never verdicts about intent, and no signal in
this module labels anyone as a scam or fraud.
"""

from __future__ import annotations

from collections import defaultdict

from . import config

SIGNAL_DEFS = {
    "NEW_AGENT": ("EVIDENCE", "Registered in the IdentityRegistry within the last ~24h (172,800 blocks) of this snapshot."),
    "METADATA_VALID": ("EVIDENCE", "Registration file decodes and passes the ERC-8004 registration-v1 checks."),
    "METADATA_INVALID": ("EVIDENCE", "Registration file is missing, malformed, or fails a blocking ERC-8004 check."),
    "METADATA_UNVERIFIED": ("EVIDENCE", "Registration file not verified yet (pending fetch, IPFS, or unsupported scheme)."),
    "ENDPOINT_LIVE": ("EVIDENCE", "Probed endpoint answered HTTP 2xx/3xx, or 400/401/402/403/405/406/422/429 (server up; request needed parameters, auth or payment)."),
    "ENDPOINT_HTTP_ERROR": ("EVIDENCE", "Probed endpoint answered with an HTTP error (e.g. 404/410/5xx)."),
    "ENDPOINT_UNREACHABLE": ("EVIDENCE", "Primary endpoint could not be reached (DNS/connect/TLS/timeout)."),
    "ENDPOINT_BLOCKED": ("EVIDENCE", "Primary endpoint was not fetched: it points at a non-public address or disallowed URL."),
    "X402_DECLARED": ("EVIDENCE", "Registration file declares x402Support: true (a declaration, not a payment test)."),
    "HTTP_402_OBSERVED": ("EVIDENCE", "Primary endpoint answered HTTP 402 Payment Required."),
    "USDC_ACTIVITY_PRESENT": ("EVIDENCE", "Owner address sent or received USDC in the rolling ~6h window (gas payments excluded)."),
    "REPUTATION_FEEDBACK_PRESENT": ("EVIDENCE", "At least one NewFeedback event targets this agent in the ReputationRegistry."),
    "VALIDATION_ACTIVITY_PRESENT": ("EVIDENCE", "At least one ValidationRequest/Response targets this agent."),
    "MASS_REGISTRATION_CLUSTER": ("HEURISTIC", f"Owner address controls >= {config.MASS_REGISTRATION_THRESHOLD} agents. Common for platforms and batch deployers; not a judgement."),
    "HIGH_REGISTRATION_DENSITY": ("HEURISTIC", f"Owner registered >= {config.DENSITY_THRESHOLD} agents within ~1h ({config.DENSITY_WINDOW_BLOCKS} blocks) ending at this registration."),
    "IDENTICAL_METADATA_CLUSTER": ("HEURISTIC", f"Byte-identical agentURI shared by >= {config.IDENTICAL_METADATA_THRESHOLD} agents."),
}

# Display priority for the headline signal (first match wins).
PRIORITY = [
    "ENDPOINT_LIVE", "HTTP_402_OBSERVED", "X402_DECLARED", "USDC_ACTIVITY_PRESENT",
    "REPUTATION_FEEDBACK_PRESENT", "VALIDATION_ACTIVITY_PRESENT", "METADATA_VALID",
    "MASS_REGISTRATION_CLUSTER", "HIGH_REGISTRATION_DENSITY", "IDENTICAL_METADATA_CLUSTER",
    "ENDPOINT_HTTP_ERROR", "ENDPOINT_UNREACHABLE", "ENDPOINT_BLOCKED",
    "METADATA_INVALID", "METADATA_UNVERIFIED", "NEW_AGENT",
]

CLASS_DEFS = {
    "STANDOUT": "Valid registration and a live endpoint, outside any mass-registration cluster.",
    "ACTIVE": "Valid registration with onchain activity (USDC, reputation, or validation), outside mass clusters.",
    "CLUSTERED": "Part of a mass-registration cluster (heuristic).",
    "INCOMPLETE": "Registration file invalid or not yet verified.",
    "BACKGROUND": "Valid registration, no further activity observed yet.",
}


def compute_clusters(agents: dict[str, dict]) -> tuple[list[dict], dict[int, set[str]]]:
    by_owner: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_uri: dict[str, list[int]] = defaultdict(list)
    for a in agents.values():
        if a["owner"] and a["registered_block"] is not None:
            by_owner[a["owner"]].append((a["registered_block"], a["agent_id"]))
        if a["uri_sha256"] and a["uri_kind"] != "EMPTY":
            by_uri[a["uri_sha256"]].append(a["agent_id"])
    flags: dict[int, set[str]] = defaultdict(set)
    clusters: list[dict] = []
    for owner, regs in sorted(by_owner.items()):
        regs.sort()
        if len(regs) >= config.MASS_REGISTRATION_THRESHOLD:
            for _, aid in regs:
                flags[aid].add("MASS_REGISTRATION_CLUSTER")
            clusters.append({"kind": "MASS_REGISTRATION_CLUSTER", "key": owner, "agents": len(regs),
                             "first_agent_id": min(x[1] for x in regs), "last_agent_id": max(x[1] for x in regs),
                             "first_block": regs[0][0], "last_block": regs[-1][0]})
        lo = 0
        for hi, (block, aid) in enumerate(regs):          # two-pointer window ending at each registration
            while regs[lo][0] < block - config.DENSITY_WINDOW_BLOCKS + 1:
                lo += 1
            if hi - lo + 1 >= config.DENSITY_THRESHOLD:
                flags[aid].add("HIGH_REGISTRATION_DENSITY")
    for digest, ids in sorted(by_uri.items()):
        if len(ids) >= config.IDENTICAL_METADATA_THRESHOLD:
            for aid in ids:
                flags[aid].add("IDENTICAL_METADATA_CLUSTER")
            clusters.append({"kind": "IDENTICAL_METADATA_CLUSTER", "key": digest, "agents": len(ids),
                             "first_agent_id": min(ids), "last_agent_id": max(ids)})
    clusters.sort(key=lambda c: (-c["agents"], c["kind"], c["key"]))
    return clusters, flags


def agent_signals(agent: dict, *, snapshot_block: int, cluster_flags: set[str], usdc: dict | None) -> list[str]:
    sig: set[str] = set(cluster_flags)
    reg = agent["registered_block"]
    if reg is not None and reg > snapshot_block - config.NEW_AGENT_WINDOW_BLOCKS:
        sig.add("NEW_AGENT")
    status = (agent["meta"] or {}).get("status")
    if status == "VALID":
        sig.add("METADATA_VALID")
    elif status == "INVALID":
        sig.add("METADATA_INVALID")
    else:
        sig.add("METADATA_UNVERIFIED")
    if (agent["meta"] or {}).get("x402") is True:
        sig.add("X402_DECLARED")
    ep = agent["endpoint"]
    ep_map = {"LIVE": "ENDPOINT_LIVE", "HTTP_ERROR": "ENDPOINT_HTTP_ERROR",
              "UNREACHABLE": "ENDPOINT_UNREACHABLE", "BLOCKED": "ENDPOINT_BLOCKED"}
    if ep["status"] in ep_map:
        sig.add(ep_map[ep["status"]])
    if ep.get("http_status") == 402:
        sig.add("HTTP_402_OBSERVED")
    if usdc and usdc.get("transfers", 0) > 0:
        sig.add("USDC_ACTIVITY_PRESENT")
    if agent["feedback_count"] > 0:
        sig.add("REPUTATION_FEEDBACK_PRESENT")
    if agent["validation_count"] > 0:
        sig.add("VALIDATION_ACTIVITY_PRESENT")
    return [s for s in PRIORITY if s in sig]


def classify(signals: list[str]) -> str:
    s = set(signals)
    mass = "MASS_REGISTRATION_CLUSTER" in s
    if "METADATA_VALID" not in s:
        return "CLUSTERED" if mass else "INCOMPLETE"
    if mass:
        return "CLUSTERED"
    if "ENDPOINT_LIVE" in s:
        return "STANDOUT"
    if s & {"USDC_ACTIVITY_PRESENT", "REPUTATION_FEEDBACK_PRESENT", "VALIDATION_ACTIVITY_PRESENT"}:
        return "ACTIVE"
    return "BACKGROUND"


# 4xx answers that prove a server is up at that path (e.g. x402 endpoints reject a bare GET with 400).
LIVE_4XX = frozenset({400, 401, 402, 403, 405, 406, 422, 429})


def classify_endpoint(outcome: str, http_status: int | None) -> str:
    if outcome in ("BLOCKED", "INVALID_URL"):
        return "BLOCKED"
    if outcome in ("UNREACHABLE",):
        return "UNREACHABLE"
    if http_status is not None and (200 <= http_status < 400 or http_status in LIVE_4XX):
        return "LIVE"
    if http_status is not None:
        return "HTTP_ERROR"
    return "UNREACHABLE"
