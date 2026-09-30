# Architecture

```
┌──────────────────────┐  eth_chainId / eth_blockNumber / eth_getLogs (≤9,999 blocks)
│ Arc mainnet RPC      │  eth_getBlockByNumber (chunk-end timestamps) / eth_call (Multicall3)
│ rpc.mainnet.arc.io   │◄──────────────────────────────────────────────┐
└──────────────────────┘                                               │ paced ≥1 s, backoff on -32005
                                                                       │ ≤250 calls/run
┌──────────────────────────── GitHub Actions: hourly, no secrets ──────┴─────────────┐
│ src/scanner/run.py                                                                   │
│  1. require chainId == 5042 (fail closed)                                            │
│  2. forward scan  forward_cursor+1 → head−3   (≤24 chunks)                           │
│  3. backfill      backfill_cursor−1 → registry deploy block 13,344,062 (≤60 chunks)  │
│       erc8004.AgentBook: Registered / URIUpdated / MetadataSet / Transfer,           │
│       NewFeedback, ValidationRequest/Response; newest (block, logIndex) wins        │
│  4. metadata: data: URIs decoded inline; https fetched via safe_http (≤40, ≤10/host)  │
│  5. endpoints: primary http(s) service endpoint, re-checked every 24h (≤40/run)      │
│  6. USDC: Transfer logs (0x3600… + EIP-7708 emitter) for owners, rolling 43,200     │
│     blocks; Multicall3.getEthBalance for balances                                    │
│  7. signals.py → clusters + per-agent signals + class                                │
│  8. snapshot.py → canonical bytes → SHA-256 → s-<block>.json, latest.json,           │
│     directory.json (hash embedded in snapshot), index.json, anchors.json             │
│  9. state/state.json written atomically once, at the end                             │
└───────────────────────────────┬──────────────────────────────────────────────────────┘
                                │ git commit (only if changed) + Pages artifact
                                ▼
┌──────────────────────── GitHub Pages (static) ─────────────────────────┐
│ index.html + app.js: tiles, timeline, agents table, clusters,          │
│   Pulse on Arc, signal definitions (all untrusted text via textContent)│
│ verify.js: SHA-256 of the raw file + canonical re-serialization +      │
│   index + actionId; anchor lookup via eth_call proofHashByAction       │
└────────────────────────────────────────────────────────────────────────┘

Phase 2 (owner-approved, local, never CI; completed 2026-09-30):
  recordProof(actionId, proofHash) ──► PulseActionProofRegistry (Arc mainnet)
  register(agentURI)               ──► ERC-8004 IdentityRegistry 0x8004A169… (Pulse agent)
```

## Cursor semantics
- `forward_cursor` is the highest block fully scanned. `backfill_cursor` is the lowest block fully scanned.
- A cursor advances only after a chunk has been fully applied. State is saved once, atomically, so an interrupted run resumes from the last saved state and never double-counts.
- Counted events (feedback, validation) are keyed by `block:logIndex`. Agents are keyed by agentId. Duplicate log delivery is idempotent.

## Why static
There is no database service, so there are no D1-style read quotas. Snapshots are small files served by a CDN, and every viewer can check them independently. The browser's only live calls are `eth_blockNumber` (the live head) and, after Phase 2, `eth_call` for anchors. The Arc RPC allows cross-origin requests.
