# Security model

## What this repository can do
- Send **read-only** JSON-RPC calls to `https://rpc.mainnet.arc.io`. `ArcRpc.call` rejects any method outside `READ_ONLY_METHODS`, and tests confirm that `eth_sendRawTransaction`, `eth_sign` and similar methods raise `PermissionError`.
- Send bounded HTTP GETs to URLs that agents published about themselves.
- Write JSON files under `snapshots/` and `state/`.

## What it cannot do
- It cannot hold, load, derive or request a private key or mnemonic. `tests/test_safety.py::test_no_key_or_signing_code` scans `src/`, `scripts/`, `public/` and `.github/`.
- It cannot sign or broadcast anything. The workflow has no secrets and the scanner has no write path.
- `scripts/contract_facts.js` only compiles the contract and writes an **unsigned** plan (`signed: false`, `broadcast: false`, `deployed_address: null`).

Mainnet writes (deploying the registry, registering the Pulse agent, anchoring snapshots) were a separate Phase 2, now complete: 5 transactions under an owner grant that is now exhausted. Each one is shown to the owner in full and signed locally by the owner under a scoped grant. Keys never go into CI.

## RPC discipline
- `eth_getLogs` ranges are at most 9,999 blocks (Arc rejects 10,000 or more with `-32012`).
- At least 1 s between calls, a hard per-run ceiling of 250 calls (`BudgetExhausted` ends a phase cleanly), and exponential backoff with bounded jitter on `-32005`, HTTP 429 or 5xx, honouring `Retry-After`. Retries are bounded at 5.
- The scanner checks the chain id before any work. Anything other than `5042` fails closed with no snapshot written.
- Scans are bounded: forward catch-up is limited to 24 chunks per run and backfill to 60 chunks per run, walking back to the registry deploy block and then stopping.

## Untrusted agent metadata
| Threat | Control |
|---|---|
| XSS through names or descriptions | The dashboard writes only through `textContent`. Tests forbid `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function` and string timers. The CSP is `script-src 'self'` with no inline scripts. |
| Malicious links (`javascript:`, `data:`) | Every link goes through `safeHref` (http/https only, no userinfo) and gets `rel="nofollow noopener noreferrer"` and `target=_blank`. |
| Tracking images or hotlinks | Agent images are never loaded; the page records only whether one is declared. The CSP limits images to `'self' data:`. |
| Bidi or control-character spoofing | Removed at ingest (`sanitize_text`), and lengths are capped. |
| SSRF from the CI runner | Only http/https on default ports, no userinfo, no non-ASCII URLs. **Every** resolved IP must be public, so loopback, RFC1918, link-local (including 169.254.169.254), CGNAT, ULA, multicast, reserved and IPv4-mapped addresses are all blocked. The connection is **pinned to the validated IP** to defeat DNS rebinding. Each redirect is re-validated, with at most 2. |
| Resource exhaustion | Registration files are capped at 64 KiB, endpoint bodies at 4 KiB, and requests time out after 6 s. Each run makes at most 40 metadata fetches and 40 endpoint checks, with at most 10 per host. |
| Accusation / defamation | Heuristic signals are labelled HEURISTIC. A test forbids words like "scam" and "fraud" in signal text. |

## Integrity
- Snapshot files are canonical bytes. `.gitattributes` marks `snapshots/**` as `-text` so git never rewrites line endings.
- `index.json` keeps the hash of every snapshot, including snapshots whose files were pruned by retention.
- The browser verifier checks the file hash, the canonical hash, the index entry and the actionId. It reports an anchor only when `proofHashByAction(actionId)` on the configured registry returns that exact hash.
