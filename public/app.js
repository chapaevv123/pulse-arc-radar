/* Pulse Arc Agent Radar - dashboard renderer.
 * Safety rules: every string from snapshots (and especially third-party
 * agent metadata) is written with textContent; links go through safeHref
 * (http/https only) and always carry rel="nofollow noopener noreferrer";
 * no images from agent metadata are loaded. This file never assigns HTML.
 */
(function (root) {
  "use strict";

  var RPC_URL = "https://rpc.mainnet.arc.io";
  var EXPLORER = "https://explorer.arc.io";
  var PAGE_SIZE = 50;
  var LINK_REL = "nofollow noopener noreferrer";
  var CLASSES = ["UNCLUSTERED", "ALL", "STANDOUT", "ACTIVE", "BACKGROUND", "CLUSTERED", "INCOMPLETE"];

  // ---- pure helpers (unit-tested in Node) --------------------------------
  function safeHref(url) {
    if (typeof url !== "string" || url.length > 2048) return null;
    var trimmed = url.trim();
    if (!/^https?:\/\/[^\s]+$/i.test(trimmed)) return null;
    try {
      var u = new URL(trimmed);
      if (u.protocol !== "https:" && u.protocol !== "http:") return null;
      if (u.username || u.password) return null;
      return u.href;
    } catch (e) { return null; }
  }
  function isTx(h) { return typeof h === "string" && /^0x[0-9a-f]{64}$/.test(h); }
  function isAddr(a) { return typeof a === "string" && /^0x[0-9a-fA-F]{40}$/.test(a); }
  function explorerTx(h) { return isTx(h) ? EXPLORER + "/tx/" + h : null; }
  function explorerAddr(a) { return isAddr(a) ? EXPLORER + "/address/" + a : null; }
  function explorerBlock(n) { return Number.isSafeInteger(n) && n >= 0 ? EXPLORER + "/block/" + n : null; }
  function shortHex(h) { return typeof h === "string" && h.length > 14 ? h.slice(0, 8) + "…" + h.slice(-6) : String(h || "–"); }
  function fmtInt(n) { return Number.isFinite(n) ? n.toLocaleString("en-US") : "–"; }
  function fmtTime(iso) { return typeof iso === "string" ? iso.replace("T", " ").replace("Z", " UTC") : "–"; }

  // ---- DOM builders --------------------------------------------------------
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k] == null ? "" : String(attrs[k]);
      else if (k === "class") node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c == null) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }
  function link(href, text, cls) {
    var safe = safeHref(href);
    if (!safe) return el("span", { text: text, class: cls || "" });
    return el("a", { href: safe, text: text, rel: LINK_REL, target: "_blank", class: cls || "" });
  }
  function badge(text, tone) { return el("span", { class: "badge " + (tone || ""), text: text }); }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function $(id) { return document.getElementById(id); }

  var TONES = {
    VALID: "good", INVALID: "bad", LIVE: "good", HTTP_ERROR: "warn", UNREACHABLE: "bad", BLOCKED: "bad",
    PENDING: "info", PENDING_FETCH: "info", FETCH_FAILED: "warn", UNVERIFIED: "info", NONE_DECLARED: "",
    STANDOUT: "good", ACTIVE: "good", BACKGROUND: "", CLUSTERED: "warn", INCOMPLETE: "bad"
  };

  function getJson(path) {
    return fetch(path, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(path + " HTTP " + r.status);
      return r.json();
    });
  }

  // ---- state -----------------------------------------------------------
  var S = { snapshot: null, directory: null, index: null, anchors: null, filter: "UNCLUSTERED", query: "", page: 0 };

  function tile(label, value, hint) {
    return el("div", { class: "tile" }, [el("span", { class: "label", text: label }),
      el("span", { class: "value", text: value }), hint ? el("span", { class: "hint", text: hint }) : null]);
  }

  function renderOverview() {
    var s = S.snapshot, st = s.stats;
    $("hero-agents").textContent = fmtInt(st.agents_observed);
    $("hero-note").textContent = "Highest agent id " + fmtInt(st.highest_agent_id) + " · " + fmtInt(st.owners) + " owner addresses";
    $("chip-scan").textContent = "Last scan " + fmtTime(s.created_at);
    var tiles = $("tiles");
    clear(tiles);
    var blockTile = tile("Latest Arc block", fmtInt(s.block_range.to), "snapshot block · live: checking…");
    tiles.appendChild(blockTile);
    [["New agents (24h)", fmtInt(st.new_agents_24h), "registered in the last ~172,800 blocks"],
     ["x402 declared", fmtInt(st.x402_declared), fmtInt(st.http_402_observed) + " answered HTTP 402"],
     ["Endpoints live", fmtInt(st.endpoint_live), fmtInt(st.endpoints_declared) + " declared · " + fmtInt(st.endpoint_pending) + " pending"],
     ["USDC-active agents", fmtInt(st.usdc_active_agents), fmtInt(st.usdc_active_owners) + " owners, rolling ~6h"],
     ["Flagged clusters", fmtInt(st.clusters), fmtInt(st.clustered_agents) + " agents in mass clusters"],
     ["Valid metadata", fmtInt(st.metadata_valid), fmtInt(st.metadata_invalid) + " invalid · " + fmtInt(st.metadata_unverified) + " unverified"],
     ["Last scan", fmtTime(s.created_at).slice(5, 16), "snapshot " + s.snapshot_id]
    ].forEach(function (t) { tiles.appendChild(tile(t[0], t[1], t[2])); });
    liveHead(blockTile.lastChild);

    var cov = s.coverage, deploy = cov.registry_deploy_block, head = s.block_range.to;
    var pct = Math.max(0, Math.min(1, (head - cov.backfill_cursor + 1) / (head - deploy + 1)));
    $("coverage-fill").style.width = (pct * 100).toFixed(1) + "%";
    $("coverage-meter").setAttribute("aria-label", "History coverage " + (pct * 100).toFixed(1) + "%");
    $("coverage-text").textContent = cov.backfill_complete
      ? "Complete history since IdentityRegistry deployment (block " + fmtInt(deploy) + ")"
      : "Blocks " + fmtInt(cov.backfill_cursor) + "–" + fmtInt(head) + " scanned (" + (pct * 100).toFixed(1) +
        "% of history since block " + fmtInt(deploy) + "); backfill continues each run";
    $("notice").textContent = s.notice;
  }

  function liveHead(hintNode) {
    fetch(RPC_URL, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_blockNumber", params: [] }) })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var n = parseInt(j.result, 16);
        hintNode.textContent = Number.isSafeInteger(n) ? "snapshot block · live head " + fmtInt(n) : "snapshot block";
      })
      .catch(function () { hintNode.textContent = "snapshot block · live head unavailable"; });
  }

  // ---- timeline (single series: no legend; hover tooltip per bar) --------
  function renderTimeline() {
    var counts = {};
    S.directory.agents.forEach(function (a) { if (a.time) counts[a.time.slice(0, 10)] = (counts[a.time.slice(0, 10)] || 0) + 1; });
    var days = Object.keys(counts).sort();
    var host = $("timeline");
    clear(host);
    if (!days.length) { host.appendChild(el("p", { class: "muted", text: "No registrations in scanned range yet." })); return; }
    var NS = "http://www.w3.org/2000/svg", W = Math.max(280, Math.round(host.clientWidth || 1000)), H = 200, padL = 36, padB = 22, padT = 8;
    var max = Math.max.apply(null, days.map(function (d) { return counts[d]; }));
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Registrations per day, " + days[0] + " to " + days[days.length - 1]);
    function svgEl(tag, attrs, text) {
      var n = document.createElementNS(NS, tag);
      Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
      if (text != null) n.textContent = text;
      return n;
    }
    [0, 0.5, 1].forEach(function (f) {
      var y = padT + (H - padT - padB) * (1 - f);
      svg.appendChild(svgEl("line", { x1: padL, x2: W, y1: y, y2: y, class: "grid" }));
      svg.appendChild(svgEl("text", { x: 0, y: y + 4 }, fmtInt(Math.round(max * f))));
    });
    var slot = (W - padL) / days.length, bw = Math.max(1, slot - 2), tip = $("tooltip");
    days.forEach(function (d, i) {
      var h = (H - padT - padB) * counts[d] / max, x = padL + i * slot + 1, y = H - padB - h;
      var r = Math.min(4, bw / 2, h);
      var path = "M" + x + "," + (H - padB) + "V" + (y + r) + "Q" + x + "," + y + " " + (x + r) + "," + y +
        "H" + (x + bw - r) + "Q" + (x + bw) + "," + y + " " + (x + bw) + "," + (y + r) + "V" + (H - padB) + "Z";
      var bar = svgEl("path", { d: path, class: "bar" });
      var hit = svgEl("rect", { x: padL + i * slot, y: padT, width: slot, height: H - padT - padB, fill: "transparent" });
      function show(ev) {
        bar.classList.add("active");
        tip.textContent = d + " · " + fmtInt(counts[d]) + " registrations";
        tip.hidden = false;
        tip.style.left = Math.min(ev.clientX + 12, window.innerWidth - 220) + "px";
        tip.style.top = (ev.clientY + 12) + "px";
      }
      function hide() { bar.classList.remove("active"); tip.hidden = true; }
      hit.addEventListener("mousemove", show);
      hit.addEventListener("mouseleave", hide);
      svg.appendChild(bar);
      svg.appendChild(hit);
      var every = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor((W - padL) / 48))));
      if (i % every === 0) {
        svg.appendChild(svgEl("text", { x: x, y: H - 6 }, d.slice(5)));
      }
    });
    host.appendChild(svg);
  }

  // ---- agents table --------------------------------------------------------
  function filtered() {
    var q = S.query.trim().toLowerCase();
    return S.directory.agents.filter(function (a) {
      if (S.filter === "UNCLUSTERED" ? a["class"] === "CLUSTERED" : (S.filter !== "ALL" && a["class"] !== S.filter)) return false;
      if (!q) return true;
      return String(a.id) === q || (a.owner || "").toLowerCase().indexOf(q) >= 0 ||
        (a.name || "").toLowerCase().indexOf(q) >= 0;
    });
  }

  function endpointCell(a) {
    var ep = a.endpoint || {};
    var label = ep.status === "NONE_DECLARED" ? "none declared" : ep.status + (ep.http_status ? " " + ep.http_status : "");
    return el("td", null, [badge(label, TONES[ep.status])]);
  }

  function usdcCell(a) {
    if (!a.usdc) return el("td", { class: "muted", text: "–" });
    return el("td", null, [el("span", { class: "mono", text: a.usdc.balance == null ? "–" : a.usdc.balance }),
      el("div", { class: "muted", text: a.usdc.transfers + " transfers (6h)" })]);
  }

  function detailRow(a) {
    var defs = S.directory.signal_defs || {};
    var box = el("div", null, [
      a.description ? el("p", { text: a.description }) : null,
      el("div", null, a.signals.map(function (s) {
        var d = defs[s] || {};
        return el("div", { class: "sig" }, [badge(s, d.kind === "HEURISTIC" ? "warn" : "info"),
          el("span", { class: "muted", text: " " + (d.kind || "") + " — " + (d.explanation || "") })]);
      })),
      el("p", { class: "muted", text: "Metadata source: " + (a.meta_source || "–") + " · backlink: " + (a.backlink || "–") +
        (a.issues.length ? " · issues: " + a.issues.join(", ") : "") +
        (a.trust.length ? " · trust: " + a.trust.join(", ") : "") }),
      a.endpoint && a.endpoint.url ? el("p", { class: "muted" }, ["Endpoint (untrusted): ", link(a.endpoint.url, a.endpoint.url)]) : null,
      a.uri ? el("p", { class: "muted" }, ["Registration URL (untrusted): ", link(a.uri, a.uri)]) : null,
      a.usdc && a.usdc.last_tx ? el("p", { class: "muted" }, ["Last USDC transfer: ", link(explorerTx(a.usdc.last_tx), shortHex(a.usdc.last_tx), "mono")]) : null
    ]);
    return el("tr", { class: "detail" }, [el("td", { colspan: "9" }, [box])]);
  }

  function renderTable() {
    var rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    S.page = Math.min(S.page, pages - 1);
    var body = $("agent-rows");
    clear(body);
    rows.slice(S.page * PAGE_SIZE, (S.page + 1) * PAGE_SIZE).forEach(function (a) {
      var tr = el("tr", { class: "row", tabindex: "0" }, [
        el("td", null, [el("span", { class: "mono", text: "#" + a.id }), el("span", { class: "name", text: a.name || "(no name)" })]),
        el("td", null, [el("span", { text: a.time ? "≈ " + fmtTime(a.time).slice(0, 16) : "–" }),
          el("div", { class: "muted" }, [link(explorerBlock(a.block), "block " + fmtInt(a.block))])]),
        el("td", null, [link(explorerAddr(a.owner), shortHex(a.owner), "mono")]),
        el("td", null, [badge(a.meta_status || "–", TONES[a.meta_status])]),
        endpointCell(a),
        el("td", { text: a.x402 === true ? "declared" : a.x402 === false ? "no" : "–" }),
        usdcCell(a),
        el("td", null, [badge(a["class"], TONES[a["class"]]), el("div", { class: "muted", text: a.signals[0] || "" })]),
        el("td", null, [link(explorerTx(a.tx), "tx " + shortHex(a.tx), "mono"), el("div", { class: "muted" }, [
          link(explorerAddr(S.snapshot.registries.identity), "IdentityRegistry")])])
      ]);
      var open = null;
      function toggle(ev) {
        if (ev.target && ev.target.closest && ev.target.closest("a")) return;
        if (open) { open.remove(); open = null; } else { open = detailRow(a); tr.after(open); }
      }
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", function (ev) { if (ev.key === "Enter") toggle(ev); });
      body.appendChild(tr);
    });
    $("table-count").textContent = fmtInt(rows.length) + " of " + fmtInt(S.directory.agents.length) + " agents · click a row for evidence";
    $("page-info").textContent = "Page " + (S.page + 1) + " / " + pages;
    $("prev").disabled = S.page === 0;
    $("next").disabled = S.page >= pages - 1;
  }

  function renderFilters() {
    var box = $("class-filter");
    clear(box);
    var counts = S.snapshot.stats.classes || {};
    CLASSES.forEach(function (c) {
      var n = c === "ALL" ? S.directory.agents.length
        : c === "UNCLUSTERED" ? S.directory.agents.length - (counts.CLUSTERED || 0) : (counts[c] || 0);
      var b = el("button", { type: "button", "aria-pressed": String(S.filter === c), text: c.charAt(0) + c.slice(1).toLowerCase() + " " + fmtInt(n) });
      if (c === "UNCLUSTERED") b.title = "All agents outside mass-registration clusters";
      else if (S.directory.class_defs && S.directory.class_defs[c]) b.title = S.directory.class_defs[c];
      b.addEventListener("click", function () { S.filter = c; S.page = 0; renderFilters(); renderTable(); });
      box.appendChild(b);
    });
  }

  function renderClusters() {
    var body = $("cluster-rows");
    clear(body);
    if (!S.snapshot.clusters.length) { body.appendChild(el("tr", null, [el("td", { colspan: "4", class: "muted", text: "No clusters above thresholds." })])); return; }
    S.snapshot.clusters.forEach(function (c) {
      var key = c.kind === "MASS_REGISTRATION_CLUSTER" ? link(explorerAddr(c.key), shortHex(c.key), "mono") : el("span", { class: "mono", text: "uri sha256 " + shortHex(c.key) });
      body.appendChild(el("tr", null, [el("td", null, [badge(c.kind, "warn")]), el("td", null, [key]),
        el("td", { text: fmtInt(c.agents) }), el("td", { class: "mono", text: "#" + c.first_agent_id + " – #" + c.last_agent_id })]));
    });
  }

  function renderPulseOnArc() {
    var a = S.anchors || {}, agent = a.pulse_agent || {}, box = $("pulse-on-arc");
    clear(box);
    function card(title, status, lines) {
      return el("div", { class: "card" }, [el("h3", { text: title }), el("span", { class: "status", text: status })]
        .concat(lines.map(function (l) { return typeof l === "string" ? el("p", { text: l }) : l; })));
    }
    var roles = a.roles || {};
    box.appendChild(card("Pulse ERC-8004 identity", agent.agent_id ? "REGISTERED · agent #" + agent.agent_id : "PENDING MAINNET DEPLOYMENT", [
      "Pulse will register as an agent on the Arc mainnet IdentityRegistry, pointing to this radar.",
      el("p", null, ["Agent owner (Pulse wallet): ", link(explorerAddr(agent.owner), shortHex(agent.owner), "mono")]),
      el("p", null, [link(agent.metadata_uri, "Registration file"), " · testnet predecessor agent #894567 (",
        link("https://github.com/chapaevv123/pulse-arc", "chapaevv123/pulse-arc"), ")"])]));
    box.appendChild(card("PulseActionProofRegistry", a.registry ? "DEPLOYED" : "PENDING MAINNET DEPLOYMENT", [
      "Minimal, non-custodial, write-once registry: actionId → proofHash. No token, custody, upgrade or admin path.",
      el("p", null, [a.recorder ? "Recorder: " : "Planned deployer/recorder (Pulse wallet): ",
        link(explorerAddr(a.recorder || roles.deployer_recorder_wallet), shortHex(a.recorder || roles.deployer_recorder_wallet), "mono")])]));
    var prepared = (a.prepared_anchors || []).length;
    box.appendChild(card("Anchored snapshot proofs", (a.anchors || []).length ? (a.anchors.length + " anchored") : "NOT YET ANCHORED", [
      "Each snapshot's SHA-256 can be anchored on Arc, so anyone can prove a signal existed at a given block.",
      prepared + " snapshots prepared for anchoring (not sent). Anchoring happens only after owner-approved mainnet deployment.",
      el("p", null, ["Builder: ", link("https://x.com/MagnatSV", "@MagnatSV"), " · ", link(explorerAddr(roles.builder_wallet), shortHex(roles.builder_wallet), "mono")])]));
  }

  // ---- verifier ----------------------------------------------------------
  function renderVerifyOptions() {
    var sel = $("snapshot-select");
    clear(sel);
    S.index.snapshots.filter(function (e) { return e.file; }).forEach(function (e) {
      sel.appendChild(el("option", { value: e.id, text: e.id + " · blocks " + fmtInt(e.from_block) + "–" + fmtInt(e.to_block) + " · " + fmtTime(e.created_at) }));
    });
  }

  function verifySelected() {
    var out = $("verify-out"), id = $("snapshot-select").value;
    var entry = S.index.snapshots.filter(function (e) { return e.id === id; })[0];
    clear(out);
    if (!entry) return;
    out.appendChild(el("dt", { text: "Status" }));
    out.appendChild(el("dd", { text: "Computing…" }));
    fetch("snapshots/" + entry.file, { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.arrayBuffer(); })
      .then(function (buf) { return root.PulseVerify.verifySnapshot(new Uint8Array(buf), entry); })
      .then(function (v) {
        return root.PulseVerify.anchorStatus(S.anchors, v.action_id, v.raw_sha256, RPC_URL).then(function (an) { return [v, an]; });
      })
      .then(function (res) {
        var v = res[0], an = res[1];
        clear(out);
        [["Result", v.verified ? "VERIFIED — file hash, canonical hash, index and actionId all match" : "MISMATCH — do not trust this file"],
         ["File SHA-256", v.raw_sha256], ["Canonical SHA-256", v.canonical_sha256 + (v.canonical_matches_raw ? "  (matches file)" : "  (DIFFERS)")],
         ["Index SHA-256", (v.index_sha256 || "–") + (v.index_match ? "  (matches)" : "  (DIFFERS)")],
         ["actionId", v.action_id + (v.action_id_match ? "  (matches snapshot)" : "  (DIFFERS)")],
         ["Onchain anchor", an.status + (an.detail ? " · " + an.detail : "") + (an.onchain ? " · " + an.onchain : "")]
        ].forEach(function (p) { out.appendChild(el("dt", { text: p[0] })); out.appendChild(el("dd", { class: "mono", text: p[1] })); });
      })
      .catch(function (e) { clear(out); out.appendChild(el("dt", { text: "Error" })); out.appendChild(el("dd", { text: String(e.message || e) })); });
  }

  function renderDefs() {
    var box = $("signal-defs"), defs = S.directory.signal_defs || {};
    clear(box);
    Object.keys(defs).sort().forEach(function (k) {
      box.appendChild(el("dt", null, [badge(k, defs[k].kind === "HEURISTIC" ? "warn" : "info")]));
      box.appendChild(el("dd", { text: defs[k].kind + " — " + defs[k].explanation }));
    });
  }

  function start() {
    Promise.all([getJson("snapshots/latest.json"), getJson("snapshots/directory.json"),
                 getJson("snapshots/index.json"), getJson("snapshots/anchors.json").catch(function () { return null; })])
      .then(function (r) {
        S.snapshot = r[0]; S.directory = r[1]; S.index = r[2]; S.anchors = r[3];
        renderOverview(); renderTimeline(); renderFilters();
        var rt = null;
        window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(renderTimeline, 150); });
        renderTable(); renderClusters();
        renderPulseOnArc(); renderVerifyOptions(); renderDefs();
        $("search").addEventListener("input", function (e) { S.query = e.target.value; S.page = 0; renderTable(); });
        $("prev").addEventListener("click", function () { S.page -= 1; renderTable(); });
        $("next").addEventListener("click", function () { S.page += 1; renderTable(); });
        $("verify-btn").addEventListener("click", verifySelected);
        verifySelected();
      })
      .catch(function (e) {
        var box = $("load-error");
        box.hidden = false;
        box.textContent = "Could not load snapshot data: " + (e.message || e);
      });
  }

  var api = { safeHref: safeHref, explorerTx: explorerTx, explorerAddr: explorerAddr, explorerBlock: explorerBlock,
              shortHex: shortHex, LINK_REL: LINK_REL };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else { root.PulseRadar = api; document.addEventListener("DOMContentLoaded", start); }
})(typeof self !== "undefined" ? self : this);
