/* Pulse Arc Agent Radar - snapshot verifier (browser + Node).
 *
 * Canonical form mirrors src/scanner/snapshot.py exactly:
 *   sorted keys, no whitespace, printable-ASCII output (DEL and non-ASCII code units as
 *   lowercase \uXXXX), safe integers only, no floats.
 * proofHash = sha256(canonical bytes); actionId = sha256(domain + snapshot_id).
 * Read-only: the only network call is an eth_call to a registry that exists
 * only after owner-approved mainnet deployment.
 */
(function (root) {
  "use strict";

  var ACTION_DOMAIN = "pulse-arc-radar/v1/snapshot/";
  var SEL_PROOF_HASH_BY_ACTION = "0x828006bc";
  var ZERO32 = "0x" + "0".repeat(64);

  function escapeString(s) {
    return JSON.stringify(s).replace(/[^\x00-\x7e]/g, function (c) {
      return "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0");
    });
  }

  function canonicalize(v) {
    if (v === null) return "null";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "number") {
      if (!Number.isSafeInteger(v)) throw new Error("NON_SAFE_INTEGER");
      return String(v);
    }
    if (typeof v === "string") return escapeString(v);
    if (Array.isArray(v)) return "[" + v.map(canonicalize).join(",") + "]";
    if (typeof v === "object") {
      var keys = Object.keys(v).sort();
      return "{" + keys.map(function (k) {
        if (!/^[\x00-\x7f]*$/.test(k)) throw new Error("KEY_NOT_ASCII");
        return escapeString(k) + ":" + canonicalize(v[k]);
      }).join(",") + "}";
    }
    throw new Error("TYPE_NOT_ALLOWED");
  }

  function subtle() {
    var c = (typeof globalThis !== "undefined" && globalThis.crypto) || null;
    if (!c || !c.subtle) throw new Error("WEBCRYPTO_UNAVAILABLE");
    return c.subtle;
  }

  function toHex(buf) {
    return "0x" + Array.prototype.map.call(new Uint8Array(buf), function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }

  function sha256Hex(bytes) {
    return subtle().digest("SHA-256", bytes).then(toHex);
  }

  function asciiBytes(str) {
    var out = new Uint8Array(str.length);
    for (var i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      if (c > 0x7f) throw new Error("CANONICAL_NOT_ASCII");
      out[i] = c;
    }
    return out;
  }

  function actionId(snapshotId) {
    return sha256Hex(new TextEncoder().encode(ACTION_DOMAIN + snapshotId));
  }

  /* rawBytes: Uint8Array of the published file. indexEntry: optional entry
   * from index.json. Returns hashes and the three independent checks. */
  function verifySnapshot(rawBytes, indexEntry) {
    var text = new TextDecoder("utf-8", { fatal: true }).decode(rawBytes);
    var parsed = JSON.parse(text);
    var canonical = canonicalize(parsed);
    return Promise.all([sha256Hex(rawBytes), sha256Hex(asciiBytes(canonical)), actionId(parsed.snapshot_id)])
      .then(function (h) {
        return {
          snapshot_id: parsed.snapshot_id,
          raw_sha256: h[0],
          canonical_sha256: h[1],
          canonical_matches_raw: h[0] === h[1],
          index_sha256: indexEntry ? indexEntry.sha256 : null,
          index_match: indexEntry ? indexEntry.sha256 === h[0] : null,
          action_id: h[2],
          action_id_match: parsed.action_id === h[2],
          verified: h[0] === h[1] && parsed.action_id === h[2] && (!indexEntry || indexEntry.sha256 === h[0]),
          snapshot: parsed
        };
      });
  }

  /* Anchor status. Never claims an anchor that the chain does not show. */
  function anchorStatus(anchors, snapshotActionId, proofHash, rpcUrl, fetchImpl) {
    if (!anchors || !anchors.registry) {
      return Promise.resolve({ status: "NOT_YET_ANCHORED",
        detail: (anchors && anchors.registry_status) || "PENDING_MAINNET_DEPLOYMENT" });
    }
    if (!/^0x[0-9a-fA-F]{40}$/.test(anchors.registry) || !/^0x[0-9a-f]{64}$/.test(snapshotActionId)) {
      return Promise.resolve({ status: "ANCHOR_CONFIG_INVALID" });
    }
    var f = fetchImpl || fetch;
    var body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_call",
      params: [{ to: anchors.registry, data: SEL_PROOF_HASH_BY_ACTION + snapshotActionId.slice(2) }, "latest"] });
    return f(rpcUrl, { method: "POST", headers: { "content-type": "application/json" }, body: body })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || typeof j.result !== "string") return { status: "ANCHOR_LOOKUP_FAILED" };
        var onchain = j.result.toLowerCase();
        if (onchain === ZERO32) return { status: "NOT_ANCHORED", onchain: onchain };
        return { status: onchain === proofHash.toLowerCase() ? "ANCHORED_MATCH" : "ANCHOR_MISMATCH", onchain: onchain };
      })
      .catch(function () { return { status: "ANCHOR_LOOKUP_FAILED" }; });
  }

  var api = { canonicalize: canonicalize, sha256Hex: sha256Hex, actionId: actionId, asciiBytes: asciiBytes,
              verifySnapshot: verifySnapshot, anchorStatus: anchorStatus, ACTION_DOMAIN: ACTION_DOMAIN };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PulseVerify = api;
})(typeof self !== "undefined" ? self : this);
