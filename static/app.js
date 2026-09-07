const API = "";

const COLORS = {
  corroborates: "#059669",
  contradicts: "#dc2626",
  reconciled: "#d97706",
  accent: "#4f46e5",
};
const DOC_PALETTE = ["#4f46e5", "#0284c7", "#059669", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#65a30d"];

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}
function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function pdfLink(documentId, page) { return `${API}/api/documents/${documentId}/pdf#page=${page || 1}`; }
function shortName(n) { return String(n).replace(/^[0-9a-f]{32}_/, "").replace(/\.pdf$/i, ""); }
function fmtSecs(s) {
  if (s == null) return "-";
  return s >= 60 ? `${Math.floor(s / 60)}m ${(s % 60).toFixed(0)}s` : `${s.toFixed(1)}s`;
}

const ICONS = {
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  loader: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  externalLink: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
  barChart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>`,
  file: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  inbox: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
  link: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  shield: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  arrowRight: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`,
  layers: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
};

const STATUS_META = {
  done: { icon: ICONS.check, label: "Done" },
  processing: { icon: ICONS.loader, label: "Processing" },
  pending: { icon: ICONS.clock, label: "Queued" },
  failed: { icon: ICONS.x, label: "Failed" },
};
function statusBadge(status) {
  const m = STATUS_META[status] || { icon: "", label: status };
  const spin = status === "processing" ? ' style="animation:spin 1.4s linear infinite;display:flex"' : ' style="display:flex"';
  return `<span class="badge status-${status}"><span${spin}>${m.icon}</span>${m.label}</span>`;
}
if (!document.getElementById("spin-kf")) {
  const s = document.createElement("style");
  s.id = "spin-kf";
  s.textContent = "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}";
  document.head.appendChild(s);
}
function emptyState(text) { return `<div class="empty">${ICONS.inbox}<span>${escapeHtml(text)}</span></div>`; }

// ---------------- shared data cache ----------------

const store = { docs: [], facts: [], rels: [], stats: null };

async function loadAll() {
  const [stats, docs, facts, rels] = await Promise.all([
    fetch(`${API}/api/stats`).then((r) => r.json()),
    fetch(`${API}/api/documents`).then((r) => r.json()),
    fetch(`${API}/api/facts`).then((r) => r.json()),
    fetch(`${API}/api/relationships`).then((r) => r.json()),
  ]);
  store.stats = stats; store.docs = docs; store.facts = facts; store.rels = rels;
  renderHeaderMeta();
}

function renderHeaderMeta() {
  const busy = store.docs.some((d) => d.status === "processing" || d.status === "pending");
  document.getElementById("headerMeta").innerHTML = busy
    ? `<span class="live-dot busy"></span> Processing ${store.docs.filter((d) => d.status === "processing" || d.status === "pending").length} document(s)`
    : `<span class="live-dot"></span> Idle · ${store.stats ? store.stats.facts : 0} facts indexed`;
}

// ---------------- nav ----------------

const views = ["overview", "graph", "relationships", "facts", "documents", "issues", "upload"];

async function activateView(view) {
  if (!views.includes(view)) view = "overview";
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  views.forEach((v) => document.getElementById(`view-${v}`).classList.toggle("active", v === view));
  if (location.hash.slice(1) !== view) location.hash = view;
  await refreshCurrent(view);
}

document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => activateView(btn.dataset.view));
});
window.addEventListener("hashchange", () => activateView(location.hash.slice(1)));

async function refreshCurrent(view) {
  if (view === "overview") { await loadAll(); renderOverview(); }
  if (view === "graph") { await loadAll(); renderGraph(); }
  if (view === "relationships") { await loadAll(); renderRelationships(); }
  if (view === "facts") { await loadAll(); loadFactFilters(); renderFacts(); }
  if (view === "documents") loadDocuments();
  if (view === "issues") loadIssues();
}

// ---------------- overview ----------------

function kpi(label, value, foot, iconName, tone) {
  return `<div class="kpi">
    <div class="kpi-top">
      <span class="kpi-label">${label}</span>
      <span class="kpi-icon ${tone || ""}">${ICONS[iconName]}</span>
    </div>
    <div class="kpi-value num">${value}</div>
    <div class="kpi-foot">${foot}</div>
  </div>`;
}

function donut(segments, size = 150) {
  const total = segments.reduce((a, s) => a + s.value, 0);
  const r = size / 2 - 16, cx = size / 2, cy = size / 2, C = 2 * Math.PI * r;
  let offset = 0;
  const arcs = segments.filter((s) => s.value > 0).map((s) => {
    const frac = total ? s.value / total : 0;
    const dash = `${frac * C} ${C - frac * C}`;
    const circle = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}" stroke-width="18"
      stroke-dasharray="${dash}" stroke-dashoffset="${-offset * C}" transform="rotate(-90 ${cx} ${cy})"
      style="transition:stroke-dasharray .9s cubic-bezier(.34,1.1,.64,1)"><title>${escapeHtml(s.label)}: ${s.value}</title></circle>`;
    offset += frac;
    return circle;
  }).join("");
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#f2f4f7" stroke-width="18"/>
    ${arcs}
    <text x="${cx}" y="${cy - 2}" text-anchor="middle" font-size="24" font-weight="600" fill="#101828" style="font-variant-numeric:tabular-nums">${total}</text>
    <text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="11" fill="#98a2b3">total</text>
  </svg>`;
}

function ring(pct, size = 160) {
  const r = size / 2 - 14, cx = size / 2, cy = size / 2, C = 2 * Math.PI * r;
  const filled = (pct / 100) * C;
  const color = pct >= 90 ? "#059669" : pct >= 70 ? "#d97706" : "#dc2626";
  return `<div style="text-align:center">
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#f2f4f7" stroke-width="14"/>
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="14" stroke-linecap="round"
        stroke-dasharray="${filled} ${C - filled}" transform="rotate(-90 ${cx} ${cy})"
        style="transition:stroke-dasharray 1s cubic-bezier(.34,1.1,.64,1)"/>
      <text x="${cx}" y="${cy + 2}" text-anchor="middle" font-size="27" font-weight="600" fill="#101828" style="font-variant-numeric:tabular-nums">${pct}%</text>
      <text x="${cx}" y="${cy + 20}" text-anchor="middle" font-size="11" fill="#98a2b3">verified</text>
    </svg>
  </div>`;
}

function renderOverview() {
  const s = store.stats;
  const grounded = s.facts - s.ungrounded_facts;
  const pct = s.facts ? Math.round((grounded / s.facts) * 100) : 0;
  const rt = s.relationships_by_type || {};

  document.getElementById("kpiRow").innerHTML = [
    kpi("Documents", s.documents, `${s.documents_done} processed`, "file", "accent"),
    kpi("Facts extracted", s.facts, `${grounded} with verified quotes`, "layers", "accent"),
    kpi("Relationships", s.relationships, `${rt.contradicts || 0} contradictions found`, "link", "green"),
    kpi("Flagged issues", s.issues, "surfaced, not hidden", "alert", "amber"),
  ].join("");

  // Pipeline flow, aggregated from the stats already included in the
  // documents list response (no per-document round trips).
  const withStats = store.docs.filter((d) => d.stats);
  const sum = (key) => withStats.reduce((a, d) => a + (d.stats[key] || 0), 0);
  const activeStage = (store.docs.find((d) => d.status === "processing") || {}).progress?.stage;
  const stages = [
    { label: "Pages read", val: sum("pages"), key: null },
    { label: "Chunks", val: sum("chunks"), key: null },
    { label: "Facts", val: s.facts, key: "extracting" },
    { label: "Candidate pairs", val: sum("candidate_pairs"), key: "comparing" },
    { label: "Relationships", val: s.relationships, key: null },
  ];
  document.getElementById("pipelineFlow").innerHTML = stages.map((st, i) => `
    ${i ? `<div class="flow-arrow">${ICONS.arrowRight}</div>` : ""}
    <div class="flow-node ${activeStage && st.key === activeStage ? "active" : ""}">
      <div class="fn-val num">${st.val}</div>
      <div class="fn-label">${st.label}</div>
    </div>`).join("");
  document.getElementById("flowStatus").innerHTML = activeStage
    ? `<span class="live-dot busy" style="display:inline-block;vertical-align:middle;margin-right:5px"></span>live`
    : "";

  const segs = [
    { label: "Corroborates", value: rt.corroborates || 0, color: COLORS.corroborates },
    { label: "Contradicts", value: rt.contradicts || 0, color: COLORS.contradicts },
    { label: "Reconciled", value: rt.reconciled || 0, color: COLORS.reconciled },
  ];
  document.getElementById("relDonut").innerHTML = donut(segs);
  document.getElementById("relLegend").innerHTML = segs.map((x) => `
    <div class="legend-item">
      <span class="legend-dot" style="background:${x.color}"></span>
      <span class="legend-label">${x.label}</span>
      <span class="legend-val num">${x.value}</span>
    </div>`).join("");

  document.getElementById("groundingRing").innerHTML = ring(pct) +
    `<div class="small" style="margin-top:6px">${grounded} of ${s.facts} facts had their quote found verbatim on the source page</div>`;

  const byDoc = {};
  store.facts.forEach((f) => { byDoc[f.document_name] = (byDoc[f.document_name] || 0) + 1; });
  const rows = Object.entries(byDoc).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const max = Math.max(...rows.map((r) => r[1]), 1);
  document.getElementById("factsByDoc").innerHTML = rows.length ? rows.map(([name, n], i) => `
    <div class="bar-row">
      <div class="bar-label" title="${escapeHtml(name)}">${escapeHtml(shortName(name))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(n / max) * 100}%;background:${DOC_PALETTE[i % DOC_PALETTE.length]}"></div></div>
      <div class="bar-val num">${n}</div>
    </div>`).join("") : emptyState("No facts extracted yet.");
}

// ---------------- knowledge graph ----------------

let graphState = { nodes: [], links: [], transform: { x: 0, y: 0, k: 1 }, raf: null, filters: new Set(["corroborates", "contradicts", "reconciled"]) };

function buildGraphData() {
  const active = graphState.filters;
  const rels = store.rels.filter((r) => active.has(r.relation_type));
  const idSet = new Set();
  rels.forEach((r) => { idSet.add(r.fact_id_a); idSet.add(r.fact_id_b); });
  const factById = {};
  store.facts.forEach((f) => { factById[f.id] = f; });
  const docIds = [...new Set(store.facts.map((f) => f.document_id))];

  const nodes = [...idSet].filter((id) => factById[id]).map((id) => {
    const f = factById[id];
    return {
      id, fact: f, degree: 0,
      color: DOC_PALETTE[docIds.indexOf(f.document_id) % DOC_PALETTE.length],
      x: 0, y: 0, vx: 0, vy: 0,
    };
  });
  const index = {};
  nodes.forEach((n, i) => { index[n.id] = i; });
  const links = rels.filter((r) => index[r.fact_id_a] !== undefined && index[r.fact_id_b] !== undefined)
    .map((r) => {
      const s = index[r.fact_id_a], t = index[r.fact_id_b];
      nodes[s].degree++; nodes[t].degree++;
      return { s, t, type: r.relation_type, rel: r };
    });
  return { nodes, links };
}

// Velocity-based force layout (d3-force style: charge repulsion with a
// distance cutoff, link springs, mild centering, velocity damping). An
// earlier Fruchterman-Reingold version with hard boundary clamping pinned
// disconnected components against the canvas edges -- this one lets the
// layout find its own scale and the view auto-fits to it afterwards.
const SIM = { charge: -420, cutoff: 420, linkDist: 68, linkK: 0.05, center: 0.013, damping: 0.87 };

function layoutInit(nodes, w, h) {
  const R = Math.min(w, h) * 0.3;
  nodes.forEach((n, i) => {
    const a = i * 2.399963; // golden-angle spiral: even, non-overlapping start
    const rad = R * Math.sqrt(i / Math.max(1, nodes.length));
    n.x = w / 2 + Math.cos(a) * rad;
    n.y = h / 2 + Math.sin(a) * rad;
    n.vx = 0; n.vy = 0;
  });
}

function layoutStep(nodes, links, w, h, alpha) {
  for (const n of nodes) { n.fx = 0; n.fy = 0; }

  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy;
      if (d2 > SIM.cutoff * SIM.cutoff) continue;
      if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
      const d = Math.sqrt(d2);
      const f = SIM.charge / d2;           // negative => repulsive
      const ux = dx / d, uy = dy / d;
      a.fx -= ux * f; a.fy -= uy * f;
      b.fx += ux * f; b.fy += uy * f;
    }
  }

  for (const l of links) {
    const a = nodes[l.s], b = nodes[l.t];
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.max(0.01, Math.sqrt(dx * dx + dy * dy));
    const f = (d - SIM.linkDist) * SIM.linkK;
    const ux = dx / d, uy = dy / d;
    a.fx += ux * f; a.fy += uy * f;
    b.fx -= ux * f; b.fy -= uy * f;
  }

  for (const n of nodes) {
    n.fx += (w / 2 - n.x) * SIM.center;
    n.fy += (h / 2 - n.y) * SIM.center;
    n.vx = (n.vx + n.fx * alpha) * SIM.damping;
    n.vy = (n.vy + n.fy * alpha) * SIM.damping;
    n.x += n.vx;
    n.y += n.vy;
  }
}

// After the layout settles, fit it to the viewport so the graph is always
// fully visible regardless of the scale the simulation happened to find.
function fitToView(nodes, svg, gRoot, pad = 85) {
  if (!nodes.length) return;
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  // Measured live rather than cached: the SVG has no layout size at the
  // moment its tab is first switched on, so a cached width/height would
  // frame the graph against the wrong viewport and clip it.
  const box = svg.getBoundingClientRect();
  const w = box.width || 900, h = box.height || 620;
  const gw = Math.max(1, maxX - minX), gh = Math.max(1, maxY - minY);
  const k = Math.max(0.25, Math.min(2.2, Math.min((w - pad * 2) / gw, (h - pad * 2) / gh)));
  graphState.transform = {
    k,
    x: w / 2 - ((minX + maxX) / 2) * k,
    y: h / 2 - ((minY + maxY) / 2) * k,
  };
  applyTransform(gRoot);
}

function renderGraph() {
  const svg = document.getElementById("graphSvg");
  const box = svg.getBoundingClientRect();
  const w = box.width || 900, h = box.height || 620;
  const { nodes, links } = buildGraphData();
  graphState.nodes = nodes; graphState.links = links;

  document.getElementById("graphMeta").textContent = `${nodes.length} connected facts · ${links.length} relationships`;
  document.getElementById("nodePanel").innerHTML = "";

  if (!nodes.length) {
    svg.innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#98a2b3" font-size="14" font-family="Inter">No relationships to display for the selected filters.</text>`;
    return;
  }

  layoutInit(nodes, w, h);

  svg.innerHTML = `<g id="gRoot">
    <g id="gLinks" stroke-linecap="round"></g>
    <g id="gNodes"></g>
  </g>`;
  const gRoot = svg.querySelector("#gRoot");
  const gLinks = svg.querySelector("#gLinks");
  const gNodes = svg.querySelector("#gNodes");

  // Corroborations are the bulk of the edges and mostly confirm what you'd
  // expect; contradictions and reconciliations are the findings worth
  // looking at, so they're drawn heavier and brighter.
  const EDGE_STYLE = {
    corroborates: { w: 1.2, o: 0.28 },
    contradicts: { w: 2.4, o: 0.85 },
    reconciled: { w: 2.2, o: 0.8 },
  };
  const linkEls = links.map((l) => {
    const st = EDGE_STYLE[l.type] || { w: 1.5, o: 0.5 };
    const e = document.createElementNS("http://www.w3.org/2000/svg", "line");
    e.setAttribute("stroke", COLORS[l.type]);
    e.setAttribute("stroke-width", st.w);
    e.setAttribute("stroke-opacity", st.o);
    gLinks.appendChild(e);
    return e;
  });

  // Label the most-connected nodes so the graph is readable without
  // clicking -- hubs are where the interesting cross-checking happened.
  const labelCutoff = [...nodes].sort((a, b) => b.degree - a.degree)[Math.min(7, nodes.length - 1)]?.degree ?? 99;

  const nodeEls = nodes.map((n, i) => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.style.cursor = "pointer";
    const r = 5 + Math.min(9, Math.sqrt(n.degree) * 2.4);
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("r", r);
    c.setAttribute("fill", n.color);
    c.setAttribute("fill-opacity", ".9");
    c.setAttribute("stroke", "#fff");
    c.setAttribute("stroke-width", "1.6");
    g.appendChild(c);

    if (n.degree >= labelCutoff && n.degree > 1) {
      const txt = document.createElementNS("http://www.w3.org/2000/svg", "text");
      const raw = `${n.fact.subject || ""} · ${n.fact.attribute || ""}`.trim();
      txt.textContent = raw.length > 30 ? raw.slice(0, 29) + "…" : raw;
      txt.setAttribute("x", r + 5);
      txt.setAttribute("y", 4);
      txt.setAttribute("font-size", "10.5");
      txt.setAttribute("font-family", "Inter, sans-serif");
      txt.setAttribute("fill", "#475467");
      txt.setAttribute("paint-order", "stroke");
      txt.setAttribute("stroke", "#fff");
      txt.setAttribute("stroke-width", "3");
      txt.style.pointerEvents = "none";
      g.appendChild(txt);
    }
    g.addEventListener("click", (ev) => { ev.stopPropagation(); showNodePanel(n, links, nodes); });
    g.addEventListener("mouseenter", () => {
      c.setAttribute("stroke", "#101828");
      linkEls.forEach((le, li) => {
        const on = links[li].s === i || links[li].t === i;
        le.setAttribute("stroke-opacity", on ? ".95" : ".08");
        le.setAttribute("stroke-width", on ? "2.6" : "1.2");
      });
    });
    g.addEventListener("mouseleave", () => {
      c.setAttribute("stroke", "#fff");
      linkEls.forEach((le, li) => {
        const st = EDGE_STYLE[links[li].type] || { w: 1.5, o: 0.5 };
        le.setAttribute("stroke-opacity", st.o);
        le.setAttribute("stroke-width", st.w);
      });
    });
    gNodes.appendChild(g);
    return g;
  });

  function paint() {
    links.forEach((l, i) => {
      const a = nodes[l.s], b = nodes[l.t];
      linkEls[i].setAttribute("x1", a.x); linkEls[i].setAttribute("y1", a.y);
      linkEls[i].setAttribute("x2", b.x); linkEls[i].setAttribute("y2", b.y);
    });
    nodes.forEach((n, i) => nodeEls[i].setAttribute("transform", `translate(${n.x},${n.y})`));
  }

  cancelAnimationFrame(graphState.raf);
  let alpha = 1, iter = 0;
  (function tick() {
    for (let s = 0; s < 3; s++) { layoutStep(nodes, links, w, h, alpha); alpha *= 0.994; iter++; }
    paint();
    if (iter % 30 === 0) fitToView(nodes, svg, gRoot);   // keep it framed while it settles
    if (iter < 600 && alpha > 0.02) {
      graphState.raf = requestAnimationFrame(tick);
    } else {
      fitToView(nodes, svg, gRoot);
    }
  })();

  applyTransform(gRoot);
  setupPanZoom(svg, gRoot);
}

function applyTransform(gRoot) {
  const t = graphState.transform;
  gRoot.setAttribute("transform", `translate(${t.x},${t.y}) scale(${t.k})`);
}

function setupPanZoom(svg, gRoot) {
  if (svg._panzoom) return;
  svg._panzoom = true;
  let dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
  svg.addEventListener("mousedown", (e) => {
    dragging = true; svg.classList.add("grabbing");
    sx = e.clientX; sy = e.clientY; ox = graphState.transform.x; oy = graphState.transform.y;
  });
  window.addEventListener("mouseup", () => { dragging = false; svg.classList.remove("grabbing"); });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    graphState.transform.x = ox + (e.clientX - sx);
    graphState.transform.y = oy + (e.clientY - sy);
    applyTransform(svg.querySelector("#gRoot"));
  });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const t = graphState.transform;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const rect = svg.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    t.x = mx - (mx - t.x) * factor;
    t.y = my - (my - t.y) * factor;
    t.k = Math.max(0.25, Math.min(4, t.k * factor));
    applyTransform(svg.querySelector("#gRoot"));
  }, { passive: false });
  svg.addEventListener("click", () => { document.getElementById("nodePanel").innerHTML = ""; });
}

function showNodePanel(node, links, nodes) {
  const f = node.fact;
  const connected = links.filter((l) => nodes[l.s].id === node.id || nodes[l.t].id === node.id);
  const rows = connected.slice(0, 8).map((l) => {
    const other = nodes[l.s].id === node.id ? nodes[l.t] : nodes[l.s];
    return `<div class="np-rel">
      <span class="badge rel-${l.type}">${l.type}</span>
      <div style="margin-top:6px;color:var(--text-secondary);line-height:1.45">${escapeHtml(other.fact.statement.slice(0, 130))}${other.fact.statement.length > 130 ? "…" : ""}</div>
    </div>`;
  }).join("");
  document.getElementById("nodePanel").innerHTML = `
    <div class="node-panel">
      <button class="icon-btn np-close" onclick="document.getElementById('nodePanel').innerHTML=''">${ICONS.x}</button>
      <h4>${escapeHtml(f.subject || "Fact")} · ${escapeHtml(f.attribute || "")}</h4>
      <div class="np-src">${escapeHtml(shortName(f.document_name))} · page ${f.page_number} · ${node.degree} connection${node.degree === 1 ? "" : "s"}</div>
      <div style="line-height:1.5">${escapeHtml(f.statement)}</div>
      <div class="quote" style="margin-top:10px;font-size:11.5px">"${escapeHtml(f.quote.slice(0, 220))}${f.quote.length > 220 ? "…" : ""}"</div>
      ${rows}
    </div>`;
}

document.querySelectorAll("[data-gfilter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const t = btn.dataset.gfilter;
    if (graphState.filters.has(t)) { graphState.filters.delete(t); btn.classList.remove("on"); }
    else { graphState.filters.add(t); btn.classList.add("on"); }
    renderGraph();
  });
});
document.getElementById("graphReset").addEventListener("click", () => {
  graphState.transform = { x: 0, y: 0, k: 1 };
  renderGraph();
});

// ---------------- relationships ----------------

const REL_ICON = { corroborates: ICONS.check, contradicts: ICONS.x, reconciled: ICONS.link };

// When both sides carry a number, show the actual comparison -- for a
// contradiction the size of the gap is usually the whole point, and it
// saves the reader doing mental arithmetic across two quotes.
function valueDelta(a, b) {
  const x = a.value_numeric, y = b.value_numeric;
  if (x == null || y == null || !isFinite(x) || !isFinite(y)) return "";
  // Only compare like with like: an absolute figure in crore and a margin
  // in percent are both numbers, but their difference is meaningless and
  // rendering it would actively mislead. Requires both sides to declare
  // the same unit before any delta is shown.
  const ua = (a.unit || "").trim().toLowerCase();
  const ub = (b.unit || "").trim().toLowerCase();
  if (!ua || !ub || ua !== ub) return "";
  const fmt = (n) => Math.abs(n) >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(n);
  if (x === y) {
    return `<div class="value-compare"><span>${fmt(x)}</span><span class="small">=</span><span>${fmt(y)}</span>
      <span class="badge rel-corroborates" style="margin-left:auto">exact match</span></div>`;
  }
  const diff = Math.abs(x - y);
  const base = Math.max(Math.abs(x), Math.abs(y));
  const pct = base ? (diff / base) * 100 : 0;
  const unit = a.unit && a.unit === b.unit ? ` ${escapeHtml(a.unit)}` : "";
  return `<div class="value-compare">
    <span>${fmt(x)}</span><span class="small">vs</span><span>${fmt(y)}</span>
    <span class="badge ${pct > 1 ? "rel-contradicts" : "rel-reconciled"}" style="margin-left:auto">
      Δ ${fmt(diff)}${unit} · ${pct.toFixed(1)}%
    </span></div>`;
}

function relCard(r) {
  const box = (f) => `
    <div class="fact-box">
      <a class="src-pill" href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${ICONS.file}${escapeHtml(shortName(f.document_name))} · p.${f.page_number}</a>
      <div class="fb-statement">${escapeHtml(f.statement)}</div>
      <div class="quote" style="font-size:11.5px">"${escapeHtml(f.quote.slice(0, 200))}${f.quote.length > 200 ? "…" : ""}"</div>
    </div>`;
  return `
    <div class="rel-card">
      <div class="rel-head">
        <span class="badge rel-${r.relation_type}"><span style="display:flex">${REL_ICON[r.relation_type] || ""}</span>${r.relation_type}</span>
        <span class="small num">similarity ${Number(r.similarity_score).toFixed(2)}${r.confidence != null ? ` · confidence ${Number(r.confidence).toFixed(2)}` : ""}</span>
      </div>
      <div class="rel-body">
        <div class="rel-pair">
          ${box(r.fact_a)}
          <div class="rel-connector ${r.relation_type}">
            <div class="rc-line"></div>
            <div class="rc-icon">${REL_ICON[r.relation_type] || ""}</div>
            <div class="rc-line"></div>
          </div>
          ${box(r.fact_b)}
        </div>
        ${valueDelta(r.fact_a, r.fact_b)}
        <div class="explanation"><b>Reasoning:</b> ${escapeHtml(r.explanation)}</div>
        ${r.reconciliation_context ? `<div class="reconcile-context">${ICONS.link}<span><b>Reconciled by:</b> ${escapeHtml(r.reconciliation_context)}</span></div>` : ""}
      </div>
    </div>`;
}

function renderRelationships() {
  const type = document.getElementById("relTypeFilter").value;
  const rels = type ? store.rels.filter((r) => r.relation_type === type) : store.rels;
  document.getElementById("relCount").textContent = `${rels.length} relationship${rels.length === 1 ? "" : "s"}`;
  document.getElementById("relList").innerHTML = rels.length
    ? rels.map(relCard).join("")
    : emptyState("No relationships of this type yet.");
}
document.getElementById("relTypeFilter").addEventListener("change", renderRelationships);

// ---------------- facts ----------------

function loadFactFilters() {
  const sel = document.getElementById("factDocFilter");
  const current = sel.value;
  sel.innerHTML = `<option value="">All documents</option>` +
    store.docs.map((d) => `<option value="${d.id}">${escapeHtml(shortName(d.original_name))}</option>`).join("");
  sel.value = current;
}

function factCard(f) {
  const meta = [];
  if (f.time_period) meta.push(`<span>${escapeHtml(f.time_period)}</span>`);
  if (f.scope) meta.push(`<span>${escapeHtml(f.scope)}</span>`);
  if (f.unit) meta.push(`<span>${escapeHtml(f.unit)}</span>`);
  if (f.confidence != null) meta.push(`<span>confidence ${Number(f.confidence).toFixed(2)}</span>`);
  return `
    <div class="fact-card">
      <div class="fact-head">
        <a class="src-pill" href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${ICONS.file}${escapeHtml(shortName(f.document_name))} · p.${f.page_number}</a>
        ${f.quote_grounded
          ? `<span class="badge status-done"><span style="display:flex">${ICONS.shield}</span>quote verified</span>`
          : `<span class="badge status-processing"><span style="display:flex">${ICONS.alert}</span>quote unverified</span>`}
      </div>
      <div class="fact-statement">${escapeHtml(f.statement)}</div>
      <div class="triple">
        <span class="t-sub">${escapeHtml(f.subject || "?")}</span>
        <span class="t-arrow">${ICONS.arrowRight}</span>
        <span class="t-attr">${escapeHtml(f.attribute || "?")}</span>
        <span class="t-arrow">=</span>
        <span class="t-val">${escapeHtml(f.value || "?")}</span>
      </div>
      <div class="meta">${meta.join("")}</div>
      <div class="quote ${f.quote_grounded ? "" : "ungrounded"}">${f.quote_grounded ? "" : `<span class="warn-tag">Not found verbatim on page — </span>`}"${escapeHtml(f.quote)}"</div>
    </div>`;
}

function renderFacts() {
  const docId = document.getElementById("factDocFilter").value;
  const q = document.getElementById("factSearch").value.toLowerCase();
  let facts = store.facts;
  if (docId) facts = facts.filter((f) => String(f.document_id) === String(docId));
  if (q) facts = facts.filter((f) =>
    (f.statement || "").toLowerCase().includes(q) ||
    (f.subject || "").toLowerCase().includes(q) ||
    (f.attribute || "").toLowerCase().includes(q));
  document.getElementById("factCount").textContent = `${facts.length} fact${facts.length === 1 ? "" : "s"}`;
  document.getElementById("factsList").innerHTML = facts.length
    ? facts.slice(0, 300).map(factCard).join("")
    : emptyState("No facts match.");
}
document.getElementById("factDocFilter").addEventListener("change", renderFacts);
document.getElementById("factSearch").addEventListener("input", () => {
  clearTimeout(window._fst);
  window._fst = setTimeout(renderFacts, 200);
});

// ---------------- documents ----------------

let pollTimer = null;

function progressBar(progress) {
  if (!progress) {
    return `<div class="progress-track"><div class="progress-fill indeterminate"></div></div>
            <div class="progress-label"><span class="stage">Starting…</span></div>`;
  }
  const pct = progress.total ? Math.min(100, Math.round((progress.current / progress.total) * 100)) : 0;
  const label = { extracting: "Extracting facts", embedding: "Generating embeddings", comparing: "Comparing facts" }[progress.stage] || progress.stage;
  const indet = progress.total <= 1;
  return `
    <div class="progress-track"><div class="progress-fill${indet ? " indeterminate" : ""}" style="width:${indet ? 35 : pct}%"></div></div>
    <div class="progress-label">
      <span class="stage">${escapeHtml(label)}${progress.detail ? " · " + escapeHtml(progress.detail) : ""}</span>
      <span class="num">${progress.total > 1 ? `${progress.current}/${progress.total}` : ""}</span>
    </div>`;
}

async function loadDocuments() {
  const docs = await fetch(`${API}/api/documents`).then((r) => r.json());
  store.docs = docs;
  renderHeaderMeta();
  const body = document.getElementById("documentsBody");
  body.innerHTML = "";
  let inFlight = false;

  for (const d of docs) {
    if (d.status === "pending" || d.status === "processing") inFlight = true;
    const statusCell = d.status === "processing"
      ? `<div class="progress-cell">${statusBadge(d.status)}<div style="margin-top:7px">${progressBar(d.progress)}</div></div>`
      : statusBadge(d.status);
    body.appendChild(el(`
      <tr>
        <td class="doc-name">${escapeHtml(shortName(d.original_name))}</td>
        <td>${statusCell}</td>
        <td class="num">${d.num_pages ?? "—"}</td>
        <td class="num">${d.fact_count}</td>
        <td class="small">${new Date(d.uploaded_at * 1000).toLocaleString()}</td>
        <td><div class="row-actions">
          <a href="${pdfLink(d.id, 1)}" target="_blank"><button class="icon-btn" title="Open PDF">${ICONS.externalLink}</button></a>
          ${d.status === "done" ? `<button class="icon-btn perf-btn" data-doc="${d.id}" title="Performance">${ICONS.barChart}</button>` : ""}
        </div></td>
      </tr>`));
    if (d.status === "failed" && d.error_message) {
      body.appendChild(el(`<tr class="notice-row error"><td colspan="6">${escapeHtml(d.error_message.split("\n")[0])}</td></tr>`));
    }
    if (d.reused_from_document_id) {
      body.appendChild(el(`<tr class="notice-row"><td colspan="6">Identical to document #${d.reused_from_document_id} — results reused, no LLM calls made.</td></tr>`));
    }
  }
  if (!docs.length) body.appendChild(el(`<tr><td colspan="6">${emptyState("No documents yet — upload one to get started.")}</td></tr>`));

  body.querySelectorAll(".perf-btn").forEach((b) => b.addEventListener("click", () => togglePerf(b)));
  clearTimeout(pollTimer);
  if (inFlight) pollTimer = setTimeout(loadDocuments, 2000);
}

async function togglePerf(btn) {
  const next = btn.closest("tr").nextElementSibling;
  if (next && next.classList.contains("perf-row")) { next.remove(); return; }
  document.querySelectorAll(".perf-row").forEach((r) => r.remove());
  const doc = await fetch(`${API}/api/documents/${btn.dataset.doc}`).then((r) => r.json());
  const s = doc.stats;
  if (!s) {
    btn.closest("tr").insertAdjacentElement("afterend",
      el(`<tr class="perf-row"><td colspan="6" class="small">No performance stats recorded for this document.</td></tr>`));
    return;
  }
  const timings = [
    ["PDF extraction", s.timing.pdf_extraction],
    ["Fact extraction (LLM)", s.timing.fact_extraction],
    ["Embeddings", s.timing.embeddings],
    ["Candidate retrieval", s.timing.candidate_retrieval],
    ["Relationship reasoning (LLM)", s.timing.relationship_reasoning],
    ["Database", s.timing.database],
  ];
  const maxT = Math.max(...timings.map((t) => t[1]), 0.001);
  btn.closest("tr").insertAdjacentElement("afterend", el(`<tr class="perf-row"><td colspan="6">
    <div class="perf-panel">
      <div class="perf-grid">
        <div class="perf-stat"><div class="n">${s.pages}</div><div class="l">Pages</div></div>
        <div class="perf-stat"><div class="n">${s.chunks}</div><div class="l">Chunks</div></div>
        <div class="perf-stat"><div class="n">${s.facts}</div><div class="l">Facts</div></div>
        <div class="perf-stat"><div class="n">${s.candidate_pairs}</div><div class="l">Candidates</div></div>
        <div class="perf-stat"><div class="n">${s.relationships_stored}</div><div class="l">Relationships</div></div>
        <div class="perf-stat"><div class="n">${s.ollama_calls}</div><div class="l">LLM calls</div></div>
        <div class="perf-stat"><div class="n">${s.extraction_cache_hits + s.reasoning_cache_hits}</div><div class="l">Cache hits</div></div>
        <div class="perf-stat"><div class="n">${fmtSecs(s.timing.total)}</div><div class="l">Total</div></div>
      </div>
      ${timings.map(([l, v]) => `
        <div class="bar-row">
          <div class="bar-label">${escapeHtml(l)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.max(1.5, (v / maxT) * 100)}%"></div></div>
          <div class="bar-val num">${fmtSecs(v)}</div>
        </div>`).join("")}
    </div></td></tr>`));
}

// ---------------- issues ----------------

async function loadIssues() {
  const issues = await fetch(`${API}/api/issues`).then((r) => r.json());
  const body = document.getElementById("issuesBody");
  body.innerHTML = issues.length
    ? issues.map((i) => `
        <tr>
          <td class="small num">${i.document_id ?? "—"}</td>
          <td class="small num">${i.page_number ?? "—"}</td>
          <td><span class="badge status-failed"><span style="display:flex">${ICONS.alert}</span>${escapeHtml(i.issue_type)}</span></td>
          <td class="small">${escapeHtml(i.detail)}${i.raw_excerpt ? `<div class="quote" style="margin-top:6px">${escapeHtml(i.raw_excerpt.slice(0, 240))}</div>` : ""}</td>
        </tr>`).join("")
    : `<tr><td colspan="4">${emptyState("No issues logged yet.")}</td></tr>`;
}

// ---------------- upload ----------------

const pdfInput = document.getElementById("pdfInput");
const dropzone = document.getElementById("dropzone");
const fileChip = document.getElementById("fileChip");

dropzone.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", renderFileChip);
["dragenter", "dragover"].forEach((e) => dropzone.addEventListener(e, (ev) => { ev.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((e) => dropzone.addEventListener(e, (ev) => { ev.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (ev) => {
  const f = ev.dataTransfer.files[0];
  if (f) { const dt = new DataTransfer(); dt.items.add(f); pdfInput.files = dt.files; renderFileChip(); }
});
function renderFileChip() {
  const f = pdfInput.files[0];
  fileChip.innerHTML = f ? `<div class="dz-file">${ICONS.file}${escapeHtml(f.name)} · ${(f.size / 1048576).toFixed(1)} MB</div>` : "";
}

document.getElementById("uploadBtn").addEventListener("click", async () => {
  const statusEl = document.getElementById("uploadStatus");
  const btn = document.getElementById("uploadBtn");
  statusEl.className = "small";
  if (!pdfInput.files.length) { statusEl.textContent = "Choose a PDF first."; statusEl.className = "small error"; return; }
  const fd = new FormData();
  fd.append("file", pdfInput.files[0]);
  const params = new URLSearchParams();
  const pagesSpec = document.getElementById("pagesSpec").value;
  const maxPages = document.getElementById("maxPages").value;
  if (pagesSpec) params.set("pages", pagesSpec);
  else if (maxPages) params.set("max_pages", maxPages);
  let url = `${API}/api/documents`;
  if ([...params].length) url += `?${params}`;

  btn.disabled = true; statusEl.textContent = "Uploading…";
  const resp = await fetch(url, { method: "POST", body: fd });
  btn.disabled = false;
  if (!resp.ok) { statusEl.textContent = `Upload failed: ${await resp.text()}`; statusEl.className = "small error"; return; }
  const doc = await resp.json();
  statusEl.className = "small success";
  statusEl.textContent = doc.reused_document_id
    ? `Identical to document #${doc.reused_document_id} — reused instantly, no processing needed.`
    : `Queued as document #${doc.id}.`;
  pdfInput.value = ""; fileChip.innerHTML = "";
  document.querySelector('nav button[data-view="documents"]').click();
});

// ---------------- init ----------------

(async function init() {
  await loadAll();
  const initial = location.hash.slice(1);
  await activateView(views.includes(initial) ? initial : "overview");
  loadDocuments();
})();
