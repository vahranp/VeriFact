const API = "";

const C = { corroborates: "#059669", contradicts: "#dc2626", reconciled: "#d97706", uncertain: "#64748b", brand: "#4f46e5" };
const DOC_COLORS = ["#4f46e5", "#0284c7", "#059669", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#65a30d", "#e11d48", "#0d9488"];

const I = {
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  loader: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.9" y1="4.9" x2="7.8" y2="7.8"/><line x1="16.2" y1="16.2" x2="19.1" y2="19.1"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/><line x1="4.9" y1="19.1" x2="7.8" y2="16.2"/><line x1="16.2" y1="7.8" x2="19.1" y2="4.9"/></svg>`,
  file: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  layers: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
  link: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  shield: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  arrow: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`,
  ext: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
  inbox: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
  zap: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
  timer: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M9 2h6"/></svg>`,
  stop: `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>`,
};

// ---------------- utils ----------------

const $ = (id) => document.getElementById(id);
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; }
function esc(s) { return s == null ? "" : String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function docName(n) { return String(n || "").replace(/^[0-9a-f]{32}_/, "").replace(/\.pdf$/i, ""); }

// Several uploads are the same PDF ingested with different page selections,
// so the filename alone makes them indistinguishable in a list. The page
// scope is what actually tells them apart.
function docScope(d) {
  if (!d) return "";
  const sel = d.page_selector;
  if (!sel) return "full document";
  if (sel.startsWith("max:")) return `first ${sel.slice(4)} pages`;
  return `page${/[,\-]/.test(sel) ? "s" : ""} ${sel}`;
}
function docLabel(d) { return d ? `${docName(d.original_name)} · ${docScope(d)}` : ""; }
function docById(id) { return S.docs.find((x) => String(x.id) === String(id)); }
function pdfLink(id, page) { return `${API}/api/documents/${id}/pdf#page=${page || 1}`; }
function fmtSec(s) { if (s == null) return "—"; return s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${s.toFixed(1)}s`; }
function fmtNum(n) { return Math.abs(n) >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(n); }
function empty(t) { return `<div class="empty">${I.inbox}<span>${esc(t)}</span></div>`; }

// animated number count-up
function countUp(node, target, dur = 900) {
  const start = performance.now();
  const isInt = Number.isInteger(target);
  (function step(now) {
    const p = Math.min(1, (now - start) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    const v = target * e;
    node.textContent = isInt ? Math.round(v).toLocaleString() : v.toFixed(1);
    if (p < 1) requestAnimationFrame(step);
  })(start);
}
function animateIn(root) {
  root.querySelectorAll("[data-count]").forEach((n) => countUp(n, parseFloat(n.dataset.count)));
  requestAnimationFrame(() => {
    root.querySelectorAll(".bar-f").forEach((b, i) => setTimeout(() => { b.style.width = b.dataset.w + "%"; }, i * 45));
    root.querySelectorAll(".col-bar").forEach((b, i) => setTimeout(() => { b.style.height = b.dataset.h + "%"; }, i * 22));
  });
  root.querySelectorAll(".rise").forEach((n, i) => { n.style.animationDelay = Math.min(i * 55, 400) + "ms"; });
}

const STATUS = { done: { i: I.check, l: "Done" }, processing: { i: I.loader, l: "Processing" }, pending: { i: I.clock, l: "Queued" }, failed: { i: I.x, l: "Failed" }, cancelled: { i: I.stop, l: "Stopped" } };
function badge(status) {
  const m = STATUS[status] || { i: "", l: status };
  const sp = status === "processing" ? "animation:spin 1.4s linear infinite;" : "";
  return `<span class="badge b-${status}"><span style="display:flex;${sp}">${m.i}</span>${m.l}</span>`;
}
if (!$("kf")) { const s = document.createElement("style"); s.id = "kf"; s.textContent = "@keyframes spin{to{transform:rotate(360deg)}}"; document.head.appendChild(s); }

// Cancellation is cooperative on the server (see app/db.py request_cancel):
// this only sets a flag, so the button immediately shows "Stopping…" while
// the pipeline notices between the current chunk/candidate pair and the
// next -- it doesn't claim the job has already stopped.
const stopping = new Set();
async function stopDocument(id, onDone) {
  if (stopping.has(id)) return;
  stopping.add(id);
  try {
    const res = await fetch(`${API}/api/documents/${id}/cancel`, { method: "POST" });
    if (!res.ok && res.status !== 409) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || `Could not stop document ${id}.`);
      stopping.delete(id);
      return;
    }
  } catch (e) {
    alert(`Could not reach the server to stop document ${id}.`);
    stopping.delete(id);
    return;
  }
  if (onDone) onDone();
}

function stopButton(id, { small } = {}) {
  const pending = stopping.has(id);
  const cls = small ? "btn-icon stop-icon" : "btn-danger-outline";
  return `<button class="${cls}" title="Stop processing" ${pending ? "disabled" : ""}
    onclick="event.stopPropagation(); stopDocument(${id}, () => { renderDocTable(); if (S.currentDoc && S.currentDoc.id === ${id}) renderDocument(${id}); })">
    ${small ? I.stop : `${I.stop}<span>${pending ? "Stopping…" : "Stop"}</span>`}
  </button>`;
}

// ---------------- charts ----------------

function donut(segs, size = 168) {
  const total = segs.reduce((a, s) => a + s.value, 0);
  const r = size / 2 - 17, cx = size / 2, cy = size / 2, CIRC = 2 * Math.PI * r;
  let off = 0;
  const arcs = segs.filter((s) => s.value > 0).map((s, i) => {
    const frac = total ? s.value / total : 0;
    const a = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}" stroke-width="17"
      stroke-dasharray="0 ${CIRC}" stroke-dashoffset="${-off * CIRC}" transform="rotate(-90 ${cx} ${cy})"
      style="transition:stroke-dasharray .95s cubic-bezier(.22,.8,.3,1) ${i * 130}ms">
      <title>${esc(s.label)}: ${s.value}</title></circle>`;
    off += frac;
    return { html: a, dash: `${frac * CIRC} ${CIRC - frac * CIRC}` };
  });
  const id = "dn" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => {
    const g = $(id); if (!g) return;
    g.querySelectorAll("circle[data-arc]").forEach((c, i) => c.setAttribute("stroke-dasharray", arcs[i].dash));
  }, 30);
  return `<svg id="${id}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#eef1f6" stroke-width="17"/>
    ${arcs.map((a) => a.html.replace("<circle", "<circle data-arc")).join("")}
    <text x="${cx}" y="${cy - 1}" text-anchor="middle" font-size="25" font-weight="600" fill="#0f172a" style="font-variant-numeric:tabular-nums">${total}</text>
    <text x="${cx}" y="${cy + 18}" text-anchor="middle" font-size="11" fill="#94a3b8">total</text></svg>`;
}

function ring(pct, size = 172, label = "verified") {
  const r = size / 2 - 15, cx = size / 2, cy = size / 2, CIRC = 2 * Math.PI * r;
  const col = pct >= 90 ? "#059669" : pct >= 70 ? "#d97706" : "#dc2626";
  const id = "rg" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => { const c = $(id); if (c) c.setAttribute("stroke-dasharray", `${(pct / 100) * CIRC} ${CIRC}`); }, 40);
  return `<div style="text-align:center"><svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#eef1f6" stroke-width="14"/>
    <circle id="${id}" cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${col}" stroke-width="14" stroke-linecap="round"
      stroke-dasharray="0 ${CIRC}" transform="rotate(-90 ${cx} ${cy})" style="transition:stroke-dasharray 1.05s cubic-bezier(.22,.8,.3,1)"/>
    <text x="${cx}" y="${cy + 2}" text-anchor="middle" font-size="27" font-weight="600" fill="#0f172a" style="font-variant-numeric:tabular-nums">${pct}%</text>
    <text x="${cx}" y="${cy + 21}" text-anchor="middle" font-size="11" fill="#94a3b8">${esc(label)}</text></svg></div>`;
}

function barList(rows, opts = {}) {
  if (!rows.length) return empty(opts.emptyText || "Nothing to show.");
  const max = Math.max(...rows.map((r) => r.value), 1);
  return `<div class="bars">${rows.map((r, i) => `
    <div class="bar">
      <div class="bar-l" title="${esc(r.label)}">${esc(r.label)}</div>
      <div class="bar-t"><div class="bar-f" data-w="${(r.value / max) * 100}" style="background:${r.color || "var(--brand)"}"></div></div>
      <div class="bar-v">${esc(r.display != null ? r.display : r.value)}</div>
    </div>`).join("")}</div>`;
}

function columns(rows) {
  if (!rows.length) return empty("No page data.");
  const max = Math.max(...rows.map((r) => r.value), 1);
  const showEvery = Math.ceil(rows.length / 24);
  return `<div class="cols">${rows.map((r, i) => `
    <div class="col" title="Page ${esc(r.label)}: ${r.value} facts">
      <div class="col-bar" data-h="${(r.value / max) * 100}"></div>
      <div class="col-l">${i % showEvery === 0 ? esc(r.label) : ""}</div>
    </div>`).join("")}</div>`;
}

// ---------------- data ----------------

const S = { docs: [], facts: [], rels: [], stats: null, currentDoc: null };

async function loadAll() {
  const [stats, docs, facts, rels] = await Promise.all([
    fetch(`${API}/api/stats`).then((r) => r.json()),
    fetch(`${API}/api/documents`).then((r) => r.json()),
    fetch(`${API}/api/facts`).then((r) => r.json()),
    fetch(`${API}/api/relationships`).then((r) => r.json()),
  ]);
  S.stats = stats; S.docs = docs; S.facts = facts; S.rels = rels;
  paintChrome();
}

function docColor(id) {
  const ids = [...new Set(S.docs.map((d) => d.id))].sort((a, b) => a - b);
  return DOC_COLORS[ids.indexOf(id) % DOC_COLORS.length];
}

function paintChrome() {
  const s = S.stats;
  $("cRel").textContent = s.relationships;
  $("cFacts").textContent = s.facts;
  $("cIssues").textContent = s.issues;
  const busy = S.docs.filter((d) => d.status === "processing" || d.status === "pending");
  $("pulse").innerHTML = busy.length
    ? `<span class="pulse-dot busy"></span>Processing ${busy.length} document${busy.length > 1 ? "s" : ""}`
    : `<span class="pulse-dot"></span>All documents processed`;
  $("sbFoot").innerHTML = `${s.documents_done}/${s.documents} documents · ${s.facts} facts`;

  $("sbDocs").innerHTML = S.docs.length ? S.docs.map((d) => `
    <button class="sb-item sb-doc" data-doc="${d.id}" title="${esc(docLabel(d))}">
      <span class="sb-dot ${d.status}"></span>
      <span style="min-width:0;flex:1">
        <span class="sb-doc-name">${esc(docName(d.original_name))}</span>
        <span class="sb-doc-scope">${esc(docScope(d))}</span>
      </span>
      <span class="sb-count">${d.fact_count}</span>
    </button>`).join("") : `<div class="small" style="padding:6px 10px;color:#55637a">No documents yet</div>`;

  $("sbDocs").querySelectorAll("[data-doc]").forEach((b) =>
    b.addEventListener("click", () => go(`document/${b.dataset.doc}`)));

  const opts = `<option value="">All documents</option>` + S.docs.map((d) => `<option value="${d.id}">${esc(docLabel(d))}</option>`).join("");
  ["factDoc", "relDoc", "graphDocFilter"].forEach((id) => {
    const sel = $(id); if (!sel) return;
    const cur = sel.value; sel.innerHTML = opts; sel.value = cur;
  });
}

// ---------------- routing ----------------

const VIEWS = ["overview", "document", "graph", "relationships", "facts", "coherence", "documents", "issues", "upload"];
const TITLES = { overview: "Overview", graph: "Knowledge Graph", relationships: "Relationships", facts: "Facts", coherence: "Logic Check", documents: "All documents", issues: "Extraction Issues", upload: "Upload document" };

function go(route) { if (location.hash.slice(1) !== route) location.hash = route; else route_(route); }

async function route_(route) {
  const [head, arg] = (route || "overview").split("/");
  const view = VIEWS.includes(head) ? head : "overview";
  if (view !== "document") clearTimeout(docPoll);
  VIEWS.forEach((v) => $(`view-${v}`).classList.toggle("active", v === view));
  document.querySelectorAll(".sb-item[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".sb-item[data-doc]").forEach((b) => b.classList.toggle("active", view === "document" && b.dataset.doc === arg));

  if (view !== "document") { $("pageTitle").textContent = TITLES[view] || "Overview"; $("pageSub").textContent = ""; }

  await loadAll();
  if (view === "overview") renderOverview();
  if (view === "document") renderDocument(arg);
  if (view === "graph") renderGraph();
  if (view === "relationships") renderRels();
  if (view === "facts") renderFacts();
  if (view === "documents") renderDocTable();
  if (view === "issues") renderIssues();
  if (view === "coherence") renderCoherence();
}

document.querySelectorAll(".sb-item[data-view]").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
window.addEventListener("hashchange", () => route_(location.hash.slice(1)));

// ---------------- overview ----------------

function statTile(label, value, foot, icon, tone, suffix) {
  return `<div class="stat ${tone || ""} rise">
    <div class="stat-h"><span class="stat-label">${label}</span><span class="stat-ico">${icon}</span></div>
    <div class="stat-val num"><span data-count="${value}">0</span>${suffix || ""}</div>
    <div class="stat-foot">${foot}</div></div>`;
}

function renderOverview() {
  const s = S.stats, rt = s.relationships_by_type || {};
  const grounded = s.facts - s.ungrounded_facts;
  const pct = s.facts ? Math.round((grounded / s.facts) * 100) : 0;

  $("ovStats").innerHTML = [
    statTile("Documents", s.documents, `${s.documents_done} fully processed`, I.file),
    statTile("Facts extracted", s.facts, `${grounded} with verified quotes`, I.layers),
    statTile("Relationships", s.relationships, `${rt.contradicts || 0} contradictions surfaced`, I.link, "pos"),
    statTile("Flagged issues", s.issues, "surfaced, not hidden", I.alert, "warn"),
  ].join("");

  const withStats = S.docs.filter((d) => d.stats);
  const sum = (k) => withStats.reduce((a, d) => a + (d.stats[k] || 0), 0);
  const active = (S.docs.find((d) => d.status === "processing") || {}).progress?.stage;
  const stages = [
    ["Pages read", sum("pages"), null], ["Chunks", sum("chunks"), null],
    ["Facts", s.facts, "extracting"], ["Candidate pairs", sum("candidate_pairs"), "comparing"],
    ["Relationships", s.relationships, null],
  ];
  $("ovFlow").innerHTML = stages.map(([l, v, k], i) => `
    ${i ? `<div class="farrow">${I.arrow}</div>` : ""}
    <div class="fnode ${active && k === active ? "on" : ""}"><div class="fv num" data-count="${v}">0</div><div class="fl">${l}</div></div>`).join("");
  $("flowLive").innerHTML = active ? `<span class="badge b-processing">live</span>` : "";

  const segs = [
    { label: "Corroborates", value: rt.corroborates || 0, color: C.corroborates },
    { label: "Contradicts", value: rt.contradicts || 0, color: C.contradicts },
    { label: "Reconciled", value: rt.reconciled || 0, color: C.reconciled },
  ];
  const tot = segs.reduce((a, x) => a + x.value, 0) || 1;
  $("ovDonut").innerHTML = donut(segs);
  $("ovLegend").innerHTML = segs.map((x) => `<div class="lg">
    <span class="lg-dot" style="background:${x.color}"></span><span class="lg-name">${x.label}</span>
    <span class="lg-val num">${x.value}</span><span class="lg-pct">${((x.value / tot) * 100).toFixed(0)}%</span></div>`).join("");

  $("ovRing").innerHTML = ring(pct) + `<div class="small" style="margin-top:10px;max-width:260px">${grounded} of ${s.facts} facts had their quote located verbatim on the source page</div>`;

  const byDoc = {};
  S.facts.forEach((f) => { byDoc[f.document_id] = (byDoc[f.document_id] || 0) + 1; });
  const rows = Object.entries(byDoc).map(([id, n]) => {
    const d = docById(id);
    return { label: d ? docLabel(d) : `Document ${id}`, value: n, color: docColor(Number(id)) };
  }).sort((a, b) => b.value - a.value);
  $("ovByDoc").outerHTML = `<div class="bars" id="ovByDoc">${barList(rows).replace(/^<div class="bars">|<\/div>$/g, "")}</div>`;

  animateIn($("view-overview"));
}

// ---------------- document detail ----------------

let docPoll = null;

async function renderDocument(id) {
  const host = $("docDetail");
  host.innerHTML = `<div class="grid g4"><div class="skel" style="height:104px"></div><div class="skel" style="height:104px"></div><div class="skel" style="height:104px"></div><div class="skel" style="height:104px"></div></div>`;
  const d = await fetch(`${API}/api/documents/${id}`).then((r) => r.json());
  S.currentDoc = d;

  $("pageTitle").textContent = docName(d.original_name);
  $("pageSub").textContent = `${docScope(d)} · ${d.num_pages ?? "—"} pages in file · uploaded ${new Date(d.uploaded_at * 1000).toLocaleDateString()}`;

  const facts = d.facts || [], issues = d.issues || [];
  const grounded = facts.filter((f) => f.quote_grounded).length;
  const pct = facts.length ? Math.round((grounded / facts.length) * 100) : 0;
  const st = d.stats;

  const factIds = new Set(facts.map((f) => f.id));
  const rels = S.rels.filter((r) => factIds.has(r.fact_id_a) || factIds.has(r.fact_id_b));
  const relByType = { corroborates: 0, contradicts: 0, reconciled: 0 };
  rels.forEach((r) => { relByType[r.relation_type] = (relByType[r.relation_type] || 0) + 1; });
  const crossDoc = rels.filter((r) => r.fact_a.document_id !== r.fact_b.document_id).length;

  // facts per page
  const perPage = {};
  facts.forEach((f) => { perPage[f.page_number] = (perPage[f.page_number] || 0) + 1; });
  const pageRows = Object.entries(perPage).map(([p, n]) => ({ label: p, value: n })).sort((a, b) => a.label - b.label);

  // top subjects & attributes
  const tally = (key) => {
    const m = {};
    facts.forEach((f) => { const k = (f[key] || "").trim(); if (k) m[k] = (m[k] || 0) + 1; });
    return Object.entries(m).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value).slice(0, 7);
  };

  const timingRows = st ? [
    ["PDF extraction", st.timing.pdf_extraction], ["Fact extraction (LLM)", st.timing.fact_extraction],
    ["Embeddings", st.timing.embeddings], ["Candidate retrieval", st.timing.candidate_retrieval],
    ["Relationship reasoning (LLM)", st.timing.relationship_reasoning], ["Database", st.timing.database],
  ].map(([label, v]) => ({ label, value: v, display: fmtSec(v) })) : [];

  const issueTally = {};
  issues.forEach((i) => { issueTally[i.issue_type] = (issueTally[i.issue_type] || 0) + 1; });

  host.innerHTML = `
    <div class="grid g4 mb24">
      ${statTile("Facts extracted", facts.length, `across ${pageRows.length} page${pageRows.length === 1 ? "" : "s"}`, I.layers)}
      ${statTile("Evidence verified", pct, `${grounded} of ${facts.length} quotes located`, I.shield, pct >= 90 ? "pos" : "warn", "%")}
      ${statTile("Relationships", rels.length, `${crossDoc} cross-document`, I.link, "pos")}
      ${statTile("Issues flagged", issues.length, Object.keys(issueTally).length + " distinct type(s)", I.alert, issues.length ? "warn" : "")}
    </div>

    ${d.reused_from_document_id ? `<div class="card card-b mb16 small">This upload was byte-identical to document #${d.reused_from_document_id} with the same page selection — its results were reused and no LLM calls were made.</div>` : ""}
    ${d.status === "failed" ? `<div class="card card-b mb16" style="border-color:var(--neg-border);background:var(--neg-soft);color:var(--neg);font-size:12.5px">${esc((d.error_message || "").split("\n")[0])}</div>` : ""}
    ${d.status === "cancelled" ? `<div class="card card-b mb16" style="border-color:var(--warn-border);background:var(--warn-soft);color:var(--warn);font-size:12.5px">${esc((d.error_message || "Stopped by user.").split("\n")[0])}</div>` : ""}
    ${(d.status === "pending" || d.status === "processing") ? `
      <div class="card card-b mb16" style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <div style="flex:1;min-width:220px">${badge(d.status)}${d.cancel_requested ? `<span class="small" style="color:var(--warn);margin-left:7px">stopping…</span>` : ""}
          <div style="margin-top:10px">${progress(d.progress)}</div></div>
        ${stopButton(d.id)}
      </div>` : ""}

    <div class="grid g2 mb24">
      <div class="card rise">
        <div class="card-h"><div><h3>Facts by page</h3><div class="sub">Where in the document the content is concentrated</div></div>
          <span class="badge b-brand">${facts.length} facts</span></div>
        <div class="card-b">${columns(pageRows)}</div>
      </div>
      <div class="card rise">
        <div class="card-h"><div><h3>Evidence quality</h3><div class="sub">Quotes verified against the page</div></div></div>
        <div class="card-b" style="display:flex;justify-content:center">${ring(pct, 156)}</div>
      </div>
    </div>

    <div class="grid g2e mb24">
      <div class="card rise">
        <div class="card-h"><div><h3>Relationship outcomes</h3><div class="sub">Judgements involving this document's facts</div></div></div>
        <div class="card-b" style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">
          ${donut([
            { label: "Corroborates", value: relByType.corroborates || 0, color: C.corroborates },
            { label: "Contradicts", value: relByType.contradicts || 0, color: C.contradicts },
            { label: "Reconciled", value: relByType.reconciled || 0, color: C.reconciled },
          ], 148)}
          <div class="legend" style="flex:1">
            ${["corroborates", "contradicts", "reconciled"].map((k) => `<div class="lg">
              <span class="lg-dot" style="background:${C[k]}"></span><span class="lg-name">${k[0].toUpperCase() + k.slice(1)}</span>
              <span class="lg-val num">${relByType[k] || 0}</span></div>`).join("")}
          </div>
        </div>
      </div>
      <div class="card rise">
        <div class="card-h"><div><h3>Most-described subjects</h3><div class="sub">What this document is mostly about</div></div></div>
        <div class="card-b">${barList(tally("subject"), { emptyText: "No subjects extracted." })}</div>
      </div>
    </div>

    ${st ? `
    <div class="grid g2e mb24">
      <div class="card rise">
        <div class="card-h"><div><h3>Where processing time went</h3><div class="sub">Total ${fmtSec(st.timing.total)}</div></div></div>
        <div class="card-b">${barList(timingRows)}</div>
      </div>
      <div class="card rise">
        <div class="card-h"><div><h3>Compute profile</h3><div class="sub">LLM calls actually made vs. avoided by cache</div></div></div>
        <div class="card-b">
          <div class="grid g3" style="gap:12px;margin-bottom:16px">
            <div class="stat" style="padding:13px 15px"><div class="stat-h"><span class="stat-label">LLM calls</span><span class="stat-ico">${I.zap}</span></div><div class="stat-val num" style="font-size:22px" data-count="${st.ollama_calls}">0</div></div>
            <div class="stat pos" style="padding:13px 15px"><div class="stat-h"><span class="stat-label">Cache hits</span><span class="stat-ico">${I.check}</span></div><div class="stat-val num" style="font-size:22px" data-count="${st.extraction_cache_hits + st.reasoning_cache_hits}">0</div></div>
            <div class="stat" style="padding:13px 15px"><div class="stat-h"><span class="stat-label">Chunks</span><span class="stat-ico">${I.layers}</span></div><div class="stat-val num" style="font-size:22px" data-count="${st.chunks}">0</div></div>
          </div>
          ${barList([
            { label: "Extraction calls", value: st.extraction_calls, display: st.extraction_calls },
            { label: "Reasoning calls", value: st.reasoning_calls, display: st.reasoning_calls },
            { label: "Candidate pairs shortlisted", value: st.candidate_pairs, display: st.candidate_pairs, color: "var(--info)" },
          ])}
        </div>
      </div>
    </div>` : ""}

    ${Object.keys(issueTally).length ? `
    <div class="card rise mb24">
      <div class="card-h"><div><h3>Flagged during extraction</h3><div class="sub">The pipeline's own record of where it wasn't certain</div></div></div>
      <div class="card-b">${barList(Object.entries(issueTally).map(([label, value]) => ({ label, value, color: "var(--warn)" })))}</div>
    </div>` : ""}

    <div class="card rise">
      <div class="card-h">
        <div><h3>Extracted facts</h3><div class="sub">Every fact from this document, with its source evidence</div></div>
        <a class="pill" href="${pdfLink(d.id, 1)}" target="_blank">${I.ext} Open PDF</a>
      </div>
      <div class="card-b">${facts.length ? facts.slice(0, 60).map(factCard).join("") + (facts.length > 60 ? `<div class="small" style="text-align:center;padding-top:8px">Showing 60 of ${facts.length} — see the Facts tab for all</div>` : "") : empty("No facts were extracted from this document.")}</div>
    </div>`;

  animateIn(host);

  clearTimeout(docPoll);
  if (d.status === "pending" || d.status === "processing") {
    docPoll = setTimeout(() => { if (S.currentDoc && S.currentDoc.id === d.id) renderDocument(id); }, 2000);
  }
}

// ---------------- knowledge graph ----------------

let G = { nodes: [], links: [], t: { x: 0, y: 0, k: 1 }, raf: null, filters: new Set(["corroborates", "contradicts", "reconciled"]) };
const SIM = { charge: -420, cutoff: 420, dist: 68, k: 0.05, center: 0.013, damp: 0.87 };

function graphData() {
  const docFilter = $("graphDocFilter").value;
  let rels = S.rels.filter((r) => G.filters.has(r.relation_type));
  if (docFilter) rels = rels.filter((r) => String(r.fact_a.document_id) === docFilter || String(r.fact_b.document_id) === docFilter);
  const byId = {}; S.facts.forEach((f) => { byId[f.id] = f; });
  const ids = new Set(); rels.forEach((r) => { ids.add(r.fact_id_a); ids.add(r.fact_id_b); });
  const nodes = [...ids].filter((i) => byId[i]).map((i) => ({ id: i, fact: byId[i], degree: 0, color: docColor(byId[i].document_id), x: 0, y: 0, vx: 0, vy: 0 }));
  const idx = {}; nodes.forEach((n, i) => { idx[n.id] = i; });
  const links = rels.filter((r) => idx[r.fact_id_a] !== undefined && idx[r.fact_id_b] !== undefined).map((r) => {
    const s = idx[r.fact_id_a], t = idx[r.fact_id_b];
    nodes[s].degree++; nodes[t].degree++;
    return { s, t, type: r.relation_type, rel: r };
  });
  return { nodes, links };
}

function simStep(nodes, links, w, h, alpha) {
  for (const n of nodes) { n.ax = 0; n.ay = 0; }
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
      if (d2 > SIM.cutoff * SIM.cutoff) continue;
      if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1; }
      const d = Math.sqrt(d2), f = SIM.charge / d2, ux = dx / d, uy = dy / d;
      a.ax -= ux * f; a.ay -= uy * f; b.ax += ux * f; b.ay += uy * f;
    }
  }
  for (const l of links) {
    const a = nodes[l.s], b = nodes[l.t];
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.max(.01, Math.sqrt(dx * dx + dy * dy));
    const f = (d - SIM.dist) * SIM.k, ux = dx / d, uy = dy / d;
    a.ax += ux * f; a.ay += uy * f; b.ax -= ux * f; b.ay -= uy * f;
  }
  for (const n of nodes) {
    n.ax += (w / 2 - n.x) * SIM.center; n.ay += (h / 2 - n.y) * SIM.center;
    n.vx = (n.vx + n.ax * alpha) * SIM.damp; n.vy = (n.vy + n.ay * alpha) * SIM.damp;
    n.x += n.vx; n.y += n.vy;
  }
}

function fit(nodes, svg, root, pad = 80) {
  if (!nodes.length) return;
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const a = Math.min(...xs), b = Math.max(...xs), c = Math.min(...ys), e = Math.max(...ys);
  const box = svg.getBoundingClientRect(), w = box.width || 900, h = box.height || 600;
  const k = Math.max(.25, Math.min(2.2, Math.min((w - pad * 2) / Math.max(1, b - a), (h - pad * 2) / Math.max(1, e - c))));
  G.t = { k, x: w / 2 - ((a + b) / 2) * k, y: h / 2 - ((c + e) / 2) * k };
  root.setAttribute("transform", `translate(${G.t.x},${G.t.y}) scale(${G.t.k})`);
}

function renderGraph() {
  const svg = $("graphSvg"), box = svg.getBoundingClientRect();
  const w = box.width || 900, h = box.height || 600;
  const { nodes, links } = graphData();
  G.nodes = nodes; G.links = links;
  $("graphMeta").textContent = `${nodes.length} facts · ${links.length} links`;
  $("nodePanel").innerHTML = "";
  if (!nodes.length) { svg.innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#94a3b8" font-size="13.5" font-family="Inter">No relationships match these filters.</text>`; return; }

  nodes.forEach((n, i) => {
    const ang = i * 2.399963, rad = Math.min(w, h) * .3 * Math.sqrt(i / nodes.length);
    n.x = w / 2 + Math.cos(ang) * rad; n.y = h / 2 + Math.sin(ang) * rad; n.vx = n.vy = 0;
  });

  svg.innerHTML = `<g id="gr"><g id="gl" stroke-linecap="round"></g><g id="gn"></g></g>`;
  const root = svg.querySelector("#gr"), gl = svg.querySelector("#gl"), gn = svg.querySelector("#gn");
  const ST = { corroborates: { w: 1.2, o: .26 }, contradicts: { w: 2.4, o: .85 }, reconciled: { w: 2.2, o: .8 } };

  const le = links.map((l) => {
    const s = ST[l.type], e = document.createElementNS("http://www.w3.org/2000/svg", "line");
    e.setAttribute("stroke", C[l.type]); e.setAttribute("stroke-width", s.w); e.setAttribute("stroke-opacity", s.o);
    gl.appendChild(e); return e;
  });

  const cut = [...nodes].sort((a, b) => b.degree - a.degree)[Math.min(7, nodes.length - 1)]?.degree ?? 99;
  const ne = nodes.map((n, i) => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.style.cursor = "pointer";
    const r = 5 + Math.min(9, Math.sqrt(n.degree) * 2.4);
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("r", r); c.setAttribute("fill", n.color); c.setAttribute("fill-opacity", ".92");
    c.setAttribute("stroke", "#fff"); c.setAttribute("stroke-width", "1.6");
    g.appendChild(c);
    if (n.degree >= cut && n.degree > 1) {
      const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      const raw = `${n.fact.subject || ""} · ${n.fact.attribute || ""}`.trim();
      t.textContent = raw.length > 28 ? raw.slice(0, 27) + "…" : raw;
      t.setAttribute("x", r + 5); t.setAttribute("y", 4); t.setAttribute("font-size", "10.5");
      t.setAttribute("font-family", "Inter, sans-serif"); t.setAttribute("fill", "#475569");
      t.setAttribute("paint-order", "stroke"); t.setAttribute("stroke", "#fff"); t.setAttribute("stroke-width", "3.5");
      t.style.pointerEvents = "none"; g.appendChild(t);
    }
    g.addEventListener("click", (ev) => { ev.stopPropagation(); nodePanel(n, links, nodes); });
    g.addEventListener("mouseenter", () => {
      c.setAttribute("stroke", "#0f172a");
      le.forEach((x, li) => { const on = links[li].s === i || links[li].t === i;
        x.setAttribute("stroke-opacity", on ? .95 : .06); x.setAttribute("stroke-width", on ? 2.8 : 1); });
    });
    g.addEventListener("mouseleave", () => {
      c.setAttribute("stroke", "#fff");
      le.forEach((x, li) => { const s = ST[links[li].type]; x.setAttribute("stroke-opacity", s.o); x.setAttribute("stroke-width", s.w); });
    });
    gn.appendChild(g); return g;
  });

  const paint = () => {
    links.forEach((l, i) => { const a = nodes[l.s], b = nodes[l.t];
      le[i].setAttribute("x1", a.x); le[i].setAttribute("y1", a.y); le[i].setAttribute("x2", b.x); le[i].setAttribute("y2", b.y); });
    nodes.forEach((n, i) => ne[i].setAttribute("transform", `translate(${n.x},${n.y})`));
  };

  cancelAnimationFrame(G.raf);
  let alpha = 1, it = 0;
  (function tick() {
    for (let s = 0; s < 3; s++) { simStep(nodes, links, w, h, alpha); alpha *= .994; it++; }
    paint();
    if (it % 30 === 0) fit(nodes, svg, root);
    if (it < 600 && alpha > .02) G.raf = requestAnimationFrame(tick); else fit(nodes, svg, root);
  })();

  panZoom(svg, root);
}

function panZoom(svg, root) {
  if (svg._pz) return; svg._pz = true;
  let drag = false, sx = 0, sy = 0, ox = 0, oy = 0;
  const apply = () => svg.querySelector("#gr").setAttribute("transform", `translate(${G.t.x},${G.t.y}) scale(${G.t.k})`);
  svg.addEventListener("mousedown", (e) => { drag = true; svg.classList.add("grabbing"); sx = e.clientX; sy = e.clientY; ox = G.t.x; oy = G.t.y; });
  window.addEventListener("mouseup", () => { drag = false; svg.classList.remove("grabbing"); });
  window.addEventListener("mousemove", (e) => { if (!drag) return; G.t.x = ox + (e.clientX - sx); G.t.y = oy + (e.clientY - sy); apply(); });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12, b = svg.getBoundingClientRect();
    const mx = e.clientX - b.left, my = e.clientY - b.top;
    G.t.x = mx - (mx - G.t.x) * f; G.t.y = my - (my - G.t.y) * f;
    G.t.k = Math.max(.25, Math.min(4, G.t.k * f)); apply();
  }, { passive: false });
  svg.addEventListener("click", () => { $("nodePanel").innerHTML = ""; });
}

function nodePanel(node, links, nodes) {
  const f = node.fact;
  const conn = links.filter((l) => nodes[l.s].id === node.id || nodes[l.t].id === node.id).slice(0, 6);
  $("nodePanel").innerHTML = `<div class="npanel">
    <button class="btn-icon x" onclick="document.getElementById('nodePanel').innerHTML=''">${I.x}</button>
    <h4>${esc(f.subject || "Fact")}</h4>
    <div class="src">${esc(docName(f.document_name))} · page ${f.page_number} · ${node.degree} connection${node.degree === 1 ? "" : "s"}</div>
    <div style="line-height:1.55">${esc(f.statement)}</div>
    <div class="quote" style="margin-top:11px">"${esc(f.quote.slice(0, 200))}${f.quote.length > 200 ? "…" : ""}"</div>
    ${conn.map((l) => { const o = nodes[l.s].id === node.id ? nodes[l.t] : nodes[l.s];
      return `<div class="rel"><span class="badge b-${l.type}">${l.type}</span>
        <div style="margin-top:7px;color:var(--ink-2);line-height:1.45">${esc(o.fact.statement.slice(0, 120))}${o.fact.statement.length > 120 ? "…" : ""}</div></div>`; }).join("")}
  </div>`;
}

document.querySelectorAll("[data-gf]").forEach((b) => b.addEventListener("click", () => {
  const t = b.dataset.gf;
  if (G.filters.has(t)) { G.filters.delete(t); b.classList.remove("on"); } else { G.filters.add(t); b.classList.add("on"); }
  renderGraph();
}));
$("graphReset").addEventListener("click", () => { G.t = { x: 0, y: 0, k: 1 }; renderGraph(); });
$("graphDocFilter").addEventListener("change", renderGraph);

// ---------------- relationships ----------------

const RI = { corroborates: I.check, contradicts: I.x, reconciled: I.link, uncertain: I.alert };

function delta(a, b) {
  const x = a.value_numeric, y = b.value_numeric;
  if (x == null || y == null || !isFinite(x) || !isFinite(y)) return "";
  // Only compare like with like: an absolute in crore and a margin in percent
  // are both numbers, but their difference is meaningless.
  const ua = (a.unit || "").trim().toLowerCase(), ub = (b.unit || "").trim().toLowerCase();
  if (!ua || !ub || ua !== ub) return "";
  if (x === y) return `<div class="vcmp"><span>${fmtNum(x)}</span><span class="small">=</span><span>${fmtNum(y)}</span><span class="badge b-corroborates" style="margin-left:auto">exact match</span></div>`;
  const diff = Math.abs(x - y), base = Math.max(Math.abs(x), Math.abs(y)), pct = base ? (diff / base) * 100 : 0;
  return `<div class="vcmp"><span>${fmtNum(x)}</span><span class="small">vs</span><span>${fmtNum(y)}</span>
    <span class="badge ${pct > 1 ? "b-contradicts" : "b-reconciled"}" style="margin-left:auto">Δ ${fmtNum(diff)} ${esc(a.unit)} · ${pct.toFixed(1)}%</span></div>`;
}

function relCard(r) {
  // Evidence is the point of the system, so each side of a relationship
  // carries its own value, source link and grounding verdict -- a reviewer
  // should never have to open the Facts tab to judge whether a claim is
  // supported.
  const box = (f) => `<div class="fbox">
    <a class="pill" href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${I.file}${esc(docName(f.document_name))} <span class="small">(${esc(docScope(docById(f.document_id)))})</span> · p.${f.page_number}</a>
    <div class="st">${esc(f.statement)}</div>
    ${f.value != null || f.time_period || f.scope ? `<div class="fmeta">
      ${f.value != null ? `<span class="fv">${esc(f.value)}${f.unit ? ` <span class="small">${esc(f.unit)}</span>` : ""}</span>` : ""}
      ${f.time_period ? `<span class="ftag">${esc(f.time_period)}</span>` : ""}
      ${f.scope ? `<span class="ftag">${esc(f.scope)}</span>` : ""}
    </div>` : ""}
    <div class="quote ${f.evidence_status === "fact_validated" ? "" : "bad"}">"${esc(f.quote.slice(0, 190))}${f.quote.length > 190 ? "…" : ""}"</div>
    <div class="gstat">${evidenceBadge(f)}</div></div>`;
  return `<div class="rcard rise">
    <div class="rcard-h">
      <span class="badge b-${r.relation_type}"><span style="display:flex">${RI[r.relation_type] || ""}</span>${r.relation_type}</span>
      ${r.fact_a.document_id !== r.fact_b.document_id ? `<span class="badge b-brand">cross-document</span>` : ""}
      <span class="small num" style="margin-left:auto">similarity ${Number(r.similarity_score).toFixed(2)}${r.confidence != null ? ` · confidence ${Number(r.confidence).toFixed(2)}` : ""}</span>
    </div>
    <div class="rcard-b">
      <div class="rpair">${box(r.fact_a)}
        <div class="rconn ${r.relation_type}"><div class="ln"></div><div class="ic">${RI[r.relation_type] || ""}</div><div class="ln"></div></div>
        ${box(r.fact_b)}</div>
      ${delta(r.fact_a, r.fact_b)}
      <div class="why"><b>Reasoning:</b> ${esc(r.explanation)}</div>
      ${r.reconciliation_context ? `<div class="rctx">${I.link}<span><b>Reconciled by:</b> ${esc(r.reconciliation_context)}</span></div>` : ""}
      ${r.candidate_reason ? `<div class="why prov"><b>Retrieved by:</b> ${esc(r.candidate_reason)}</div>` : ""}
    </div></div>`;
}

function renderRels() {
  const type = $("relType").value, doc = $("relDoc").value;
  let rels = S.rels;
  if (type) rels = rels.filter((r) => r.relation_type === type);
  if (doc) rels = rels.filter((r) => String(r.fact_a.document_id) === doc || String(r.fact_b.document_id) === doc);
  $("relCount").textContent = `${rels.length} relationship${rels.length === 1 ? "" : "s"}`;
  $("relList").innerHTML = rels.length ? rels.slice(0, 150).map(relCard).join("") : empty("No relationships match these filters.");
  animateIn($("view-relationships"));
}
$("relType").addEventListener("change", renderRels);
$("relDoc").addEventListener("change", renderRels);

// ---------------- facts ----------------

function factCard(f) {
  const meta = [];
  if (f.time_period) meta.push(`<span>${esc(f.time_period)}</span>`);
  if (f.scope) meta.push(`<span>${esc(f.scope)}</span>`);
  if (f.unit) meta.push(`<span>${esc(f.unit)}</span>`);
  if (f.confidence != null) meta.push(`<span>confidence ${Number(f.confidence).toFixed(2)}</span>`);
  return `<div class="fcard rise">
    <div class="fcard-h">
      <a class="pill" href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${I.file}${esc(docName(f.document_name))} <span class="small">(${esc(docScope(docById(f.document_id)))})</span> · p.${f.page_number}</a>
      ${evidenceBadge(f)}
    </div>
    <div class="fstate">${esc(f.statement)}</div>
    <div class="triple"><span class="s">${esc(f.subject || "?")}</span><span class="ar">${I.arrow}</span>
      <span class="a">${esc(f.attribute || "?")}</span><span class="ar">=</span><span class="v">${esc(f.value || "?")}</span></div>
    ${meta.length ? `<div class="meta">${meta.join("")}</div>` : ""}
    <div class="quote ${f.evidence_status === "fact_validated" ? "" : "bad"}">${f.quote_grounded ? "" : `<span class="wtag">Not found verbatim — </span>`}"${esc(f.quote)}"</div>
    ${f.evidence_status && f.evidence_status !== "fact_validated" && f.evidence_detail
        ? `<div class="edetail">${esc(f.evidence_detail)}</div>` : ""}
  </div>`;
}

// Grounding has two independent levels: is the quote real, and does it
// actually support the value? A fact can pass the first and fail the
// second -- 36% of real extractions did -- so the badge distinguishes
// them rather than showing a green tick for "the text exists".
function evidenceBadge(f) {
  const s = f.evidence_status;
  if (s === "fact_validated" || (!s && f.quote_grounded)) {
    return `<span class="badge b-done"><span style="display:flex">${I.shield}</span>evidence verified</span>`;
  }
  if (s === "quote_grounded") {
    return `<span class="badge b-reconciled"><span style="display:flex">${I.alert}</span>quote real, value unverified</span>`;
  }
  return `<span class="badge b-processing"><span style="display:flex">${I.alert}</span>quote unverified</span>`;
}

function renderFacts() {
  const doc = $("factDoc").value, q = $("factSearch").value.toLowerCase();
  let facts = S.facts;
  if (doc) facts = facts.filter((f) => String(f.document_id) === doc);
  if (q) facts = facts.filter((f) => (f.statement || "").toLowerCase().includes(q) || (f.subject || "").toLowerCase().includes(q) || (f.attribute || "").toLowerCase().includes(q));
  $("factCount").textContent = `${facts.length} fact${facts.length === 1 ? "" : "s"}`;
  $("factList").innerHTML = facts.length ? facts.slice(0, 200).map(factCard).join("") : empty("No facts match.");
  animateIn($("view-facts"));
}
$("factDoc").addEventListener("change", renderFacts);
$("factSearch").addEventListener("input", () => { clearTimeout(window._t); window._t = setTimeout(renderFacts, 200); });

// ---------------- documents table ----------------

let poll = null;

function progress(p) {
  if (!p) return `<div class="ptrack"><div class="pfill indet"></div></div><div class="plabel"><span class="stg">Starting…</span></div>`;
  const pct = p.total ? Math.min(100, Math.round((p.current / p.total) * 100)) : 0;
  const lbl = { extracting: "Extracting facts", embedding: "Generating embeddings", comparing: "Comparing facts" }[p.stage] || p.stage;
  const ind = p.total <= 1;
  return `<div class="ptrack"><div class="pfill${ind ? " indet" : ""}" style="width:${ind ? 34 : pct}%"></div></div>
    <div class="plabel"><span class="stg">${esc(lbl)}${p.detail ? " · " + esc(p.detail) : ""}</span><span class="num">${p.total > 1 ? `${p.current}/${p.total}` : ""}</span></div>`;
}

async function renderDocTable() {
  const docs = await fetch(`${API}/api/documents`).then((r) => r.json());
  S.docs = docs; paintChrome();
  const body = $("docBody"); body.innerHTML = "";
  let busy = false;
  for (const d of docs) {
    const active = d.status === "pending" || d.status === "processing";
    if (active) busy = true;
    const stoppingTag = d.cancel_requested ? `<span class="small" style="color:var(--warn);margin-left:7px">stopping…</span>` : "";
    const cell = d.status === "processing"
      ? `<div style="min-width:220px">${badge(d.status)}${stoppingTag}<div style="margin-top:8px">${progress(d.progress)}</div></div>`
      : `${badge(d.status)}${stoppingTag}`;
    const tr = el(`<tr class="clickable">
      <td><div style="display:flex;align-items:center;gap:10px">
        <span class="sb-dot" style="background:${docColor(d.id)}"></span>
        <span><span style="font-weight:500">${esc(docName(d.original_name))}</span><span class="small" style="display:block">${esc(docScope(d))}</span></span>
      </div></td>
      <td>${cell}</td><td class="r">${d.num_pages ?? "—"}</td><td class="r">${d.fact_count}</td>
      <td class="small">${new Date(d.uploaded_at * 1000).toLocaleString()}</td>
      <td style="display:flex;gap:6px">
        ${active ? stopButton(d.id, { small: true }) : ""}
        <a href="${pdfLink(d.id, 1)}" target="_blank" onclick="event.stopPropagation()"><button class="btn-icon" title="Open PDF">${I.ext}</button></a>
      </td></tr>`);
    tr.addEventListener("click", () => go(`document/${d.id}`));
    body.appendChild(tr);
    if (d.status === "failed" && d.error_message) body.appendChild(el(`<tr class="notice err"><td colspan="6">${esc(d.error_message.split("\n")[0])}</td></tr>`));
    if (d.reused_from_document_id) body.appendChild(el(`<tr class="notice"><td colspan="6">Identical to document #${d.reused_from_document_id} — results reused, no LLM calls made.</td></tr>`));
  }
  if (!docs.length) body.appendChild(el(`<tr><td colspan="6">${empty("No documents yet — upload one to get started.")}</td></tr>`));
  clearTimeout(poll);
  if (busy) poll = setTimeout(renderDocTable, 2000);
}

// ---------------- issues ----------------

function renderIssues() {
  fetch(`${API}/api/issues`).then((r) => r.json()).then((issues) => {
    $("issueBody").innerHTML = issues.length ? issues.map((i) => `<tr>
      <td class="r small">${i.document_id ?? "—"}</td><td class="r small">${i.page_number ?? "—"}</td>
      <td><span class="badge b-failed"><span style="display:flex">${I.alert}</span>${esc(i.issue_type)}</span></td>
      <td class="small">${esc(i.detail)}${i.raw_excerpt ? `<div class="quote" style="margin-top:7px">${esc(i.raw_excerpt.slice(0, 220))}</div>` : ""}</td></tr>`).join("")
      : `<tr><td colspan="4">${empty("No issues logged yet.")}</td></tr>`;
  });
}

// ---------------- upload ----------------

const pdfInput = $("pdfInput"), dz = $("dz"), chip = $("fileChip");
dz.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", chipUp);
["dragenter", "dragover"].forEach((e) => dz.addEventListener(e, (v) => { v.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((e) => dz.addEventListener(e, (v) => { v.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (v) => { const f = v.dataTransfer.files[0]; if (f) { const dt = new DataTransfer(); dt.items.add(f); pdfInput.files = dt.files; chipUp(); } });
function chipUp() { const f = pdfInput.files[0]; chip.innerHTML = f ? `<div class="dz-f">${I.file}${esc(f.name)} · ${(f.size / 1048576).toFixed(1)} MB</div>` : ""; }

$("upBtn").addEventListener("click", async () => {
  const st = $("upStatus"), btn = $("upBtn");
  st.className = "small";
  if (!pdfInput.files.length) { st.textContent = "Choose a PDF first."; st.className = "small err"; return; }
  const fd = new FormData(); fd.append("file", pdfInput.files[0]);
  const p = new URLSearchParams();
  if ($("pagesSpec").value) p.set("pages", $("pagesSpec").value);
  else if ($("maxPages").value) p.set("max_pages", $("maxPages").value);
  let url = `${API}/api/documents`; if ([...p].length) url += `?${p}`;
  btn.disabled = true; st.textContent = "Uploading…";
  const resp = await fetch(url, { method: "POST", body: fd });
  btn.disabled = false;
  if (!resp.ok) { st.textContent = `Upload failed: ${await resp.text()}`; st.className = "small err"; return; }
  const doc = await resp.json();
  st.className = "small ok";
  st.textContent = doc.reused_document_id ? `Identical to document #${doc.reused_document_id} — reused instantly.` : `Queued as document #${doc.id}.`;
  pdfInput.value = ""; chip.innerHTML = "";
  go("documents");
});

// ---------------- init ----------------

(async function init() {
  await loadAll();
  await route_(location.hash.slice(1) || "overview");
  renderDocTable();
})();

// ---------------- logic check (graph coherence) ----------------
// Relationships are judged pairwise and in isolation, so the graph they
// form can be internally impossible. This view surfaces those proofs and
// the edges transitivity implies. See app/coherence.py.

const REL_DOT = (t) => `<span class="cdot" style="background:${C[t] || "#94a3b8"}"></span>`;

function cohFactLine(f) {
  if (!f || !f.statement) return `<div class="cfact"><span class="small">fact ${esc(f && f.id)}</span></div>`;
  return `<div class="cfact">
    <div class="cfact-t">${esc(f.statement)}</div>
    <div class="small">${esc(docName(f.document_name))} · p.${esc(f.page_number)}</div>
  </div>`;
}

function cohViolation(v) {
  const edges = v.edges.map((e) => `
    <div class="cedge ${e.is_suspect ? "suspect" : ""}">
      ${REL_DOT(e.relation_type)}
      <b>${esc(e.relation_type)}</b>
      <span class="small num">confidence ${e.confidence == null ? "—" : Number(e.confidence).toFixed(2)}</span>
      ${e.is_suspect ? `<span class="ctag">most likely wrong</span>` : ""}
    </div>`).join("");
  return `<div class="ccard rise">
    <div class="ccard-h">${I.alert}<b>Impossible triangle</b>
      <span class="small" style="margin-left:auto">facts ${v.facts.map((f) => esc(f.id)).join(" · ")}</span>
    </div>
    <div class="ccard-b">
      <div class="cfacts">${v.facts.map(cohFactLine).join("")}</div>
      <div class="cedges">${edges}</div>
      <div class="why"><b>Why this can't hold:</b> ${esc(v.reason)}</div>
    </div>
  </div>`;
}

function cohInference(i) {
  return `<div class="ccard rise">
    <div class="ccard-h">${REL_DOT(i.relation_type)}<b>${esc(i.relation_type)}</b>
      <span class="ctag ok">deduced · no model call</span>
      <span class="small num" style="margin-left:auto">confidence ${Number(i.confidence).toFixed(2)}</span>
    </div>
    <div class="ccard-b">
      <div class="cfacts">${cohFactLine(i.fact_a)}${cohFactLine(i.fact_b)}</div>
      <div class="why"><b>Reasoning:</b> ${esc(i.explanation)}</div>
    </div>
  </div>`;
}

async function renderCoherence() {
  const head = $("cohHead"), body = $("cohBody");
  if (!head || !body) return;
  head.innerHTML = `<div class="empty">${I.loader}<span>Checking the graph…</span></div>`;
  body.innerHTML = "";

  let d;
  try {
    d = await (await fetch(`${API}/api/coherence?limit=25`)).json();
  } catch (e) {
    head.innerHTML = empty("Could not run the logic check.");
    return;
  }

  const cnt = $("cCoh");
  if (cnt) cnt.textContent = d.violations_total || "";

  head.innerHTML = `
    <div class="note">
      Every relationship is judged <b>pairwise, in isolation</b>. But equality is transitive: if
      A corroborates B and B corroborates C, then A <b>cannot</b> contradict C. Triangles like that
      prove at least one judgment is wrong — with no ground truth, no reviewer, and no extra model
      call. The same transitivity implies edges that candidate retrieval never shortlisted.
    </div>
    <div class="grid g4 mb24">
      ${statTile("Closed triangles", d.triangles_checked, "checked for logical consistency", I.layers, "")}
      ${statTile("Impossible", d.violations_total, `${(d.violation_rate * 100).toFixed(1)}% of triangles checked`, I.alert, d.violations_total ? "warn" : "good")}
      ${statTile("Edges implicated", d.implicated_edges, "at least one per triangle is wrong", I.link, "")}
      ${statTile("Deduced edges", d.inferences_total, "found with no model call", I.zap, "good")}
    </div>`;
  // The tiles animate from zero via [data-count]; without this they stay
  // showing 0, which reads as "nothing found" rather than "not yet counted".
  animateIn(head);

  body.innerHTML = `
    <h3 class="sec">Proven inconsistencies</h3>
    ${d.violations.length ? d.violations.map(cohViolation).join("") : empty("No logically impossible triangles. The graph is self-consistent.")}
    <h3 class="sec">Relationships deduced by transitivity</h3>
    <div class="note small">These were never sent to the model. They follow from edges the graph
    already contains, and any deduction resting on an edge implicated above is discarded rather
    than inheriting a known error.</div>
    ${d.inferences.length ? d.inferences.map(cohInference).join("") : empty("Nothing further follows from the current graph.")}`;

  animateIn(body);
}
