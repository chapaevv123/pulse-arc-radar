"""USDC activity for agent owner addresses (read-only).

Two cheap, bounded measurements:
  * transfers: USDC Transfer logs (ERC-20 view at 0x3600… plus Arc's EIP-7708
    native-transfer logs) where an owner is sender or recipient, inside a
    rolling window ending at the snapshot block. Owners are batched into one
    topic filter per side per chunk.
  * balance: current native USDC balance (18 decimals) via Multicall3
    getEthBalance, batched.
Gas payments do not emit logs on Arc, so a registration alone never counts
as USDC activity.
"""

from __future__ import annotations

from . import config
from .erc8004 import TOPIC_TRANSFER, topic_address
from .rpc import block_chunks

SEL_AGGREGATE3 = "82ad56cb"
SEL_GET_ETH_BALANCE = "4d2301cc"
USDC_SOURCES = [config.USDC_ERC20, config.NATIVE_TRANSFER_EMITTER]


def _pad_address(addr: str) -> str:
    return "0x" + addr.lower()[2:].rjust(64, "0")


def parse_transfer_logs(logs: list[dict], owners: set[str]) -> dict[str, dict]:
    """Aggregate Transfer logs into per-owner activity.

    A single move can appear both as an ERC-20 log and as a native log in the
    same transaction; (tx, from, to) is counted once.
    """
    seen: set[tuple[str, str, str]] = set()
    out: dict[str, dict] = {}
    for log in sorted(logs, key=lambda x: (int(x["blockNumber"], 16), int(x["logIndex"], 16))):
        topics = log.get("topics") or []
        if len(topics) < 3 or topics[0].lower() != TOPIC_TRANSFER:
            continue
        if log["address"].lower() not in USDC_SOURCES:
            continue
        src, dst = topic_address(topics[1]), topic_address(topics[2])
        key = (log["transactionHash"].lower(), src, dst)
        if key in seen:
            continue
        seen.add(key)
        block = int(log["blockNumber"], 16)
        for addr, direction in ((src, "out"), (dst, "in")):
            if addr not in owners:
                continue
            rec = out.setdefault(addr, {"transfers": 0, "in": 0, "out": 0, "last_tx": None, "last_block": None})
            rec["transfers"] += 1
            rec[direction] += 1
            if rec["last_block"] is None or block >= rec["last_block"]:
                rec["last_block"], rec["last_tx"] = block, log["transactionHash"].lower()
    return out


def encode_aggregate3_balances(addresses: list[str]) -> str:
    n = len(addresses)
    words = ["20", format(n, "x")]                      # offset to array, length
    tuple_size = 32 * 3 + 32 + 64                          # target, allowFailure, bytes offset, len, 36 bytes padded
    words += [format(n * 32 + i * tuple_size, "x") for i in range(n)]
    for addr in addresses:
        call = SEL_GET_ETH_BALANCE + addr.lower()[2:].rjust(64, "0")
        words += [config.MULTICALL3[2:], "1", "60", format(36, "x"), call[:64], call[64:].ljust(64, "0")]
    return "0x" + SEL_AGGREGATE3 + "".join(w.rjust(64, "0") for w in words)


def decode_aggregate3_balances(result_hex: str, count: int) -> list[int | None]:
    raw = bytes.fromhex(result_hex[2:])
    word = lambda off: int.from_bytes(raw[off:off + 32], "big")
    base = word(0)
    n = word(base)
    if n != count:
        raise ValueError("MULTICALL_COUNT_MISMATCH")
    head = base + 32
    out: list[int | None] = []
    for i in range(n):
        t = head + word(head + 32 * i)
        success = word(t) == 1
        data_off = t + word(t + 32)
        length = word(data_off)
        out.append(int.from_bytes(raw[data_off + 32: data_off + 32 + length], "big") if success and length == 32 else None)
    return out


def format_usdc(native_wei: int) -> str:
    """18-decimal native amount → USDC string with 6 decimals (truncated)."""
    micro = native_wei // 10**12
    return f"{micro // 10**6}.{micro % 10**6:06d}"


def scan_transfers(rpc, owners: list[str], to_block: int, window: int = config.USDC_WINDOW_BLOCKS,
                   batch: int = config.USDC_OWNER_BATCH) -> tuple[dict[str, dict], dict]:
    owner_set = set(owners)
    start = max(0, to_block - window + 1)
    logs: list[dict] = []
    for a, b in block_chunks(start, to_block):
        for i in range(0, len(owners), batch):
            group = [_pad_address(x) for x in owners[i:i + batch]]
            logs += rpc.get_logs(a, b, USDC_SOURCES, [TOPIC_TRANSFER, group])
            logs += rpc.get_logs(a, b, USDC_SOURCES, [TOPIC_TRANSFER, None, group])
    return parse_transfer_logs(logs, owner_set), {"from_block": start, "to_block": to_block}


def fetch_balances(rpc, owners: list[str], block: int, batch: int = config.USDC_OWNER_BATCH) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for i in range(0, len(owners), batch):
        group = owners[i:i + batch]
        values = decode_aggregate3_balances(rpc.eth_call(config.MULTICALL3, encode_aggregate3_balances(group), block),
                                            len(group))
        out.update(zip(group, values))
    return out
