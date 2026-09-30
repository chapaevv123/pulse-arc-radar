# Arc Microgrants: final submission text

Prepared 2026-10-01. **Not submitted**; the owner submits manually.
Figures come from the public snapshot `s-23598751` (2026-09-30 20:54 UTC). The live dashboard updates hourly, so numbers there will be slightly higher.

---

## A. Project title
**Pulse Arc Agent Radar**

## B. One-line summary
An open, live intelligence radar for Arc's ERC-8004 agent economy, with snapshots anchored on Arc mainnet so anyone can verify what was published.

## C. Short description (form field: "what it does and what it uses Arc for")
Pulse Arc Agent Radar tracks every AI agent registered in Arc mainnet's ERC-8004 IdentityRegistry. For each agent it checks the registration file, whether the declared endpoint actually responds, what the agent claims about x402 payments, and whether its owner moves USDC on Arc. The results are published hourly as an explainable public dashboard. Arc is both the data source (ERC-8004 registries and USDC activity) and the trust layer: snapshot hashes are anchored in a small open-source registry contract on Arc mainnet, and Pulse itself is registered there as ERC-8004 agent #1360.

## D. Full project description
Arc mainnet already has more than 1,300 registered ERC-8004 agents. A registration is cheap and says little on its own: anyone can register an agent with any name, any description, and any claim about payment support. Builders, users and grant reviewers looking at Arc's agent economy have no neutral way to tell which agents are reachable, which are actually payable, and which are bulk registrations.

Pulse Arc Agent Radar provides that view. It scans the complete history of the ERC-8004 IdentityRegistry since its deployment, then follows new registrations every hour. For every agent it records:
- whether the registration file is valid and links back to its onchain agent ID;
- whether the declared service endpoint answers;
- whether the agent declares x402 payment support, and separately whether its endpoint actually answers `402 Payment Required`;
- whether the owner address has recent USDC activity on Arc;
- ERC-8004 reputation feedback and validation events.

Every signal comes with an explanation and links to the registration transaction, block and owner on explorer.arc.io. Pattern-based signals, such as one address registering hundreds of agents, are clearly labelled as heuristics, not verdicts.

**What the data shows (snapshot of 2026-09-30):**
- **1,361** agents registered; **1,119** have a valid ERC-8004 registration file.
- **1,231** agents (about 90%) come from 5 owner addresses that each registered 20 or more agents. The largest single address registered **1,000**. This is flagged as a pattern for review, not as wrongdoing.
- Only **102** agents declare an HTTP endpoint at all. **97** of those responded when checked.
- **The x402 gap:** 35 agents declare `x402Support: true`, but only **4** of them returned `402 Payment Required` to a simple request. Meanwhile **5** agents that do *not* declare x402 did return 402. A single unauthenticated request is not a full payment test, but it shows that self-declared payment support and observed behaviour often differ. That is exactly the kind of gap a radar should surface.
- **61** agents have received ERC-8004 reputation feedback; USDC activity was seen for 12 owner addresses in the latest ~6-hour window.

**Verifiable by design.** Each hourly snapshot is published as a JSON file with a SHA-256 fingerprint, and the dashboard recomputes that fingerprint in your browser. Selected snapshots are anchored on Arc mainnet in `PulseActionProofRegistry`, a small write-once contract that stores snapshot fingerprints and nothing else: no tokens, no custody, no admin or upgrade functions. The verifier reads the fingerprint back from Arc and shows `ANCHORED_MATCH` when the published file is exactly what was anchored. Once anchored, what Pulse reported at a given block cannot be silently rewritten.

## E. Why Arc
- **ERC-8004 is live on Arc.** Arc mainnet ships the ERC-8004 Identity, Reputation and Validation registries. The radar builds directly on them rather than on generic chain data.
- **USDC is Arc's native currency and gas.** Agents on Arc get paid in USDC, so the owner's USDC activity is a direct signal of real economic use. x402 payments settle in USDC, which makes the x402 declaration-versus-behaviour gap especially relevant on Arc.
- **Deterministic finality.** A block number on Arc is final, so "what Pulse reported at block N" is an unambiguous reference.
- **Cheap, predictable anchoring.** Each snapshot anchor cost about **$0.001 in USDC**. The entire mainnet footprint, including contract deployment, agent registration and three anchors, cost about **$0.011**.

## F. What was built
1. **Read-only scanner.** It reads Arc mainnet's ERC-8004 registries and USDC transfers through the public Arc RPC, with bounded, rate-limited requests. It runs hourly on GitHub Actions and holds no keys.
2. **Public dashboard** (GitHub Pages). It shows headline metrics, registrations over time, a searchable and filterable agent table, clusters, signal definitions, and evidence links to explorer.arc.io. It works on desktop and mobile.
3. **In-browser verifier.** It recomputes any snapshot's fingerprint and checks it against the onchain anchor.
4. **`PulseActionProofRegistry` on Arc mainnet.** A minimal write-once contract for snapshot fingerprints.
5. **Pulse as an ERC-8004 agent (#1360).** Its registration file points to the radar, which also lists itself.
6. **Safety.** Agent metadata is untrusted third-party content: it is shown as plain text only, no remote images are loaded, and outbound requests are restricted to public addresses. The code is covered by automated tests (54 Python tests, 5 contract tests).

## G. Mainnet proof (Arc mainnet, chain ID 5042)
| What | Address / transaction |
|---|---|
| PulseActionProofRegistry | `0x1607Af4E1DA1C06362871443d2e95C367B984Efa`: https://explorer.arc.io/address/0x1607Af4E1DA1C06362871443d2e95C367B984Efa |
| Contract deployment | https://explorer.arc.io/tx/0xb0b297f8aceaa80bf9da1b47130b10fae1a4a73ac6a3450ef7b39cb832093143 |
| Pulse ERC-8004 agent #1360 | https://explorer.arc.io/tx/0x95a5bd700361aca939b026ff6486ac55e9d1913bf2a1c93a65b36a1b8cce2844 (IdentityRegistry `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`) |
| Anchor: snapshot s-23556237 | https://explorer.arc.io/tx/0x5e8b2c09eb7b5e0d20b827c424739dc4ee534ca51e58be99d3cd29cd1443d609 |
| Anchor: snapshot s-23560821 | https://explorer.arc.io/tx/0x2a4907e4ddc2d21749671dd9baf4df4fd27a5cefffdbd3373bd81b973d6766f8 |
| Anchor: snapshot s-23564062 | https://explorer.arc.io/tx/0x1e9a2b94a8672b596ff7431b791022c06af6e3cea23f34a4838a944f64db9c99 |
| Pulse wallet (deployer, recorder, agent owner) | `0x765fb7e6a0BdDDc29f57eeCE34AEda0Fb318805d` |

All three anchored snapshots show `ANCHORED_MATCH` in the public verifier.

## H. Public links
- Live demo: https://chapaevv123.github.io/pulse-arc-radar/
- Source code: https://github.com/chapaevv123/pulse-arc-radar
- Pulse agent registration file: https://chapaevv123.github.io/pulse-arc-radar/agent/erc8004-registration.json
- Earlier Pulse work on Arc Testnet: https://github.com/chapaevv123/pulse-arc

## I. Open source
Fully open source under the **MIT License** (© 2026 Pulse Intelligence): the scanner, dashboard, verifier, contract and tests. Anyone can run the scanner, check a snapshot, or reuse the registry pattern.

## J. Builder
- X: https://x.com/MagnatSV
- GitHub: https://github.com/chapaevv123
- Builder wallet on Arc: `0x6Fa3659a15e9264E43EC67Fe4bB5d31D38109A42`
- **Continuity:** Pulse was first built on Arc Testnet. The same proof-registry contract was deployed there, and Pulse was registered as testnet ERC-8004 agent #894567. The mainnet registry was deployed from the same Pulse wallet and has the same address and identical code as its testnet version.
- No previous Arc or Circle funding.

## K. Likely form fields
| Field | Value |
|---|---|
| Project name | Pulse Arc Agent Radar |
| Tagline | Open intelligence radar for Arc's ERC-8004 agent economy, verifiable on Arc mainnet |
| Live deployment link (Arc mainnet) | https://chapaevv123.github.io/pulse-arc-radar/ (onchain: https://explorer.arc.io/address/0x1607Af4E1DA1C06362871443d2e95C367B984Efa) |
| Public repository | https://github.com/chapaevv123/pulse-arc-radar |
| What it does / what it uses Arc for | Section C |
| Builder profile | https://x.com/MagnatSV (GitHub: chapaevv123) |
| Category / tags | AI agents, ERC-8004, data & analytics, infrastructure, x402, USDC |
| Team | Solo builder |
| Stage | Live on Arc mainnet (working prototype / MVP) |
| Prior funding | None |
| Payout (USDC on Arc) | Owner to decide; a candidate is the builder wallet `0x6Fa3659a15e9264E43EC67Fe4bB5d31D38109A42` |

## Next steps (if funded)
- Turn ERC-8004 reputation and validation data into per-agent trust scores.
- Run full x402 payment-path checks instead of a single request.
- Alerts for newly registered, reachable, payable agents.
- A USDC-paid (x402) signal API on Arc.
- Anchor snapshots on a regular schedule.

---

## L. Final concise version (ready to paste)

**Pulse Arc Agent Radar** is an open intelligence radar for Arc's ERC-8004 agent economy, live on Arc mainnet.

It tracks every agent in Arc's ERC-8004 IdentityRegistry, 1,361 so far, and checks whether each one is real, reachable and payable. It validates each agent's registration file, tests whether its declared endpoint responds, compares what the agent *claims* about x402 payments with how its endpoint *actually* behaves, and looks at USDC activity by the agent's owner on Arc. Everything is published hourly as an explainable public dashboard, with evidence links to explorer.arc.io; pattern-based signals are labelled as heuristics, not verdicts.

Early findings:
- About 90% of registered agents come from five bulk-registering addresses.
- Only 102 agents declare an endpoint at all.
- Of 35 agents that declare x402 support, only 4 returned `402 Payment Required` when probed, while 5 agents that don't declare it did.

**How it uses Arc:**
- Arc's ERC-8004 registries and USDC activity are the data source.
- Snapshot fingerprints are anchored in `PulseActionProofRegistry` on Arc mainnet (`0x1607Af4E1DA1C06362871443d2e95C367B984Efa`), so anyone can verify in the browser that a published report matches what was anchored.
- Pulse itself is registered as ERC-8004 agent #1360.
- All five mainnet transactions together cost about $0.011 in USDC.

Open source (MIT). It continues earlier Pulse work on Arc Testnet (agent #894567).

- Demo: https://chapaevv123.github.io/pulse-arc-radar/
- Repo: https://github.com/chapaevv123/pulse-arc-radar
- Builder: https://x.com/MagnatSV

---

## M. Attachments worth including
Screenshots of the live site:
- [ ] Desktop overview: header, metric tiles (1,361 agents, 35 x402, 97 live endpoints), coverage bar.
- [ ] "Pulse on Arc" section: REGISTERED · VERIFIED · agent #1360; DEPLOYED · VERIFIED; 3 ANCHORED ON ARC.
- [ ] The verifier with an anchored snapshot selected (e.g. `s-23564062`), showing VERIFIED and `ANCHORED_MATCH`.
- [ ] Agent table, "Standout" filter, with one row expanded to show its evidence links.
- [ ] Mobile view (about 390 px wide).

Proof links (any of section G):
- [ ] Contract page on explorer.arc.io
- [ ] Agent #1360 registration transaction
- [ ] One anchor transaction

Repo links:
- [ ] `evidence/` folder (transaction receipts)
- [ ] `docs/SIGNALS.md` (signal definitions and heuristic thresholds)
