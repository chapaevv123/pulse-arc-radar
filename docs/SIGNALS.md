# Signals

Every signal is either **EVIDENCE** (a direct restatement of an observed log, decoded file or HTTP status) or a **HEURISTIC** (a pattern worth a human look). No signal is a verdict about a project or person. Thresholds live in `src/scanner/config.py`, and the live definitions are published in `snapshots/directory.json → signal_defs`.

| Signal | Kind | Rule |
|---|---|---|
| NEW_AGENT | evidence | Registered within 172,800 blocks (about 24h) of the snapshot block |
| METADATA_VALID | evidence | The registration file parses and has no blocking issue |
| METADATA_INVALID | evidence | A blocking issue (below) |
| METADATA_UNVERIFIED | evidence | Pending fetch, `ipfs://`, or an unsupported URI scheme |
| ENDPOINT_LIVE | evidence | The probed endpoint (an `x402*`-named service if present, else the first http(s) service) answered 2xx/3xx, or 400/401/402/403/405/406/422/429 (the server is up; the bare GET lacked parameters, auth or payment, which is typical for x402 endpoints) |
| ENDPOINT_HTTP_ERROR | evidence | It answered another 4xx (e.g. 404/410) or a 5xx |
| ENDPOINT_UNREACHABLE | evidence | DNS, connect, TLS or timeout failure |
| ENDPOINT_BLOCKED | evidence | Not fetched because of the SSRF/URL rules |
| X402_DECLARED | evidence | `x402Support === true` (a strict boolean) |
| HTTP_402_OBSERVED | evidence | The endpoint answered `402 Payment Required` |
| USDC_ACTIVITY_PRESENT | evidence | The owner sent or received USDC in the rolling window of about 6h (43,200 blocks). Gas payments emit no logs on Arc and are excluded. |
| REPUTATION_FEEDBACK_PRESENT | evidence | At least one `NewFeedback` targets the agent |
| VALIDATION_ACTIVITY_PRESENT | evidence | At least one `ValidationRequest` or `ValidationResponse` targets the agent |
| MASS_REGISTRATION_CLUSTER | heuristic | The owner controls 20 or more agents |
| HIGH_REGISTRATION_DENSITY | heuristic | 10 or more same-owner registrations in the 7,200 blocks (about 1h) ending at this one |
| IDENTICAL_METADATA_CLUSTER | heuristic | A byte-identical agentURI is shared by 3 or more agents |

**Blocking metadata issues:** `NOT_JSON`, `NOT_JSON_OBJECT`, `TYPE_MISMATCH` (type ≠ `…eip-8004#registration-v1`), `NAME_MISSING`, `SERVICES_MALFORMED`, `URI_EMPTY`, `DATA_URI_MALFORMED`, `TOO_LARGE`, and `BACKLINK_MISMATCH` (the file names this registry with a different agentId).

**Warnings (not blocking):** `TYPE_NONCANONICAL` (the bare `…/EIPS/eip-8004` URL without the `#registration-v1` fragment), `DESCRIPTION_MISSING`, `SERVICES_ABSENT`, `LEGACY_ENDPOINTS_KEY`, `X402_FIELD_MALFORMED`, `BACKLINK_ABSENT`, and `BACKLINK_NO_AGENT_ID` (the file names this registry but gives no agentId).

## Pulse class
Classes are evaluated in this order:
1. **CLUSTERED**: in a mass-registration cluster.
2. **INCOMPLETE**: metadata is not VALID.
3. **STANDOUT**: valid metadata and a live endpoint.
4. **ACTIVE**: valid metadata and USDC, reputation or validation activity.
5. **BACKGROUND**: everything else.

The headline "Pulse signal" is the first signal in the priority list in `signals.py`.

## Known limitations
- Registration times are interpolated between chunk-boundary block timestamps and marked ≈.
- USDC activity is measured on the **owner** address. The optional `agentWallet` metadata value is not decoded in V1.
- One byte-identical transfer seen through both the ERC-20 view and the native log in the same transaction counts once. It is deduplicated by (tx, from, to).
- `ipfs://` registration files are not fetched in V1, because there is no trusted gateway.
