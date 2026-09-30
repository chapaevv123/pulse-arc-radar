"""Read-only, paced, budgeted JSON-RPC client for the Arc mainnet public RPC.

Only an allowlist of read methods can be sent. There is no signing code, no
key handling and no transaction submission path anywhere in this package.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from . import config

READ_ONLY_METHODS = frozenset({
    "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getLogs",
    "eth_call", "eth_getBalance", "eth_getTransactionCount",
    "eth_getTransactionReceipt", "eth_getCode",
    # read-only fee/simulation helpers used by scripts/preflight.py
    "eth_estimateGas", "eth_gasPrice", "eth_maxPriorityFeePerGas", "eth_feeHistory",
})
RATE_LIMIT_CODES = frozenset({-32005, 429})
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class RpcError(Exception):
    def __init__(self, code: int | None, message: str):
        super().__init__(f"{code}:{message}")
        self.code = code
        self.message = message


class BudgetExhausted(Exception):
    """The per-run RPC call ceiling was reached; callers stop that phase."""


class ChainMismatch(Exception):
    pass


def block_chunks(start: int, end: int, size: int = config.MAX_LOG_RANGE) -> Iterator[tuple[int, int]]:
    """Ascending inclusive ranges covering [start, end], each at most `size` blocks."""
    if size < 1 or size > config.MAX_LOG_RANGE:
        raise ValueError("CHUNK_SIZE_OUT_OF_BOUNDS")
    a = start
    while a <= end:
        b = min(a + size - 1, end)
        yield a, b
        a = b + 1


def block_chunks_desc(high: int, low: int, size: int = config.MAX_LOG_RANGE) -> Iterator[tuple[int, int]]:
    """Descending inclusive ranges covering [low, high], newest first."""
    if size < 1 or size > config.MAX_LOG_RANGE:
        raise ValueError("CHUNK_SIZE_OUT_OF_BOUNDS")
    b = high
    while b >= low:
        a = max(b - size + 1, low)
        yield a, b
        b = a - 1


def _urllib_transport(url: str, timeout: float) -> Callable[[bytes], tuple[int, dict, dict | None]]:
    def send(payload: bytes) -> tuple[int, dict, dict | None]:
        req = urllib.request.Request(url, data=payload, method="POST", headers={
            "content-type": "application/json", "user-agent": config.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RpcError(None, "RESPONSE_TOO_LARGE")
                return resp.status, dict(resp.headers), json.loads(body)
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read(65536) or b"null")
            except ValueError:
                parsed = None
            return exc.code, dict(exc.headers or {}), parsed
    return send


class ArcRpc:
    def __init__(self, url: str = config.RPC_URL, *, transport=None,
                 min_interval: float = config.MIN_CALL_INTERVAL_S,
                 max_retries: int = config.MAX_RETRIES,
                 backoff_base: float = config.BACKOFF_BASE_S,
                 backoff_cap: float = config.BACKOFF_CAP_S,
                 max_calls: int = config.MAX_RPC_CALLS_PER_RUN,
                 sleep=time.sleep, clock=time.monotonic, jitter=random.random,
                 timeout: float = 45.0):
        self.url = url
        self._send = transport or _urllib_transport(url, timeout)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.max_calls = max_calls
        self._sleep, self._clock, self._jitter = sleep, clock, jitter
        self._last = None
        self._id = 0
        self._ts_cache: dict[int, int] = {}
        self.calls = 0
        self.calls_by_method: dict[str, int] = {}
        self.rate_limit_events = 0
        self.transient_errors = 0
        self.backoff_seconds = 0.0

    # -- core ---------------------------------------------------------------
    def _pace(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = min(self.backoff_cap, self.backoff_base * (2 ** attempt))
        delay += delay * 0.25 * self._jitter()          # bounded jitter
        if retry_after is not None:
            delay = max(delay, min(retry_after, self.backoff_cap))
        self.backoff_seconds += delay
        self._sleep(delay)

    def call(self, method: str, params: list) -> Any:
        if method not in READ_ONLY_METHODS:
            raise PermissionError(f"RPC_METHOD_NOT_READ_ONLY:{method}")
        last_error: RpcError | None = None
        for attempt in range(self.max_retries + 1):
            if self.calls >= self.max_calls:
                raise BudgetExhausted(f"MAX_RPC_CALLS_PER_RUN={self.max_calls}")
            self._pace()
            self.calls += 1
            self.calls_by_method[method] = self.calls_by_method.get(method, 0) + 1
            self._id += 1
            payload = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                                  "params": params}).encode()
            try:
                status, headers, body = self._send(payload)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                self.transient_errors += 1
                last_error = RpcError(None, f"TRANSPORT:{type(exc).__name__}")
                self._backoff(attempt, None)
                continue
            err = (body or {}).get("error") if isinstance(body, dict) else None
            code = err.get("code") if isinstance(err, dict) else None
            if status == 429 or code in RATE_LIMIT_CODES:
                self.rate_limit_events += 1
                retry_after = None
                try:
                    retry_after = float({k.lower(): v for k, v in headers.items()}.get("retry-after"))
                except (TypeError, ValueError):
                    pass
                last_error = RpcError(code or 429, "RATE_LIMITED")
                self._backoff(attempt, retry_after)
                continue
            if status >= 500:
                self.transient_errors += 1
                last_error = RpcError(status, "HTTP_5XX")
                self._backoff(attempt, None)
                continue
            if err is not None:
                raise RpcError(code, str(err.get("message", "")))
            if not isinstance(body, dict) or "result" not in body:
                raise RpcError(None, f"MALFORMED_RESPONSE:http={status}")
            return body["result"]
        raise last_error or RpcError(None, "RETRIES_EXHAUSTED")

    # -- helpers ------------------------------------------------------------
    def chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def require_chain(self, expected: int = config.CHAIN_ID) -> None:
        got = self.chain_id()
        if got != expected:
            raise ChainMismatch(f"CHAIN_ID_MISMATCH:expected={expected}:got={got}")

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def block_timestamp(self, number: int) -> int:
        if number not in self._ts_cache:
            block = self.call("eth_getBlockByNumber", [hex(number), False])
            if not block:
                raise RpcError(None, f"BLOCK_NOT_FOUND:{number}")
            self._ts_cache[number] = int(block["timestamp"], 16)
        return self._ts_cache[number]

    def get_logs(self, from_block: int, to_block: int, address, topics=None) -> list[dict]:
        if to_block < from_block:
            return []
        if to_block - from_block + 1 > config.MAX_LOG_RANGE:
            raise ValueError("LOG_RANGE_TOO_LARGE")
        flt: dict[str, Any] = {"fromBlock": hex(from_block), "toBlock": hex(to_block), "address": address}
        if topics is not None:
            flt["topics"] = topics
        result = self.call("eth_getLogs", [flt])
        return result or []

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        return self.call("eth_call", [{"to": to, "data": data}, tag])

    def stats(self) -> dict:
        return {
            "rpc_calls": self.calls,
            "get_logs_calls": self.calls_by_method.get("eth_getLogs", 0),
            "calls_by_method": dict(sorted(self.calls_by_method.items())),
            "rate_limit_events": self.rate_limit_events,
            "transient_errors": self.transient_errors,
            "backoff_seconds": int(round(self.backoff_seconds)),
        }
