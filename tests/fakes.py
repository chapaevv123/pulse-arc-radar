"""Offline fakes: an in-memory Arc chain behind the JSON-RPC transport seam,
and a fake HTTP fetcher. Tests never touch the network."""

from __future__ import annotations

import base64
import json

from src.scanner import config
from src.scanner.erc8004 import (TOPIC_NEW_FEEDBACK, TOPIC_REGISTERED, TOPIC_TRANSFER,
                                 TOPIC_URI_UPDATED)
from src.scanner.safe_http import FetchResult
from src.scanner.usdc_activity import SEL_AGGREGATE3


def word(n: int) -> str:
    return format(n, "x").rjust(64, "0")


def topic_addr(addr: str) -> str:
    return "0x" + addr.lower()[2:].rjust(64, "0")


def abi_string(s: str) -> str:
    b = s.encode()
    padded = b.hex().ljust(((len(b) + 31) // 32) * 64, "0")
    return "0x" + word(32) + word(len(b)) + padded


def registration_doc(name="Agent", *, x402=None, services=None, agent_id=None, extra=None) -> dict:
    doc = {"type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1", "name": name,
           "description": f"{name} description", "services": services if services is not None else [],
           "registrations": [{"agentRegistry": f"eip155:5042:{config.IDENTITY_REGISTRY}",
                              **({"agentId": agent_id} if agent_id is not None else {})}]}
    if x402 is not None:
        doc["x402Support"] = x402
    doc.update(extra or {})
    return doc


def data_uri(doc: dict) -> str:
    return "data:application/json;base64," + base64.b64encode(json.dumps(doc).encode()).decode()


def mk_log(address, topics, data, block, li, tx=None):
    return {"address": address, "topics": topics, "data": data, "blockNumber": hex(block),
            "logIndex": hex(li), "transactionHash": tx or "0x" + format(block * 1000 + li, "x").rjust(64, "0")}


def registered(agent_id, owner, uri, block, li=0):
    return mk_log(config.IDENTITY_REGISTRY, [TOPIC_REGISTERED, "0x" + word(agent_id), topic_addr(owner)],
                  abi_string(uri), block, li)


def uri_updated(agent_id, uri, block, li=0, by="0x" + "1" * 40):
    return mk_log(config.IDENTITY_REGISTRY, [TOPIC_URI_UPDATED, "0x" + word(agent_id), topic_addr(by)],
                  abi_string(uri), block, li)


def feedback(agent_id, block, li=0, client="0x" + "2" * 40):
    return mk_log(config.REPUTATION_REGISTRY, [TOPIC_NEW_FEEDBACK, "0x" + word(agent_id), topic_addr(client)],
                  "0x", block, li)


def usdc_transfer(src, dst, block, li=0, emitter=config.USDC_ERC20, tx=None):
    return mk_log(emitter, [TOPIC_TRANSFER, topic_addr(src), topic_addr(dst)], "0x" + word(1), block, li, tx)


def encode_aggregate3_result(values: list[int]) -> str:
    n = len(values)
    words = [word(32), word(n)]
    tuple_size = 32 * 4
    words += [word(n * 32 + i * tuple_size) for i in range(n)]
    for v in values:
        words += [word(1), word(64), word(32), word(v)]
    return "0x" + "".join(words)


class FakeChain:
    """Serves the read-only methods the scanner uses from an in-memory log list."""

    def __init__(self, head: int, logs=None, chain_id=config.CHAIN_ID, balances=None, script=None):
        self.head, self.logs, self.chain_id = head, list(logs or []), chain_id
        self.balances = balances or {}
        self.script = list(script or [])        # queued (status, headers, body) overrides
        self.requests: list[dict] = []

    def __call__(self, payload: bytes):
        req = json.loads(payload)
        self.requests.append(req)
        if self.script:
            return self.script.pop(0)
        m, p = req["method"], req["params"]
        if m == "eth_chainId":
            return self._ok(req, hex(self.chain_id))
        if m == "eth_blockNumber":
            return self._ok(req, hex(self.head))
        if m == "eth_getBlockByNumber":
            n = int(p[0], 16)
            return self._ok(req, {"number": p[0], "timestamp": hex(1_790_000_000 + n // 2)})
        if m == "eth_getLogs":
            f = p[0]
            a, b = int(f["fromBlock"], 16), int(f["toBlock"], 16)
            if b - a + 1 > 10_000:
                return 200, {}, {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32012, "message": "range"}}
            addrs = f["address"] if isinstance(f["address"], list) else [f["address"]]
            addrs = [x.lower() for x in addrs]
            out = []
            for lg in self.logs:
                blk = int(lg["blockNumber"], 16)
                if not (a <= blk <= b) or lg["address"].lower() not in addrs:
                    continue
                if not self._topics_match(lg["topics"], f.get("topics")):
                    continue
                out.append(lg)
            return self._ok(req, out)
        if m == "eth_call":
            data = p[0]["data"]
            if data.startswith("0x" + SEL_AGGREGATE3):
                raw = data[10:]
                n = int(raw[64:128], 16)
                addrs = []
                for i in range(n):
                    off = (int(raw[128 + i * 64: 192 + i * 64], 16) + 64) * 2
                    tuple_hex = raw[off:]
                    call = tuple_hex[256:256 + 72]
                    addrs.append("0x" + call[8 + 24: 8 + 64])
                return self._ok(req, encode_aggregate3_result([self.balances.get(x, 0) for x in addrs]))
            return self._ok(req, "0x" + word(0))
        return 200, {}, {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "nope"}}

    @staticmethod
    def _topics_match(topics, flt):
        if not flt:
            return True
        for i, want in enumerate(flt):
            if want is None:
                continue
            if i >= len(topics):
                return False
            options = want if isinstance(want, list) else [want]
            if topics[i].lower() not in [o.lower() for o in options]:
                return False
        return True

    @staticmethod
    def _ok(req, result):
        return 200, {}, {"jsonrpc": "2.0", "id": req["id"], "result": result}


class FakeFetcher:
    def __init__(self, responses=None, default=None):
        self.responses = responses or {}
        self.default = default or FetchResult("UNREACHABLE", error="FAKE")
        self.calls: list[str] = []

    def __call__(self, url, *, max_bytes):
        self.calls.append(url)
        res = self.responses.get(url, self.default)
        if res.outcome == "OK" and len(res.body) > max_bytes:
            return FetchResult("TOO_LARGE", res.http_status, final_url=url, error="BODY_CAP")
        return res


def fast_rpc(chain, **kw):
    from src.scanner.rpc import ArcRpc
    sleeps: list[float] = []
    rpc = ArcRpc(transport=chain, min_interval=0, sleep=sleeps.append, jitter=lambda: 0.0, **kw)
    rpc.sleeps = sleeps
    return rpc
