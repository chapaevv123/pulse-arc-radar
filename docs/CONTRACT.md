# PulseActionProofRegistry: Arc mainnet (DEPLOYED 2026-09-30)

**Deployed and verified** at `0x1607Af4E1DA1C06362871443d2e95C367B984Efa` (tx `0xb0b297f8…3143`, block 23,595,066). Pulse is ERC-8004 agent #1360, and three snapshots are anchored; receipts are in `evidence/`. The five-transaction owner grant is exhausted. The plan below is kept as the executed record.

`contracts/PulseActionProofRegistry.sol` is copied **byte-for-byte** from the testnet predecessor (`chapaevv123/pulse-arc`). Its source SHA-256 is `cdfb5d3e421c0b79c3f3f5943b9b222fb7a0032685ae4f53379446443307a957`. That exact build is deployed and RPC-verified on Arc Testnet at `0x1607Af4E1DA1C06362871443d2e95C367B984Efa`.

## Behaviour
- `constructor(address recorder)` sets an immutable `authorizedRecorder`.
- `recordProof(bytes32 actionId, bytes32 proofHash)` is write-once per actionId, callable only by the recorder, and rejects zero values. It emits `PulseActionProofRecorded`.
- `proofHashByAction(bytes32)` is a public view.
- The contract has no token, custody, receive/fallback, upgrade, admin mutation or external call.

## Wallet roles (public addresses only; no signer material is ever in this repo)
| Role | Address | Used for |
|---|---|---|
| Builder / owner | `0x6Fa3659a15e9264E43EC67Fe4bB5d31D38109A42` | Attribution only; existing Arc history. **Not** a deployer. |
| Pulse wallet | `0x765fb7e6a0BdDDc29f57eeCE34AEda0Fb318805d` | Owner decision (2026-09-30): ERC-8004 agent owner, **registry deployer, `authorizedRecorder`, and anchor sender**. Funded with 0.1 USDC for gas only. |

`scripts/contract_facts.js` refuses the builder wallet as deployer.

**Address continuity:** the Pulse wallet deployed the testnet twin at nonce 0. Deploying at nonce 0 on mainnet therefore yields the **same address, `0x1607Af4E1DA1C06362871443d2e95C367B984Efa`**, and the same runtime code (keccak `0x6398dce1…7153`, recorder embedded). This only holds if the deploy is the wallet's first mainnet transaction.

## Facts (`node scripts/contract_facts.js` → `evidence/mainnet_deployment_plan.json`)
| | |
|---|---|
| Compiler | solc `0.8.30+commit.73712a01`, optimizer on (200 runs), evmVersion `prague`, metadata `ipfs` |
| Creation bytecode keccak256 | `0xdeb263de46f571d32498cc1953f17af7577d89f1a046a9737d1cf988e99458e4` (identical to testnet) |
| Runtime bytecode keccak256 (template) | `0x84b75a244f03ca76526896ec5eeaf99b323f0c2ed77d45fbe4752a38e71548b2`. Onchain runtime embeds the recorder as an immutable. |
| Constructor arg | `0x000000000000000000000000765fb7e6a0bdddc29f57eece34aeda0fb318805d` |
| Init code keccak256 | `0x71f769adb3d108ced508df436c94cd27f9a5d7e3e2e705570d56d90ce441361c` (764 bytes) |
| Gas (Arc mainnet `eth_estimateGas`, 2026-09-30) | deploy **179,515**; Pulse `register` **203,545**; each `recordProof` **47,257** (from the testnet twin) |
| Cost at the 20 gwei base fee | deploy ≈ 0.0036 USDC, register ≈ 0.0041 USDC, anchor ≈ 0.00095 USDC |

## Transaction plan (`evidence/mainnet_tx_plan.json`; UNSIGNED)
All five transactions come from the Pulse wallet in **strict nonce order**:
1. **Nonce 0, deploy:** `to = null`, `data = init code`, value 0, which creates `0x1607Af4E…4Efa`.
2. **Nonce 1, register Pulse:** `register("https://chapaevv123.github.io/pulse-arc-radar/agent/erc8004-registration.json")` on `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`.
3. **Nonces 2–4, anchor:** `recordProof(actionId, proofHash)` on `0x1607Af4E…4Efa` for each prepared snapshot.

Run `python scripts/preflight.py --step N` (read-only) immediately before each step. It fails closed on any mismatch, including an unexpected nonce.

Every transaction runs this sequence:
- read-only pre-flight: chainId 5042, nonce latest == pending, balance, `eth_estimateGas`, and an `eth_call` simulation of the exact calldata;
- show the operator the exact transaction and fee;
- explicit approval for that one transaction;
- the owner signs locally;
- one send, no automatic retry;
- verify the receipt.

After deployment, confirm `eth_getCode` is non-empty and `authorizedRecorder()` equals the deployer, then record the address in `snapshots/anchors.json`.
