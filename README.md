# Pulse Arc Agent Radar

**An open intelligence radar for the Arc agent economy, with every snapshot anchored and verifiable on Arc mainnet.**

Pulse Arc Agent Radar watches Arc mainnet's ERC-8004 registries and USDC activity. It surfaces new AI agents as they register, checks each one, and publishes explainable signals with evidence links. Every snapshot is a canonical JSON file with a SHA-256 hash, and anyone can re-verify that hash in the browser.

- **Live demo:** https://chapaevv123.github.io/pulse-arc-radar/
- **Builder:** [@MagnatSV](https://x.com/MagnatSV), builder wallet `0x6Fa3659a15e9264E43EC67Fe4bB5d31D38109A42`
- **Pulse agent identity (mainnet, pending):** owner `0x765fb7e6a0BdDDc29f57eeCE34AEda0Fb318805d`, registration file [`agent/erc8004-registration.json`](https://chapaevv123.github.io/pulse-arc-radar/agent/erc8004-registration.json)
- **Testnet predecessor:** [chapaevv123/pulse-arc](https://github.com/chapaevv123/pulse-arc). It has the same proof-registry contract (deployed and RPC-verified on Arc Testnet) and Pulse's ERC-8004 testnet identity, agent #894567.

> **Status: read-only MVP.** Scanning, signals, snapshots and in-browser verification are live.
> The Arc mainnet registry contract, Pulse's mainnet ERC-8004 identity, and snapshot anchoring are
> **PENDING MAINNET DEPLOYMENT**. The dashboard labels them that way and never displays a fake anchor.

## What it does

| | |
|---|---|
| **Agent discovery** | Reads `Registered`, `URIUpdated`, `MetadataSet` and `Transfer` events from the ERC-8004 IdentityRegistry. It covers the complete history since the registry was deployed (block 13,344,062). |
| **Registration timeline** | Shows registrations per day. Times are interpolated from block timestamps and marked as approximate (≈). |
| **Metadata validation** | Decodes `data:` URIs inline and fetches `https:` registration files with strict limits. It checks the ERC-8004 `registration-v1` shape and whether the file links back to this registry and agent id. |
| **Endpoint status** | Makes one bounded GET per declared service endpoint and classifies the result as LIVE, HTTP_ERROR, UNREACHABLE or BLOCKED. |
| **x402** | `X402_DECLARED` means the registration file declares `x402Support: true`. `HTTP_402_OBSERVED` means the endpoint really answered `402 Payment Required`. The radar reports these as two separate facts. |
| **Reputation / validation** | Counts `NewFeedback` events and `ValidationRequest`/`ValidationResponse` events per agent. |
| **USDC activity** | Counts USDC transfers by each agent's owner in a rolling window of about 6 hours. It reads both the ERC-20 view at `0x3600…0000` and Arc's EIP-7708 native-transfer logs. It also reads each owner's current native USDC balance through Multicall3. |
| **Cluster heuristics** | Flags mass registrations by one owner, high registration density, and byte-identical metadata. These are **heuristics for review, never verdicts**. |
| **Explainable signals** | Each agent gets a list of signals and a Pulse class (STANDOUT / ACTIVE / BACKGROUND / CLUSTERED / INCOMPLETE). Every signal has a published definition. |
| **Evidence** | Every row links to its registration transaction, block, owner address and the registry on `explorer.arc.io`. |
| **Snapshots** | `snapshots/latest.json`, a history of `s-<block>.json` files, and `index.json` holding every snapshot hash. |
| **Verification** | The page recomputes the file hash, the canonical hash and the actionId in your browser, and looks up the onchain anchor once a registry exists. |

## Why Arc

- **ERC-8004 is live on Arc mainnet.** The radar watches Arc's own agent-identity, reputation and validation registries, not generic block data.
- **USDC is the native gas token and the unit of account.** USDC activity is the natural activity signal for agents that get paid, so the radar reads it directly.
- **Instant, deterministic finality.** A block number is a final reference, which is why snapshots are keyed by block.
- **Cheap anchoring.** At Arc's 20 gwei base fee, anchoring a snapshot should cost about $0.001 in USDC.

## How verification works

```
proofHash = sha256(canonical snapshot bytes)                 # the file itself is canonical
actionId  = sha256("pulse-arc-radar/v1/snapshot/" + snapshot_id)
```

Canonical JSON follows the same rules in Python and JavaScript:
- keys are sorted and there is no whitespace;
- the output is pure ASCII, with DEL and every non-ASCII code unit escaped as lowercase `\uXXXX`;
- values can only be safe integers, strings, booleans, null, arrays and objects.

Parity between the two implementations is enforced by tests. The same `(actionId, proofHash)` pair is what `PulseActionProofRegistry.recordProof` stores once mainnet anchoring is approved. To verify locally:

```
python scripts/verify_anchor.py            # latest snapshot; reports NOT_YET_ANCHORED until a registry exists
```

## Architecture

```
                 read-only JSON-RPC (paced, ≤9,999-block getLogs, backoff on -32005)
Arc mainnet RPC ─────────────────────────────────────────────┐
  (chain 5042)                                               ▼
                                          GitHub Actions (hourly, no secrets)
                                          python -m src.scanner.run
                                            ├─ ERC-8004 logs → agent book (state/state.json)
                                            ├─ bounded, SSRF-guarded metadata/endpoint checks
                                            ├─ USDC transfers + Multicall3 balances
                                            └─ signals → canonical snapshot + SHA-256
                                                          │ commit if changed
                                                          ▼
                                          snapshots/*.json ──► GitHub Pages (static)
                                                                  │
                          browser: SHA-256 re-verification ◄──────┤
                          browser: live head + anchor lookup ─────┘ (eth_call only)

Phase 2, after owner approval:  owner-signed recordProof(actionId, proofHash) ──► PulseActionProofRegistry on Arc
```

There is no server, database, Worker or paid API. The scanner uses only the Python standard library.

## Repository layout

```
src/scanner/      config, rpc (read-only allowlist), erc8004, metadata, safe_http, usdc_activity, signals, snapshot, run
contracts/        PulseActionProofRegistry.sol  (copied byte-for-byte from the testnet predecessor)
scripts/          verify_anchor.py, build_site.py, prepare_anchors.py, compile_contract.js, contract_facts.js (unsigned plans only)
public/           index.html, app.js, verify.js, style.css, agent/erc8004-registration.json
snapshots/        latest.json, s-<block>.json, directory.json, index.json, anchors.json
state/            state.json (scanner cursors + agent book)
evidence/         mainnet_deployment_plan.json, mainnet_tx_plan.json (both UNSIGNED), testnet_predecessor/*
docs/             ARCHITECTURE.md, SECURITY.md, SIGNALS.md, CONTRACT.md, SUBMISSION.md
tests/            unittest suites + contract tests (node --test)
```

## Run it

```
python -m unittest discover -s tests -p "test_*.py"     # offline tests (the contract tests need `npm install`)
python -m src.scanner.run                              # one bounded read-only scan
python scripts/build_site.py && python -m http.server -d _site 8000
```

## Security

In short: read-only RPC through a method allowlist; no keys, signing or wallet code anywhere in the repo (enforced by a test); untrusted metadata is rendered as text only, under a strict CSP, with no remote images; external links are http/https only with `rel="nofollow noopener noreferrer"`; HTTP fetches are SSRF-guarded (public IPs only, IP-pinned, default ports, re-validated redirects, size and time caps). Details are in [docs/SECURITY.md](docs/SECURITY.md).

## Signals are not judgements

Agent metadata is third-party content. HEURISTIC signals (clusters, density) describe patterns that are common for platforms and batch deployers. They are not claims about any project or person. Nothing here is investment advice, and the project is not affiliated with or endorsed by Arc or Circle.

## Mainnet plan

Two wallets are involved. The builder wallet provides attribution only. The Pulse wallet (by owner decision) owns the ERC-8004 identity, deploys the registry, is its recorder, and sends anchors, in strict nonce order. The exact unsigned transactions are in [docs/CONTRACT.md](docs/CONTRACT.md) and `evidence/mainnet_tx_plan.json`. Nothing is signed until the owner approves each transaction.

## License

[MIT](LICENSE) © 2026 Pulse Intelligence
