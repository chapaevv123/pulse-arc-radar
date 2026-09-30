# Arc Microgrants: submission draft (NOT SUBMITTED)

> Submit only after Phase 2: the program requires a working deployment on **Arc mainnet**.
> The fields below read correctly once the registry contract is deployed, at least one snapshot is anchored, and Pulse is registered as an agent on mainnet. Until then, those parts are marked PENDING.

**Name:** Pulse Arc Agent Radar

**One-liner:** An open intelligence radar for the Arc agent economy, with every snapshot anchored and verifiable on Arc mainnet.

**Description:**
Pulse Arc Agent Radar watches Arc mainnet's ERC-8004 agent registries and USDC activity. It covers the complete history since the IdentityRegistry was deployed, which was 1,354 agents at the time of writing. For each agent it checks whether the registration file meets the ERC-8004 spec, probes the declared endpoint (preferring x402 payment endpoints), reads x402 declarations, reputation and validation events, and the owner's USDC activity. It also flags mass-registration clusters as clearly labelled heuristics. Every signal is explainable and links to the registration transaction, block and owner on explorer.arc.io. Each hourly snapshot is canonical JSON with a SHA-256 hash that anyone can re-verify in the browser, and snapshots are anchored to a minimal, non-custodial registry contract on Arc mainnet.

**What Arc is used for:**
- **Data:** Arc mainnet's ERC-8004 IdentityRegistry, ReputationRegistry and ValidationRegistry, plus USDC Transfer logs (the ERC-20 view and EIP-7708 native logs), read over Arc's public RPC.
- **Onchain component:** `PulseActionProofRegistry` on Arc mainnet stores `actionId → proofHash` for published snapshots. Gas is paid in USDC.
- **Identity:** Pulse is registered as an ERC-8004 agent on Arc mainnet, and its registration file points to the radar.

**Why it matters:**
Arc's agent economy is growing fast. Of the first 1,354 registered agents, 1,231 sit in mass-registration clusters, and only 96 expose an endpoint that answers. Builders, users and reviewers need a neutral, verifiable view of which agents are real, reachable and payable. Anchored snapshots make that view tamper-evident: what Pulse reported at a given block cannot be rewritten later.

**Why continue beyond the microgrant:**
- Agent reputation scoring from ERC-8004 feedback and validation.
- x402 payment-path verification.
- Alerts for new agents.
- An x402-paid signal API settled in USDC on Arc.
- Integrating Pulse's wider opportunity engine for Arc builders.

**Architecture summary:**
The Arc mainnet RPC feeds a GitHub Actions scanner (Python standard library, paced read-only calls, bounded and resumable). It writes canonical snapshots that are served as a static GitHub Pages dashboard and verified in the browser. Owner-approved anchoring writes to the Arc mainnet registry. There is no server, database or paid API.

**Demo:** https://chapaevv123.github.io/pulse-arc-radar/
**GitHub:** https://github.com/chapaevv123/pulse-arc-radar
**Builder profile:** https://x.com/MagnatSV (builder wallet on Arc: `0x6Fa3659a15e9264E43EC67Fe4bB5d31D38109A42`)
**Pulse agent wallet:** `0x765fb7e6a0BdDDc29f57eeCE34AEda0Fb318805d` (continuity with the testnet predecessor, agent #894567)
**Mainnet proof (Phase 2, PENDING):**
- registry contract address: *(pending)*
- deployment transaction: *(pending)*
- first anchor transaction: *(pending)*
- Pulse agentId on mainnet: *(pending)*
