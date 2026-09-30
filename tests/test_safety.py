"""Dashboard/verifier safety and Python<->browser parity (tests 7, 8, 9, 16, 17, 18)."""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.scanner.rpc import ArcRpc
from src.scanner.snapshot import action_id, canonical_bytes, sha256_hex
from tests import fakes

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def run_node(cmd: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(cmd, fh)
    try:
        out = subprocess.run([NODE, str(ROOT / "tests" / "js_helpers.js"), fh.name],
                             capture_output=True, text=True, timeout=60, check=True)
        return json.loads(out.stdout)
    finally:
        Path(fh.name).unlink()


TRICKY = [
    {"name": "<script>alert('x')</script>", "desc": "\"quotes\" & <b>tags</b>", "n": 9007199254740991},
    {"z": "é ü ß 中文 🚀   ", "a": [None, True, False, 0, -5, "\u0001\u001f\u007f\t\n"], "m": {}},
    {"k": "🚀 emoji pair", "e": "", "nested": {"b": [{"y": 1, "x": 2}], "a": "/slash\\back"}},
]


@unittest.skipUnless(NODE, "node not installed")
class BrowserParity(unittest.TestCase):
    def test_canonical_bytes_and_hash_match_python(self):
        res = run_node({"canonical": TRICKY})
        for obj, js_text, js_hash in zip(TRICKY, res["canonical"], res["hashes"]):
            py = canonical_bytes(obj)
            self.assertEqual(js_text.encode("ascii"), py)
            self.assertEqual(js_hash, sha256_hex(py))

    def test_action_id_parity(self):
        ids = ["s-1", "s-23550000"]
        self.assertEqual(run_node({"action_ids": ids})["action_ids"], [action_id(i) for i in ids])

    def test_published_snapshot_verifies_in_js(self):
        root = Path(tempfile.mkdtemp())
        try:
            from src.scanner.run import run
            logs = [fakes.registered(1, "0x" + "a" * 40, fakes.data_uri(fakes.registration_doc(
                "<script>alert(1)</script> Ünïcødé 🚀", agent_id=1)), 13_399_000)]
            run(root=root, rpc=fakes.fast_rpc(fakes.FakeChain(13_400_000, logs)), fetcher=fakes.FakeFetcher(),
                now=1_790_000_000, log=lambda *a: None)
            index = json.loads((root / "snapshots" / "index.json").read_bytes())
            entry = index["snapshots"][0]
            res = run_node({"files": [{"path": str(root / "snapshots" / entry["file"]), "entry": entry}]})["files"][0]
            self.assertTrue(res["verified"])
            self.assertTrue(res["canonical_matches_raw"])
            self.assertEqual(res["raw_sha256"], entry["sha256"])
            tampered = root / "tampered.json"
            tampered.write_bytes((root / "snapshots" / entry["file"]).read_bytes().replace(b"13399000", b"13399001"))
            bad = run_node({"files": [{"path": str(tampered), "entry": entry}]})["files"][0]
            self.assertFalse(bad["verified"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_anchor_status_never_fabricated(self):
        aid, proof = action_id("s-1"), "0x" + "ab" * 32
        res = run_node({"anchor": {"action_id": aid, "proof": proof, "onchain": "0x" + "0" * 64}})["anchor"]
        self.assertEqual(res[0]["status"], "NOT_YET_ANCHORED")
        self.assertEqual(res[1]["status"], "NOT_ANCHORED")
        res = run_node({"anchor": {"action_id": aid, "proof": proof, "onchain": proof}})["anchor"]
        self.assertEqual(res[1]["status"], "ANCHORED_MATCH")

    # 9 external-link safety
    def test_safe_href(self):
        res = run_node({"hrefs": ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>",
                                  " https://ok.example/a", "https://u:p@ok.example/", "vbscript:x", "//evil.example",
                                  "https://ok.example/path?q=<x>", "ftp://x.org", None, 12]})
        self.assertEqual(res["hrefs"][:3], [None, None, None])
        self.assertEqual(res["hrefs"][3], "https://ok.example/a")
        self.assertEqual(res["hrefs"][4:7], [None, None, None])
        self.assertEqual(res["hrefs"][7], "https://ok.example/path?q=%3Cx%3E")
        self.assertEqual(res["hrefs"][8:], [None, None, None])
        self.assertEqual(res["link_rel"], "nofollow noopener noreferrer")


class StaticSafety(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    # 7 + 8: untrusted text can never become HTML or script
    def test_dashboard_never_assigns_html(self):
        for rel in ("public/app.js", "public/verify.js"):
            src = self.read(rel)
            for pattern in (r"\.innerHTML", r"\.outerHTML", r"insertAdjacentHTML", r"document\.write",
                            r"\beval\s*\(", r"new\s+Function", r"setTimeout\s*\(\s*['\"]", r"createContextualFragment",
                            r"\.src\s*=", r"new\s+Image"):
                self.assertIsNone(re.search(pattern, src), f"{rel}: {pattern}")
        app = self.read("public/app.js")
        self.assertIn("node.textContent =", app)
        self.assertEqual(len(re.findall(r'el\("a"', app)), 1, "all anchors must go through link()")

    def test_page_has_csp_and_static_links_are_safe(self):
        html = self.read("public/index.html")
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("script-src 'self'", html)
        self.assertNotIn("unsafe-inline", html)
        self.assertNotIn("<img", html.lower())
        for tag in re.findall(r"<a [^>]*>", html):
            self.assertIn('rel="nofollow noopener noreferrer"', tag)
        self.assertEqual(re.findall(r"<script>", html), [], "no inline scripts")

    # 18 no private key loading anywhere in shipped code
    def test_no_key_or_signing_code(self):
        forbidden = re.compile(r"(private[_ ]?key|privateKey|mnemonic|seed[_ ]?phrase|eth_sendRawTransaction|"
                               r"eth_sendTransaction|eth_sign|personal_sign|signTransaction|new\s+Wallet|"
                               r"Wallet\.from|keystore|secrets\.)", re.IGNORECASE)
        scanned = 0
        for base in ("src", "scripts", "public", ".github"):
            for p in (ROOT / base).rglob("*"):
                if p.is_file() and p.suffix in (".py", ".js", ".html", ".yml", ".yaml", ".css") \
                        and "node_modules" not in p.parts:
                    scanned += 1
                    self.assertIsNone(forbidden.search(p.read_text(encoding="utf-8")), str(p))
        self.assertGreater(scanned, 8)

    def test_rpc_is_read_only(self):
        rpc = ArcRpc(transport=fakes.FakeChain(1), min_interval=0)
        for method in ("eth_sendRawTransaction", "eth_sendTransaction", "eth_sign", "personal_sign",
                       "eth_signTransaction", "wallet_addEthereumChain"):
            with self.assertRaises(PermissionError):
                rpc.call(method, [])


@unittest.skipUnless(NODE and (ROOT / "node_modules" / "ganache").exists(), "contract deps not installed")
class ContractTests(unittest.TestCase):
    # 17 existing contract tests keep passing on the unchanged contract
    def test_contract_suite(self):
        out = subprocess.run([NODE, "--test", "tests/registry.test.js"], cwd=ROOT, capture_output=True,
                             text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stdout[-2000:] + out.stderr[-2000:])

    def test_contract_unchanged_from_predecessor(self):
        predecessor = ROOT.parents[1] / "public_arc_demo" / "contracts" / "PulseActionProofRegistry.sol"
        if not predecessor.exists():
            self.skipTest("predecessor not present")
        mine = ROOT / "contracts" / "PulseActionProofRegistry.sol"
        self.assertEqual(sha256_hex(mine.read_bytes()), sha256_hex(predecessor.read_bytes()))


if __name__ == "__main__":
    unittest.main()
