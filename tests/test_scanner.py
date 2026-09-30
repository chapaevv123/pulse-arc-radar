import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from src.scanner import config, safe_http, signals as sig, usdc_activity
from src.scanner.erc8004 import AgentBook
from src.scanner.metadata import summarize_uri, validate_registration
from src.scanner.rpc import ArcRpc, BudgetExhausted, ChainMismatch, RpcError, block_chunks, block_chunks_desc
from src.scanner.run import check_endpoints, fetch_metadata, run
from src.scanner.safe_http import FetchResult
from src.scanner.snapshot import action_id, canonical_bytes, snapshot_hash
from tests import fakes

OWNER_A = "0x" + "a" * 40
OWNER_B = "0x" + "b" * 40
HEAD = 13_400_000


def quiet(*_a, **_k):
    pass


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


# 1 ---------------------------------------------------------------------------
class CanonicalHash(unittest.TestCase):
    def test_key_order_does_not_change_hash(self):
        a = {"b": 1, "a": {"y": [1, "x", None, True], "x": "é🚀"}}
        b = {"a": {"x": "é🚀", "y": [1, "x", None, True]}, "b": 1}
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))
        self.assertEqual(snapshot_hash(a), snapshot_hash(b))
        self.assertTrue(canonical_bytes(a).isascii())

    def test_repeatable_and_rejects_ambiguous_types(self):
        obj = {"n": 2**53 - 1, "s": "<script>alert(1)</script>"}
        self.assertEqual(snapshot_hash(obj), snapshot_hash(json.loads(canonical_bytes(obj))))
        for bad in ({"f": 1.5}, {"n": 2**53}, {1: "x"}, {"k": b"bytes"}, {"ké": 1}):
            with self.assertRaises(ValueError):
                canonical_bytes(bad)

    def test_action_id_domain_separated(self):
        self.assertNotEqual(action_id("s-1"), action_id("s-2"))
        self.assertRegex(action_id("s-1"), r"^0x[0-9a-f]{64}$")


# 2 ---------------------------------------------------------------------------
class Chunking(unittest.TestCase):
    def test_chunks_cover_exactly_and_never_exceed_limit(self):
        rng = random.Random(7)
        for _ in range(300):
            a = rng.randint(0, 10**7)
            b = a + rng.randint(0, 60_000)
            asc = list(block_chunks(a, b))
            desc = list(block_chunks_desc(b, a))
            for chunks in (asc, desc):
                self.assertTrue(all(y - x + 1 <= 9_999 for x, y in chunks))
                covered = sorted(chunks)
                self.assertEqual(covered[0][0], a)
                self.assertEqual(covered[-1][1], b)
                self.assertTrue(all(covered[i][1] + 1 == covered[i + 1][0] for i in range(len(covered) - 1)))

    def test_rpc_refuses_oversized_range_and_chunk_size(self):
        rpc = fakes.fast_rpc(fakes.FakeChain(HEAD))
        with self.assertRaises(ValueError):
            rpc.get_logs(0, 9_999, config.IDENTITY_REGISTRY)
        rpc.get_logs(0, 9_998, config.IDENTITY_REGISTRY)
        with self.assertRaises(ValueError):
            list(block_chunks(0, 10, size=10_000))


# 3 + 4 -----------------------------------------------------------------------
class CursorAndDuplicates(TempRoot):
    def chain(self):
        logs = [fakes.registered(i, OWNER_A, fakes.data_uri(fakes.registration_doc(f"A{i}", agent_id=i)),
                                 HEAD - 100_000 + i * 997) for i in range(1, 60)]
        logs += [fakes.feedback(58, HEAD - 50, 3)]
        return fakes.FakeChain(HEAD, logs)

    def test_resume_scans_disjoint_ranges(self):
        chain = self.chain()
        run(root=self.root, rpc=fakes.fast_rpc(chain), fetcher=fakes.FakeFetcher(), now=1_790_000_000,
            initial_lookback=30_000, max_backfill_chunks=2, log=quiet)
        chain.head += 12_000
        run(root=self.root, rpc=fakes.fast_rpc(chain), fetcher=fakes.FakeFetcher(), now=1_790_003_600,
            initial_lookback=30_000, max_backfill_chunks=2, log=quiet)
        ranges = [(int(r["params"][0]["fromBlock"], 16), int(r["params"][0]["toBlock"], 16))
                  for r in chain.requests if r["method"] == "eth_getLogs"
                  and r["params"][0]["address"] == [config.IDENTITY_REGISTRY, config.REPUTATION_REGISTRY,
                                                    config.VALIDATION_REGISTRY]]
        spans = sorted(ranges)
        for i in range(len(spans) - 1):
            self.assertLess(spans[i][1], spans[i + 1][0], "registry ranges overlap")
        state = json.loads((self.root / "state" / "state.json").read_text())
        self.assertEqual(state["forward_cursor"], chain.head - config.HEAD_SAFETY_BLOCKS)
        self.assertEqual(state["runs"], 2)

    def test_repeat_delivery_does_not_double_count(self):
        chain = self.chain()
        book = AgentBook()
        book.apply_logs(chain.logs)
        before = json.dumps(book.agents, sort_keys=True)
        book.apply_logs(chain.logs)
        self.assertEqual(before, json.dumps(book.agents, sort_keys=True))
        self.assertEqual(book.agents["58"]["feedback_count"], 1)
        self.assertEqual(len(book.agents), 59)

    def test_out_of_order_newer_uri_wins(self):
        book = AgentBook()
        newer = fakes.uri_updated(7, fakes.data_uri(fakes.registration_doc("New", agent_id=7)), 200)
        older = fakes.registered(7, OWNER_A, fakes.data_uri(fakes.registration_doc("Old", agent_id=7)), 100)
        book.apply_logs([newer])          # forward scan saw the update first
        book.apply_logs([older])          # backfill delivers the registration later
        self.assertEqual(book.agents["7"]["meta"]["name"], "New")
        self.assertEqual(book.agents["7"]["registered_block"], 100)

    def test_full_run_twice_same_head_is_stable(self):
        chain = self.chain()
        r1 = run(root=self.root, rpc=fakes.fast_rpc(chain), fetcher=fakes.FakeFetcher(), now=1_790_000_000,
                 initial_lookback=200_000, log=quiet)
        r2 = run(root=self.root, rpc=fakes.fast_rpc(chain), fetcher=fakes.FakeFetcher(), now=1_790_000_000,
                 initial_lookback=200_000, log=quiet)
        self.assertEqual(r1["stats"]["agents_observed"], r2["stats"]["agents_observed"])
        self.assertEqual(r2["stats"]["discovered_this_run"], 0)
        self.assertEqual(r2["stats"]["reputation_agents"], 1)

    def test_wrong_chain_fails_closed(self):
        with self.assertRaises(ChainMismatch):
            run(root=self.root, rpc=fakes.fast_rpc(fakes.FakeChain(HEAD, chain_id=5042002)),
                fetcher=fakes.FakeFetcher(), log=quiet)
        self.assertFalse((self.root / "snapshots" / "latest.json").exists())


# 5 ---------------------------------------------------------------------------
class RateLimitBackoff(unittest.TestCase):
    def limited(self):
        return 200, {}, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "rate limit exceeded"}}

    def test_backoff_then_success(self):
        chain = fakes.FakeChain(HEAD, script=[self.limited(), self.limited()])
        rpc = fakes.fast_rpc(chain)
        self.assertEqual(rpc.block_number(), HEAD)
        self.assertEqual(rpc.rate_limit_events, 2)
        self.assertEqual(rpc.sleeps, [2.0, 4.0])            # exponential, jitter pinned to 0

    def test_retry_after_and_cap(self):
        chain = fakes.FakeChain(HEAD, script=[(429, {"Retry-After": "30"}, None)])
        rpc = fakes.fast_rpc(chain)
        rpc.block_number()
        self.assertEqual(rpc.sleeps, [30.0])

    def test_bounded_retries_and_budget(self):
        chain = fakes.FakeChain(HEAD, script=[self.limited()] * 20)
        rpc = fakes.fast_rpc(chain, max_retries=3)
        with self.assertRaises(RpcError):
            rpc.block_number()
        self.assertEqual(len(chain.requests), 4)
        self.assertTrue(all(s <= config.BACKOFF_CAP_S * 1.25 for s in rpc.sleeps))
        budget = fakes.fast_rpc(fakes.FakeChain(HEAD), max_calls=2)
        budget.block_number(), budget.block_number()
        with self.assertRaises(BudgetExhausted):
            budget.block_number()

    def test_pacing_between_calls(self):
        sleeps, clock = [], [0.0]
        rpc = ArcRpc(transport=fakes.FakeChain(HEAD), min_interval=1.0, sleep=sleeps.append, clock=lambda: clock[0])
        rpc.block_number()
        rpc.block_number()
        self.assertEqual(sleeps, [1.0])


# 6 ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status, body=b"", headers=None):
        self.status, self._body, self._headers = status, body, headers or {}

    def getheader(self, k):
        return self._headers.get(k)

    def read(self, n):
        return self._body[:n]

    def close(self):
        pass


class _FakeConn:
    def __init__(self, resp):
        self.resp, self.requested = resp, None

    def request(self, method, path, headers):
        self.requested = (method, path, headers)

    def getresponse(self):
        return self.resp

    def close(self):
        pass


def resolver_for(mapping):
    def resolve(host, port, type=None):
        return [(2, 1, 6, "", (mapping[host], port))]
    return resolve


class BoundedFetch(unittest.TestCase):
    def test_url_rules(self):
        for url, code in [("ftp://x.org/a", "SCHEME_NOT_HTTP"), ("javascript:alert(1)", "SCHEME_NOT_HTTP"),
                          ("https://u:p@x.org/", "USERINFO_NOT_ALLOWED"), ("https://x.org:8443/", "NON_DEFAULT_PORT"),
                          ("https://x.org/é", "URL_NOT_ASCII"), ("https://x .org/", "URL_CONTROL_OR_SPACE")]:
            with self.assertRaises(safe_http.BlockedURL) as cm:
                safe_http.validate_url(url)
            self.assertEqual(str(cm.exception), code)

    def test_private_and_metadata_addresses_blocked(self):
        for ip in ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "fc00::1", "0.0.0.0",
                   "::ffff:127.0.0.1", "100.64.0.1"]:
            res = safe_http.fetch("https://evil.example/", max_bytes=100, resolver=resolver_for({"evil.example": ip}),
                                  connection_factory=lambda *a: self.fail("must not connect"))
            self.assertEqual(res.outcome, "BLOCKED", ip)

    def test_size_cap_and_pinned_ip(self):
        seen = {}

        def factory(scheme, host, ip, port, timeout):
            seen.update(host=host, ip=ip, port=port)
            return _FakeConn(_FakeResp(200, b"x" * 500))
        res = safe_http.fetch("https://ok.example/m.json", max_bytes=100,
                              resolver=resolver_for({"ok.example": "93.184.216.34"}), connection_factory=factory)
        self.assertEqual(res.outcome, "TOO_LARGE")
        self.assertEqual(seen, {"host": "ok.example", "ip": "93.184.216.34", "port": 443})

    def test_redirect_to_private_is_blocked_and_redirects_capped(self):
        conns = iter([_FakeConn(_FakeResp(302, headers={"Location": "http://inner.example/"}))])
        res = safe_http.fetch("https://ok.example/", max_bytes=100,
                              resolver=resolver_for({"ok.example": "93.184.216.34", "inner.example": "10.1.1.1"}),
                              connection_factory=lambda *a: next(conns))
        self.assertEqual(res.outcome, "BLOCKED")
        loop = lambda *a: _FakeConn(_FakeResp(301, headers={"Location": "/again"}))
        res = safe_http.fetch("https://ok.example/", max_bytes=100, max_redirects=2,
                              resolver=resolver_for({"ok.example": "93.184.216.34"}), connection_factory=loop)
        self.assertEqual((res.outcome, res.error), ("BLOCKED", "TOO_MANY_REDIRECTS"))

    def test_run_level_caps_per_run_and_per_host(self):
        book = AgentBook()
        for i in range(100):
            host = f"h{i % 5}.example"
            book.apply_logs([fakes.registered(i, OWNER_A, f"https://{host}/agent/{i}.json", 1000 + i)])
        report = {"metadata_fetches": 0, "endpoint_checks": 0}
        fetcher = fakes.FakeFetcher()
        fetch_metadata(book, 1_790_000_000, fetcher, report)
        self.assertLessEqual(len(fetcher.calls), config.MAX_METADATA_FETCHES_PER_RUN)
        hosts = [u.split("/")[2] for u in fetcher.calls]
        self.assertTrue(all(hosts.count(h) <= config.MAX_FETCHES_PER_HOST_PER_RUN for h in set(hosts)))
        expected = min(config.MAX_METADATA_FETCHES_PER_RUN, 5 * config.MAX_FETCHES_PER_HOST_PER_RUN)
        self.assertEqual(len(fetcher.calls), expected)
        failed = [a for a in book.agents.values() if a["meta"]["status"] == "FETCH_FAILED"]
        self.assertEqual(len(failed), expected)


# 10 + 11 ---------------------------------------------------------------------
class SignalsAndClusters(unittest.TestCase):
    def build(self, n_a=25, n_b=2):
        logs = [fakes.registered(i, OWNER_A, fakes.data_uri(fakes.registration_doc(f"A{i}", agent_id=i)), 1000 + i * 10)
                for i in range(n_a)]
        logs += [fakes.registered(100 + i, OWNER_B,
                                  fakes.data_uri(fakes.registration_doc("B", x402=True, agent_id=100 + i,
                                                                        services=[{"name": "web", "endpoint": "https://b.example/"}])),
                                  900_000 + i * 50_000) for i in range(n_b)]
        book = AgentBook()
        book.apply_logs(logs)
        return book

    def test_mass_cluster_and_density(self):
        book = self.build()
        clusters, flags = sig.compute_clusters(book.agents)
        self.assertEqual(clusters[0]["kind"], "MASS_REGISTRATION_CLUSTER")
        self.assertEqual(clusters[0]["agents"], 25)
        self.assertIn("MASS_REGISTRATION_CLUSTER", flags[0])
        self.assertNotIn("HIGH_REGISTRATION_DENSITY", flags[8])      # 9th registration in window
        self.assertIn("HIGH_REGISTRATION_DENSITY", flags[9])         # 10th
        self.assertNotIn(100, flags)

    def test_identical_uri_cluster(self):
        uri = fakes.data_uri(fakes.registration_doc("Same"))
        book = AgentBook()
        book.apply_logs([fakes.registered(i, "0x" + f"{i:040x}", uri, 10 + i) for i in range(3)])
        _, flags = sig.compute_clusters(book.agents)
        self.assertEqual({i: flags[i] for i in range(3)}, {i: {"IDENTICAL_METADATA_CLUSTER"} for i in range(3)})

    def test_signals_deterministic_and_order_independent(self):
        book = self.build()
        shuffled = dict(sorted(book.agents.items(), key=lambda kv: random.Random(3).random()))
        c1, f1 = sig.compute_clusters(book.agents)
        c2, f2 = sig.compute_clusters(shuffled)
        self.assertEqual(c1, c2)
        a = book.agents["101"]
        a["endpoint"].update(status="LIVE", http_status=402)
        s1 = sig.agent_signals(a, snapshot_block=950_100, cluster_flags=f1.get(101, set()), usdc={"transfers": 2})
        s2 = sig.agent_signals(a, snapshot_block=950_100, cluster_flags=f2.get(101, set()), usdc={"transfers": 2})
        self.assertEqual(s1, s2)
        self.assertEqual(s1[:3], ["ENDPOINT_LIVE", "HTTP_402_OBSERVED", "X402_DECLARED"])
        self.assertEqual(sig.classify(s1), "STANDOUT")
        self.assertIn("NEW_AGENT", s1)

    def test_no_accusatory_language(self):
        text = json.dumps([sig.SIGNAL_DEFS, sig.CLASS_DEFS]).lower()
        for word in ("scam", "fraud", "malicious", "rug", "fake"):
            self.assertNotIn(word, text)

    def test_endpoint_classification(self):
        self.assertEqual(sig.classify_endpoint("OK", 200), "LIVE")
        self.assertEqual(sig.classify_endpoint("HTTP_ERROR", 402), "LIVE")
        self.assertEqual(sig.classify_endpoint("HTTP_ERROR", 404), "HTTP_ERROR")
        self.assertEqual(sig.classify_endpoint("HTTP_ERROR", 400), "LIVE")      # x402 GET without params
        self.assertEqual(sig.classify_endpoint("HTTP_ERROR", 503), "HTTP_ERROR")
        self.assertEqual(sig.classify_endpoint("UNREACHABLE", None), "UNREACHABLE")
        self.assertEqual(sig.classify_endpoint("BLOCKED", None), "BLOCKED")


# 12 --------------------------------------------------------------------------
class X402AndMetadata(unittest.TestCase):
    def v(self, doc, aid=1):
        return validate_registration(json.dumps(doc).encode(), aid, "DATA_URI")

    def test_x402_flag_parsing(self):
        self.assertIs(self.v(fakes.registration_doc(x402=True, agent_id=1))["x402"], True)
        self.assertIs(self.v(fakes.registration_doc(x402=False, agent_id=1))["x402"], False)
        self.assertIsNone(self.v(fakes.registration_doc(agent_id=1))["x402"])
        bad = self.v(fakes.registration_doc(x402="true", agent_id=1))
        self.assertIsNone(bad["x402"])
        self.assertIn("X402_FIELD_MALFORMED", bad["issues"])

    def test_registration_checks(self):
        ok = self.v(fakes.registration_doc(agent_id=1))
        self.assertEqual((ok["status"], ok["backlink"]), ("VALID", "MATCH"))
        self.assertEqual(self.v(fakes.registration_doc())["backlink"], "MATCH_NO_AGENT_ID")
        self.assertEqual(self.v(fakes.registration_doc(agent_id=2))["status"], "INVALID")
        self.assertEqual(self.v({"type": "x", "name": "n"})["status"], "INVALID")
        self.assertEqual(validate_registration(b"<html>", 1, "HTTP")["issues"], ["NOT_JSON"])
        self.assertEqual(summarize_uri("", 1)[1]["issues"], ["URI_EMPTY"])
        self.assertEqual(summarize_uri("ipfs://Qm", 1)[1]["status"], "UNVERIFIED")
        self.assertEqual(summarize_uri("https://x.org/a.json", 1)[1]["status"], "PENDING_FETCH")

    def test_bare_eip_type_is_warning_not_invalid(self):
        doc = fakes.registration_doc(agent_id=1)
        doc["type"] = "https://eips.ethereum.org/EIPS/eip-8004"          # real-world near-miss seen on mainnet
        res = self.v(doc)
        self.assertEqual(res["status"], "VALID")
        self.assertIn("TYPE_NONCANONICAL", res["issues"])
        doc["type"] = "https://example.org/agent-card"
        self.assertIn("TYPE_MISMATCH", self.v(doc)["issues"])
        self.assertEqual(self.v(doc)["status"], "INVALID")

    def test_x402_service_preferred_for_probe(self):
        from src.scanner.metadata import primary_endpoint
        res = self.v(fakes.registration_doc(agent_id=1, services=[
            {"name": "web", "endpoint": "https://a.example/"},
            {"name": "x402:wallet", "endpoint": "https://a.example/pay"}]))
        self.assertEqual(primary_endpoint(res), "https://a.example/pay")
        res = self.v(fakes.registration_doc(agent_id=1, services=[
            {"name": "ens", "endpoint": "agent.eth"}, {"name": "web", "endpoint": "https://a.example/"}]))
        self.assertEqual(primary_endpoint(res), "https://a.example/")

    def test_stale_summaries_revalidated_without_network(self):
        book = AgentBook()
        doc = fakes.registration_doc(agent_id=3)
        doc["type"] = "https://eips.ethereum.org/EIPS/eip-8004"
        book.apply_logs([fakes.registered(3, OWNER_A, fakes.data_uri(doc), 10),
                         fakes.registered(4, OWNER_A, "https://x.example/4.json", 11)])
        for a in book.agents.values():                       # simulate summaries from validator v1
            a["meta"] = dict(a["meta"], status="INVALID", issues=["TYPE_MISMATCH"], validator=1)
        self.assertEqual(book.revalidate_stale(), 2)
        self.assertEqual(book.agents["3"]["meta"]["status"], "VALID")
        self.assertEqual(book.agents["4"]["meta"]["status"], "PENDING_FETCH")
        self.assertEqual(book.revalidate_stale(), 0)

    def test_sanitizes_control_and_bidi(self):
        doc = fakes.registration_doc("Evil‮\u0000Name\n<b>x</b>", agent_id=1)
        self.assertEqual(self.v(doc)["name"], "EvilName <b>x</b>")


# 13 --------------------------------------------------------------------------
class UsdcActivity(unittest.TestCase):
    def test_parser_dedupes_and_splits_direction(self):
        tx = "0x" + "c" * 64
        logs = [fakes.usdc_transfer(OWNER_A, OWNER_B, 10, 0, tx=tx),
                fakes.usdc_transfer(OWNER_A, OWNER_B, 10, 1, emitter=config.NATIVE_TRANSFER_EMITTER, tx=tx),
                fakes.usdc_transfer("0x" + "9" * 40, OWNER_A, 12, 0),
                fakes.usdc_transfer("0x" + "8" * 40, "0x" + "7" * 40, 13, 0),
                fakes.usdc_transfer(OWNER_A, OWNER_B, 14, 0, emitter="0x" + "5" * 40)]
        out = usdc_activity.parse_transfer_logs(logs, {OWNER_A, OWNER_B})
        self.assertEqual(out[OWNER_A], {"transfers": 2, "in": 1, "out": 1, "last_block": 12,
                                        "last_tx": logs[2]["transactionHash"]})
        self.assertEqual(out[OWNER_B]["transfers"], 1)

    def test_multicall_roundtrip_and_format(self):
        chain = fakes.FakeChain(HEAD, balances={OWNER_A: 4_651_754_000_000_000_000, OWNER_B: 0})
        bal = usdc_activity.fetch_balances(fakes.fast_rpc(chain), [OWNER_A, OWNER_B], HEAD)
        self.assertEqual(usdc_activity.format_usdc(bal[OWNER_A]), "4.651754")
        self.assertEqual(usdc_activity.format_usdc(bal[OWNER_B]), "0.000000")

    def test_window_scan_filters_by_owner(self):
        logs = [fakes.usdc_transfer(OWNER_A, OWNER_B, HEAD - 10), fakes.usdc_transfer(OWNER_A, OWNER_B, HEAD - 60_000)]
        out, window = usdc_activity.scan_transfers(fakes.fast_rpc(fakes.FakeChain(HEAD, logs)), [OWNER_A], HEAD)
        self.assertEqual(out[OWNER_A]["transfers"], 1)
        self.assertEqual(window, {"from_block": HEAD - config.USDC_WINDOW_BLOCKS + 1, "to_block": HEAD})


# 14 + 15 ---------------------------------------------------------------------
class SnapshotOutput(TempRoot):
    def run_once(self, head=HEAD, now=1_790_000_000):
        logs = [fakes.registered(1, OWNER_A, fakes.data_uri(fakes.registration_doc(
            "<img src=x onerror=alert(1)>", agent_id=1)), head - 500),
                fakes.registered(2, OWNER_B, "https://agent.example/reg.json", head - 400),
                fakes.usdc_transfer(OWNER_A, OWNER_B, head - 100)]
        chain = fakes.FakeChain(head, logs, balances={OWNER_A: 10**18})
        body = json.dumps(fakes.registration_doc("Remote", x402=True, agent_id=2, services=[
            {"name": "A2A", "endpoint": "https://agent.example/a2a"}])).encode()
        fetcher = fakes.FakeFetcher({"https://agent.example/reg.json": FetchResult("OK", 200, body),
                                     "https://agent.example/a2a": FetchResult("HTTP_ERROR", 402)})
        return run(root=self.root, rpc=fakes.fast_rpc(chain), fetcher=fetcher, now=now, log=quiet)

    def test_schema(self):
        self.run_once()
        snap = json.loads((self.root / "snapshots" / "latest.json").read_bytes())
        for key in ("schema", "snapshot_id", "action_id", "created_at", "chain_id", "block_range", "stats",
                    "clusters", "new_agents", "data_sources", "scanner_version", "coverage", "directory", "notice"):
            self.assertIn(key, snap)
        self.assertEqual(snap["chain_id"], 5042)
        self.assertEqual(snap["schema"], "pulse_arc_radar_snapshot_v1")
        st = snap["stats"]
        self.assertEqual((st["agents_observed"], st["x402_declared"], st["endpoint_live"], st["usdc_active_agents"],
                          st["http_402_observed"]), (2, 1, 1, 2, 1))
        row = next(r for r in snap["new_agents"] if r["id"] == 1)
        self.assertEqual(row["name"], "<img src=x onerror=alert(1)>")   # stored as data; rendered as text
        self.assertEqual(row["usdc"]["balance"], "1.000000")
        self.assertEqual(snap["action_id"], action_id(snap["snapshot_id"]))

    def test_latest_and_index(self):
        self.run_once()
        second = self.run_once(head=HEAD + 5_000, now=1_790_003_600)
        out = self.root / "snapshots"
        sid = second["snapshot"]["id"]
        self.assertEqual((out / "latest.json").read_bytes(), (out / f"{sid}.json").read_bytes())
        index = json.loads((out / "index.json").read_bytes())
        self.assertEqual(index["latest"], sid)
        self.assertEqual([e["id"] for e in index["snapshots"]], [sid, f"s-{HEAD - 3}"])
        self.assertEqual(index["snapshots"][0]["sha256"], snapshot_hash(json.loads((out / "latest.json").read_bytes())))
        snap = json.loads((out / "latest.json").read_bytes())
        self.assertEqual(snap["directory"]["sha256"], snapshot_hash(json.loads((out / "directory.json").read_bytes())))
        anchors = json.loads((out / "anchors.json").read_bytes())
        self.assertIsNone(anchors["registry"])
        self.assertEqual(anchors["registry_status"], "PENDING_MAINNET_DEPLOYMENT")

    def test_retention_keeps_index_entries(self):
        from src.scanner import snapshot as snap_mod
        for i in range(4):
            snap = {"snapshot_id": f"s-{i}", "action_id": action_id(f"s-{i}"), "created_at": "t",
                    "block_range": {"from": i, "to": i}, "stats": {"agents_observed": 0}}
            snap_mod.publish(self.root, snap, {"agents": []}, retention=2)
        index = json.loads((self.root / "index.json").read_bytes())
        self.assertEqual(len(index["snapshots"]), 4)
        self.assertEqual([e["file"] is None for e in index["snapshots"]], [False, False, True, True])
        self.assertFalse((self.root / "s-0.json").exists())


if __name__ == "__main__":
    unittest.main()
