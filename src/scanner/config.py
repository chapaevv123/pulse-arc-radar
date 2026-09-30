"""Verified Arc mainnet constants and conservative scanner limits.

Every address below was checked against docs.arc.io and confirmed with
read-only RPC calls on 2026-09-30. Nothing here enables a write path.
"""

SCANNER_VERSION = "0.1.0"

CHAIN_ID = 5042
NETWORK = "arc_mainnet"
RPC_URL = "https://rpc.mainnet.arc.io"
EXPLORER_URL = "https://explorer.arc.io"

# ERC-8004 registries (Arc mainnet, docs.arc.io/arc/references/contract-addresses).
IDENTITY_REGISTRY = "0x8004a169fb4a3325136eb29fa0ceb6d2e539a432"
REPUTATION_REGISTRY = "0x8004baa17c55a88189ae136b182e5fda19de9b63"
VALIDATION_REGISTRY = "0x8004cc8439f36fd5f9f049d9ff86523df6daab58"
# First block with IdentityRegistry code (binary search over eth_getCode).
IDENTITY_REGISTRY_DEPLOY_BLOCK = 13_344_062

# USDC: native gas token (18 decimals) with an ERC-20 view (6 decimals).
USDC_ERC20 = "0x3600000000000000000000000000000000000000"
# EIP-7708 native-transfer log emitter used by Arc for native USDC moves.
NATIVE_TRANSFER_EMITTER = "0xfffffffffffffffffffffffffffffffffffffffe"
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"

# RPC discipline for the public endpoint.
MAX_LOG_RANGE = 9_999            # blocks per eth_getLogs, inclusive
HEAD_SAFETY_BLOCKS = 3           # primary RPC is load-balanced; stay behind the edge
MIN_CALL_INTERVAL_S = 1.0        # pacing between RPC calls
MAX_RETRIES = 5
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0
MAX_RPC_CALLS_PER_RUN = 250      # hard ceiling; the run degrades gracefully

# Scan windows (Arc produces ~2 blocks/second, ~7,200 blocks/hour).
BLOCKS_PER_HOUR = 7_200
INITIAL_LOOKBACK_BLOCKS = 172_800        # ~24h forward start on a fresh state
MAX_FORWARD_CHUNKS_PER_RUN = 24
MAX_BACKFILL_CHUNKS_PER_RUN = 60         # bounded walk back to the deploy block
NEW_AGENT_WINDOW_BLOCKS = 172_800        # NEW_AGENT = registered in the last ~24h
USDC_WINDOW_BLOCKS = 43_200              # rolling ~6h window for transfer activity
USDC_OWNER_BATCH = 100                   # addresses per topic filter / multicall

# Untrusted HTTP (metadata + endpoints).
HTTP_TIMEOUT_S = 6.0
METADATA_MAX_BYTES = 65_536
ENDPOINT_MAX_BYTES = 4_096
MAX_REDIRECTS = 2
MAX_METADATA_FETCHES_PER_RUN = 40
MAX_ENDPOINT_CHECKS_PER_RUN = 40
MAX_FETCHES_PER_HOST_PER_RUN = 10
ENDPOINT_RECHECK_S = 86_400
USER_AGENT = "PulseArcAgentRadar/0.1 (+https://github.com/chapaevv123/pulse-arc-radar)"

# Heuristic thresholds (documented in docs/SIGNALS.md; heuristics, not verdicts).
MASS_REGISTRATION_THRESHOLD = 20         # agents owned by one address
DENSITY_WINDOW_BLOCKS = 7_200            # ~1 hour
DENSITY_THRESHOLD = 10                   # same-owner registrations inside the window
IDENTICAL_METADATA_THRESHOLD = 3         # agents sharing a byte-identical agentURI

SNAPSHOT_RETENTION = 168                 # snapshot files kept (~7 days hourly)

# Wallet roles (PUBLIC addresses only; this repository never holds signer material).
BUILDER_WALLET = "0x6fa3659a15e9264e43ec67fe4bb5d31d38109a42"       # builder attribution only
PULSE_AGENT_WALLET = "0x765fb7e6a0bdddc29f57eece34aeda0fb318805d"   # owns Pulse's ERC-8004 identity
DEPLOYER_RECORDER_WALLET = None                                     # separate wallet; pending owner
SITE_URL = "https://chapaevv123.github.io/pulse-arc-radar/"
REPO_URL = "https://github.com/chapaevv123/pulse-arc-radar"
AGENT_METADATA_URL = SITE_URL + "agent/erc8004-registration.json"
