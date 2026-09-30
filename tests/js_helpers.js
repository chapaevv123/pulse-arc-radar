// Node harness used by tests/test_safety.py. Reads a JSON command on argv[2].
const fs = require("node:fs");
const path = require("node:path");
const verify = require(path.join(__dirname, "..", "public", "verify.js"));
const app = require(path.join(__dirname, "..", "public", "app.js"));

async function main() {
  const cmd = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const out = {};
  if (cmd.canonical) {
    out.canonical = cmd.canonical.map((v) => {
      const c = verify.canonicalize(v);
      return c;
    });
    out.hashes = await Promise.all(out.canonical.map((c) => verify.sha256Hex(verify.asciiBytes(c))));
  }
  if (cmd.files) {
    out.files = await Promise.all(cmd.files.map(async (f) => {
      const raw = new Uint8Array(fs.readFileSync(f.path));
      const r = await verify.verifySnapshot(raw, f.entry || null);
      delete r.snapshot;
      return r;
    }));
  }
  if (cmd.action_ids) out.action_ids = await Promise.all(cmd.action_ids.map((s) => verify.actionId(s)));
  if (cmd.hrefs) out.hrefs = cmd.hrefs.map((h) => app.safeHref(h));
  if (cmd.anchor) {
    const fakeFetch = async () => ({ json: async () => ({ result: cmd.anchor.onchain }) });
    out.anchor = [
      await verify.anchorStatus({ registry: null, registry_status: "PENDING_MAINNET_DEPLOYMENT" }, cmd.anchor.action_id, cmd.anchor.proof, "x", fakeFetch),
      await verify.anchorStatus({ registry: "0x" + "1".repeat(40) }, cmd.anchor.action_id, cmd.anchor.proof, "x", fakeFetch),
    ];
  }
  out.link_rel = app.LINK_REL;
  process.stdout.write(JSON.stringify(out));
}
main().catch((e) => { console.error(e); process.exit(1); });
