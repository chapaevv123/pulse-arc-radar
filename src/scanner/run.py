"""One bounded scan run: forward scan, bounded backfill, bounded HTTP checks,
USDC activity, signals, canonical snapshot.

State is written once, atomically, at the end of the run. Cursors only
advance per fully processed chunk, so an interrupted run resumes from the
last saved cursors without double counting.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import config, safe_http, signals as sig, usdc_activity
from .erc8004 import REGISTRY_ADDRESSES, AgentBook
from .metadata import empty_summary, validate_registration
from .rpc import ArcRpc, BudgetExhausted, RpcError, block_chunks, block_chunks_desc
from .snapshot import SNAPSHOT_SCHEMA, DIRECTORY_SCHEMA, action_id, publish

STATE_SCHEMA = "pulse_arc_radar_state_v1"


def iso(ts: int | float) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: Path) -> dict:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") != STATE_SCHEMA or state.get("chain_id") != config.CHAIN_ID:
            raise RuntimeError("STATE_SCHEMA_OR_CHAIN_MISMATCH")
        return state
    return {"schema": STATE_SCHEMA, "chain_id": config.CHAIN_ID, "forward_cursor": None,
            "backfill_cursor": None, "floor_block": config.IDENTITY_REGISTRY_DEPLOY_BLOCK,
            "agents": {}, "counted_events": [], "block_times": {}, "usdc": {"window": None, "owners": {}},
            "last_signals": {}, "runs": 0}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def approx_time(block: int | None, block_times: dict[str, int]) -> str | None:
    if block is None or not block_times:
        return None
    keys = sorted(int(k) for k in block_times)
    if len(keys) == 1:
        k = keys[0]
        return iso(block_times[str(k)] + (block - k) * 0.5)
    i = min(max(bisect.bisect_left(keys, block), 1), len(keys) - 1)
    a, b = keys[i - 1], keys[i]
    ta, tb = block_times[str(a)], block_times[str(b)]
    return iso(ta + (tb - ta) * (block - a) / (b - a))


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def fetch_metadata(book: AgentBook, now: int, fetcher, report: dict) -> None:
    queue = []
    for a in book.agents.values():
        m = a["meta"] or {}
        if a["uri_kind"] != "HTTP" or not a["uri"]:
            continue
        if m.get("status") == "PENDING_FETCH" or (m.get("status") == "FETCH_FAILED"
                                                  and now - (a["meta_checked_at"] or 0) >= config.ENDPOINT_RECHECK_S):
            queue.append(a)
    queue.sort(key=lambda a: -a["agent_id"])
    per_host: dict[str, int] = defaultdict(int)
    cache: dict[str, safe_http.FetchResult] = {}
    for a in queue:
        url = a["uri"]
        if url not in cache:
            if len(cache) >= config.MAX_METADATA_FETCHES_PER_RUN:
                break
            host = _host(url)
            if per_host[host] >= config.MAX_FETCHES_PER_HOST_PER_RUN:
                continue
            per_host[host] += 1
            cache[url] = fetcher(url, max_bytes=config.METADATA_MAX_BYTES)
            report["metadata_fetches"] += 1
        res = cache[url]
        if res.outcome == "OK":
            a["meta"] = validate_registration(res.body, a["agent_id"], "HTTP")
        else:
            issue = f"FETCH_{res.outcome}" + (f"_{res.http_status}" if res.http_status else "")
            a["meta"] = empty_summary("FETCH_FAILED", "HTTP", [issue])
        a["meta_checked_at"] = now
        AgentBook.refresh_endpoint(a)


def check_endpoints(book: AgentBook, now: int, fetcher, report: dict) -> None:
    due = [a for a in book.agents.values() if a["endpoint"]["url"] and (
        a["endpoint"]["status"] == "PENDING" or
        now - (a["endpoint"]["checked_at"] or 0) >= config.ENDPOINT_RECHECK_S)]
    due.sort(key=lambda a: -a["agent_id"])
    per_host: dict[str, int] = defaultdict(int)
    results: dict[str, tuple[str, int | None]] = {}
    for a in due:
        url = a["endpoint"]["url"]
        if url not in results:
            if len(results) >= config.MAX_ENDPOINT_CHECKS_PER_RUN:
                break
            host = _host(url)
            if per_host[host] >= config.MAX_FETCHES_PER_HOST_PER_RUN:
                continue
            per_host[host] += 1
            res = fetcher(url, max_bytes=config.ENDPOINT_MAX_BYTES)
            results[url] = (sig.classify_endpoint(res.outcome, res.http_status), res.http_status)
            report["endpoint_checks"] += 1
        status, code = results[url]
        a["endpoint"] = {"url": url, "status": status, "http_status": code, "checked_at": now}


def agent_row(a: dict, *, usdc: dict | None, signals: list[str], klass: str, block_times: dict) -> dict:
    m = a["meta"] or {}
    return {
        "id": a["agent_id"], "owner": a["owner"], "block": a["registered_block"], "tx": a["registered_tx"],
        "time": approx_time(a["registered_block"], block_times),
        "name": m.get("name"), "description": m.get("description"),
        "meta_status": m.get("status"), "meta_source": m.get("source"), "issues": m.get("issues") or [],
        "x402": m.get("x402"), "backlink": m.get("backlink"), "trust": m.get("trust") or [],
        "services": len(m.get("services") or []), "uri_kind": a["uri_kind"],
        "uri": a["uri"] if a["uri_kind"] == "HTTP" else None,
        "endpoint": {"url": a["endpoint"]["url"], "status": a["endpoint"]["status"],
                     "http_status": a["endpoint"]["http_status"],
                     "checked_at": iso(a["endpoint"]["checked_at"]) if a["endpoint"]["checked_at"] else None},
        "usdc": usdc, "feedback": a["feedback_count"], "validations": a["validation_count"],
        "metadata_keys": a["metadata_keys"], "signals": signals, "class": klass,
    }


def run(*, root: Path, rpc: ArcRpc | None = None, fetcher=None, now: int | None = None,
        max_forward_chunks: int = config.MAX_FORWARD_CHUNKS_PER_RUN,
        max_backfill_chunks: int = config.MAX_BACKFILL_CHUNKS_PER_RUN,
        initial_lookback: int = config.INITIAL_LOOKBACK_BLOCKS,
        skip_http: bool = False, log=print) -> dict:
    t0 = time.monotonic()
    root = Path(root)
    state_path, out_dir = root / "state" / "state.json", root / "snapshots"
    state = load_state(state_path)
    rpc = rpc or ArcRpc()
    fetcher = fetcher or safe_http.fetch
    now = int(now if now is not None else time.time())
    report = {"forward_chunks": 0, "backfill_chunks": 0, "metadata_fetches": 0, "endpoint_checks": 0, "revalidated": 0,
              "errors": [], "usdc_status": "SKIPPED"}

    rpc.require_chain(config.CHAIN_ID)                  # fail closed on anything but Arc mainnet
    head = rpc.block_number() - config.HEAD_SAFETY_BLOCKS
    if state["forward_cursor"] is None:
        start = max(state["floor_block"], head - initial_lookback + 1)
        state["forward_cursor"] = start - 1
        state["backfill_cursor"] = start
    book = AgentBook(state["agents"], state["counted_events"])
    run_from = state["forward_cursor"] + 1

    def scan(a: int, b: int) -> None:
        book.apply_logs(rpc.get_logs(a, b, REGISTRY_ADDRESSES))
        state["block_times"][str(b)] = rpc.block_timestamp(b)

    try:
        for a, b in block_chunks(state["forward_cursor"] + 1, head):
            if report["forward_chunks"] >= max_forward_chunks:
                break
            scan(a, b)
            state["forward_cursor"] = b
            report["forward_chunks"] += 1
        for a, b in block_chunks_desc(state["backfill_cursor"] - 1, state["floor_block"]):
            if report["backfill_chunks"] >= max_backfill_chunks:
                break
            scan(a, b)
            state["backfill_cursor"] = a
            report["backfill_chunks"] += 1
    except (BudgetExhausted, RpcError) as exc:
        report["errors"].append(f"LOG_SCAN:{exc}")
    to_block = state["forward_cursor"]
    report["revalidated"] = book.revalidate_stale()
    for a in book.agents.values():                 # keep endpoint status derived from one rule set
        ep = a["endpoint"]
        if ep["http_status"] is not None:
            ep["status"] = sig.classify_endpoint("OK", ep["http_status"])

    if not skip_http:
        fetch_metadata(book, now, fetcher, report)
        check_endpoints(book, now, fetcher, report)

    owners = book.owners()
    try:
        transfers, window = usdc_activity.scan_transfers(rpc, owners, to_block) if owners else ({}, None)
        balances = usdc_activity.fetch_balances(rpc, owners, to_block) if owners else {}
        state["usdc"] = {"window": window, "owners": {
            o: dict(transfers.get(o, {"transfers": 0, "in": 0, "out": 0, "last_tx": None, "last_block": None}),
                    balance=usdc_activity.format_usdc(balances[o]) if balances.get(o) is not None else None)
            for o in owners}}
        report["usdc_status"] = "FRESH"
    except (BudgetExhausted, RpcError) as exc:
        report["errors"].append(f"USDC:{exc}")
        report["usdc_status"] = "STALE"

    # ---- signals + snapshot -------------------------------------------------
    clusters, flags = sig.compute_clusters(book.agents)
    usdc_owners = state["usdc"]["owners"]
    rows, prev, cur = [], state["last_signals"], {}
    for key in sorted(book.agents, key=lambda k: -int(k)):
        a = book.agents[key]
        if a["registered_block"] is None:
            continue                                     # seen only via later events; wait for backfill
        u = usdc_owners.get(a["owner"])
        s = sig.agent_signals(a, snapshot_block=to_block, cluster_flags=flags.get(a["agent_id"], set()), usdc=u)
        cur[key] = s
        rows.append(agent_row(a, usdc=u, signals=s, klass=sig.classify(s), block_times=state["block_times"]))
    changes = [{"id": int(k), "before": prev.get(k, []), "after": v} for k, v in cur.items()
               if prev.get(k) is not None and prev.get(k) != v]
    changes.sort(key=lambda c: -c["id"])
    new_rows = [r for r in rows if r["id"] in book.added]

    def count(pred) -> int:
        return sum(1 for r in rows if pred(r))
    classes = defaultdict(int)
    for r in rows:
        classes[r["class"]] += 1
    stats = {
        "agents_observed": len(rows),
        "highest_agent_id": max((r["id"] for r in rows), default=None),
        "new_agents_24h": count(lambda r: "NEW_AGENT" in r["signals"]),
        "discovered_this_run": len(new_rows),
        "metadata_valid": count(lambda r: r["meta_status"] == "VALID"),
        "metadata_invalid": count(lambda r: r["meta_status"] == "INVALID"),
        "metadata_unverified": count(lambda r: r["meta_status"] not in ("VALID", "INVALID")),
        "endpoints_declared": count(lambda r: r["endpoint"]["url"] is not None),
        "endpoint_live": count(lambda r: r["endpoint"]["status"] == "LIVE"),
        "endpoint_http_error": count(lambda r: r["endpoint"]["status"] == "HTTP_ERROR"),
        "endpoint_unreachable": count(lambda r: r["endpoint"]["status"] == "UNREACHABLE"),
        "endpoint_blocked": count(lambda r: r["endpoint"]["status"] == "BLOCKED"),
        "endpoint_pending": count(lambda r: r["endpoint"]["status"] == "PENDING"),
        "x402_declared": count(lambda r: r["x402"] is True),
        "http_402_observed": count(lambda r: "HTTP_402_OBSERVED" in r["signals"]),
        "usdc_active_agents": count(lambda r: "USDC_ACTIVITY_PRESENT" in r["signals"]),
        "usdc_active_owners": sum(1 for o in usdc_owners.values() if o.get("transfers", 0) > 0),
        "owners": len(owners),
        "reputation_agents": count(lambda r: r["feedback"] > 0),
        "validation_agents": count(lambda r: r["validations"] > 0),
        "clusters": len(clusters),
        "clustered_agents": count(lambda r: "MASS_REGISTRATION_CLUSTER" in r["signals"]),
        "classes": dict(sorted(classes.items())),
    }
    sid = f"s-{to_block}"
    rpc_stats = rpc.stats()
    snapshot = {
        "schema": SNAPSHOT_SCHEMA, "snapshot_id": sid, "action_id": action_id(sid),
        "created_at": iso(now), "chain_id": config.CHAIN_ID, "network": config.NETWORK,
        "scanner_version": config.SCANNER_VERSION,
        "block_range": {"from": run_from, "to": to_block,
                        "to_block_time": iso(state["block_times"][str(to_block)])
                        if str(to_block) in state["block_times"] else None},
        "coverage": {"forward_cursor": state["forward_cursor"], "backfill_cursor": state["backfill_cursor"],
                     "registry_deploy_block": state["floor_block"],
                     "backfill_complete": state["backfill_cursor"] <= state["floor_block"],
                     "usdc_window": state["usdc"]["window"], "usdc_status": report["usdc_status"]},
        "registries": {"identity": config.IDENTITY_REGISTRY, "reputation": config.REPUTATION_REGISTRY,
                       "validation": config.VALIDATION_REGISTRY},
        "stats": stats, "clusters": clusters[:50],
        "new_agents": new_rows, "signal_changes": changes[:500], "signal_changes_total": len(changes),
        "data_sources": {"rpc_url": config.RPC_URL, "explorer": config.EXPLORER_URL,
                         "rpc": rpc_stats, "metadata_fetches": report["metadata_fetches"],
                         "endpoint_checks": report["endpoint_checks"], "errors": report["errors"],
                         "revalidated": report["revalidated"],
                         "forward_chunks": report["forward_chunks"], "backfill_chunks": report["backfill_chunks"]},
        "notice": "Signals are explainable observations. HEURISTIC signals flag patterns for review and are "
                  "not judgements about any project or person. Agent metadata is untrusted third-party content.",
    }
    directory = {"schema": DIRECTORY_SCHEMA, "snapshot_id": sid, "chain_id": config.CHAIN_ID, "to_block": to_block,
                 "signal_defs": {k: {"kind": v[0], "explanation": v[1]} for k, v in sig.SIGNAL_DEFS.items()},
                 "class_defs": sig.CLASS_DEFS, "agents": rows}
    entry = publish(out_dir, snapshot, directory)

    state.update(agents=book.agents, counted_events=sorted(book.counted), last_signals=cur,
                 runs=state["runs"] + 1)
    save_state(state_path, state)
    result = {"snapshot": entry, "stats": stats, "report": report, "rpc": rpc_stats,
              "runtime_s": int(round(time.monotonic() - t0)), "decode_errors": book.decode_errors}
    log(json.dumps(result, indent=1))
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Pulse Arc Agent Radar - read-only scan run")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    p.add_argument("--max-forward-chunks", type=int, default=config.MAX_FORWARD_CHUNKS_PER_RUN)
    p.add_argument("--max-backfill-chunks", type=int, default=config.MAX_BACKFILL_CHUNKS_PER_RUN)
    p.add_argument("--max-rpc-calls", type=int, default=config.MAX_RPC_CALLS_PER_RUN)
    p.add_argument("--skip-http", action="store_true")
    args = p.parse_args(argv)
    rpc = ArcRpc(max_calls=args.max_rpc_calls)
    run(root=Path(args.root), rpc=rpc, max_forward_chunks=args.max_forward_chunks,
        max_backfill_chunks=args.max_backfill_chunks, skip_http=args.skip_http)
    return 0


if __name__ == "__main__":
    sys.exit(main())
