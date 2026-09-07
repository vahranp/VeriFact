const API = "";

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function pdfLink(documentId, page) {
  return `${API}/api/documents/${documentId}/pdf#page=${page || 1}`;
}

// ---------------- icons ----------------

const ICONS = {
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  loader: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  externalLink: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
  barChart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>`,
  file: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  alertTriangle: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  inbox: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>`,
  link: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  copy: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
  chevronDown: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`,
};

const STATUS_META = {
  done: { icon: ICONS.check, label: "done" },
  processing: { icon: ICONS.loader, label: "processing" },
  pending: { icon: ICONS.clock, label: "pending" },
  failed: { icon: ICONS.x, label: "failed" },
};

function statusBadge(status) {
  const m = STATUS_META[status] || { icon: "", label: status };
  const spin = status === "processing" ? ' style="animation:spin 1.4s linear infinite"' : "";
  return `<span class="badge status-${status}"><span${spin}>${m.icon}</span>${m.label}</span>`;
}

if (!document.getElementById("spin-kf")) {
  const style = document.createElement("style");
  style.id = "spin-kf";
  style.textContent = "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}";
  document.head.appendChild(style);
}

function emptyState(text) {
  return `<div class="empty">${ICONS.inbox}<span>${escapeHtml(text)}</span></div>`;
}

// ---------------- nav ----------------

const views = ["upload", "documents", "facts", "relationships", "issues"];
document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    views.forEach((v) => document.getElementById(`view-${v}`).classList.remove("active"));
    document.getElementById(`view-${btn.dataset.view}`).classList.add("active");
    refreshCurrent(btn.dataset.view);
  });
});

function refreshCurrent(view) {
  if (view === "documents") loadDocuments();
  if (view === "facts") loadFactFilters().then(loadFacts);
  if (view === "relationships") loadRelationships();
  if (view === "issues") loadIssues();
  loadStats();
}

// ---------------- stats ----------------

async function loadStats() {
  const s = await fetch(`${API}/api/stats`).then((r) => r.json());
  const byType = Object.entries(s.relationships_by_type)
    .map(([k, v]) => `${k} <b>${v}</b>`).join(" &middot; ");
  document.getElementById("stats").innerHTML = `
    <span class="stat-chip">${ICONS.file}docs <b>${s.documents_done}/${s.documents}</b></span>
    <span class="stat-chip accent">${ICONS.barChart}facts <b>${s.facts}</b>${s.ungrounded_facts ? ` <span style="opacity:.7">(${s.ungrounded_facts} ungrounded)</span>` : ""}</span>
    <span class="stat-chip">${ICONS.link}relationships <b>${s.relationships}</b>${byType ? " — " + byType : ""}</span>
    <span class="stat-chip">${ICONS.alertTriangle}issues <b>${s.issues}</b></span>
  `;
}

// ---------------- upload ----------------

const pdfInput = document.getElementById("pdfInput");
const dropzone = document.getElementById("dropzone");
const fileChip = document.getElementById("fileChip");

dropzone.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", () => renderFileChip());

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    pdfInput.files = dt.files;
    renderFileChip();
  }
});

function renderFileChip() {
  const f = pdfInput.files[0];
  fileChip.innerHTML = f
    ? `<div class="dz-file">${ICONS.file}${escapeHtml(f.name)} &middot; ${(f.size / 1024 / 1024).toFixed(1)} MB</div>`
    : "";
}

document.getElementById("uploadBtn").addEventListener("click", async () => {
  const maxPages = document.getElementById("maxPages").value;
  const pagesSpec = document.getElementById("pagesSpec").value;
  const statusEl = document.getElementById("uploadStatus");
  const btn = document.getElementById("uploadBtn");
  statusEl.className = "small";
  if (!pdfInput.files.length) { statusEl.textContent = "Choose a PDF first."; statusEl.className = "small error"; return; }

  const fd = new FormData();
  fd.append("file", pdfInput.files[0]);
  const params = new URLSearchParams();
  if (pagesSpec) params.set("pages", pagesSpec);
  else if (maxPages) params.set("max_pages", maxPages);
  let url = `${API}/api/documents`;
  if ([...params].length) url += `?${params}`;

  btn.disabled = true;
  statusEl.textContent = "Uploading...";
  const resp = await fetch(url, { method: "POST", body: fd });
  btn.disabled = false;
  if (!resp.ok) {
    statusEl.textContent = `Upload failed: ${await resp.text()}`;
    statusEl.className = "small error";
    return;
  }
  const doc = await resp.json();
  statusEl.className = "small success";
  statusEl.textContent = doc.reused_document_id
    ? `Identical to document #${doc.reused_document_id} — reused instantly, no processing needed.`
    : `Uploaded (doc #${doc.id}). Processing in background — switch to Documents to watch progress.`;
  pdfInput.value = "";
  fileChip.innerHTML = "";
  document.querySelector('nav button[data-view="documents"]').click();
});

// ---------------- documents ----------------

let pollTimer = null;

function progressBar(progress) {
  if (!progress) {
    return `<div class="progress-track"><div class="progress-fill indeterminate"></div></div>
            <div class="progress-label"><span class="stage">Starting…</span></div>`;
  }
  const pct = progress.total ? Math.min(100, Math.round((progress.current / progress.total) * 100)) : 0;
  const stageLabel = { extracting: "Extracting facts", embedding: "Generating embeddings", comparing: "Comparing facts" }[progress.stage] || progress.stage;
  const indet = progress.total <= 1;
  return `
    <div class="progress-track"><div class="progress-fill${indet ? " indeterminate" : ""}" style="width:${indet ? 40 : pct}%"></div></div>
    <div class="progress-label">
      <span class="stage">${escapeHtml(stageLabel)}${progress.detail ? " — " + escapeHtml(progress.detail) : ""}</span>
      <span>${progress.total > 1 ? `${progress.current}/${progress.total} (${pct}%)` : ""}</span>
    </div>`;
}

async function loadDocuments() {
  const docs = await fetch(`${API}/api/documents`).then((r) => r.json());
  const body = document.getElementById("documentsBody");
  body.innerHTML = "";
  let anyInFlight = false;

  for (const d of docs) {
    if (d.status === "pending" || d.status === "processing") anyInFlight = true;
    const statusCell = d.status === "processing"
      ? `<div class="progress-cell">${statusBadge(d.status)}<div style="margin-top:6px;">${progressBar(d.progress)}</div></div>`
      : statusBadge(d.status);
    const row = el(`
      <tr>
        <td class="doc-name">${escapeHtml(d.original_name)}</td>
        <td>${statusCell}</td>
        <td>${d.num_pages ?? "-"}</td>
        <td>${d.fact_count}</td>
        <td class="small">${new Date(d.uploaded_at * 1000).toLocaleString()}</td>
        <td><a href="${pdfLink(d.id, 1)}" target="_blank" title="Open PDF"><button class="icon-btn ghost">${ICONS.externalLink}</button></a></td>
        <td>${d.status === "done" ? `<button class="icon-btn perf-btn" data-doc="${d.id}" title="Performance stats">${ICONS.barChart}</button>` : ""}</td>
      </tr>
    `);
    body.appendChild(row);
    if (d.status === "failed" && d.error_message) {
      body.appendChild(el(`<tr class="notice-row error"><td colspan="7">${ICONS.alertTriangle} ${escapeHtml(d.error_message)}</td></tr>`));
    }
    if (d.reused_from_document_id) {
      body.appendChild(el(`<tr class="notice-row"><td colspan="7">${ICONS.link} Identical content + page selection as document #${d.reused_from_document_id} — reused its results, no LLM calls made.</td></tr>`));
    }
  }

  if (!docs.length) body.appendChild(el(`<tr><td colspan="7">${emptyState("No documents yet — upload one to get started.")}</td></tr>`));

  body.querySelectorAll(".perf-btn").forEach((btn) => {
    btn.addEventListener("click", () => togglePerfRow(btn));
  });

  clearTimeout(pollTimer);
  if (anyInFlight) pollTimer = setTimeout(loadDocuments, 2000);
}

function fmtSecs(s) {
  if (s == null) return "-";
  return s >= 60 ? `${Math.floor(s / 60)}m ${(s % 60).toFixed(1)}s` : `${s.toFixed(1)}s`;
}

async function togglePerfRow(btn) {
  const existing = btn.closest("tr").nextElementSibling;
  if (existing && existing.classList.contains("perf-row")) {
    existing.remove();
    return;
  }
  document.querySelectorAll(".perf-row").forEach((r) => r.remove());
  const doc = await fetch(`${API}/api/documents/${btn.dataset.doc}`).then((r) => r.json());
  const s = doc.stats;
  let rowHtml;
  if (!s) {
    rowHtml = `<tr class="perf-row"><td colspan="7" class="small">No performance stats recorded for this document (processed before this instrumentation was added).</td></tr>`;
  } else {
    const timingRows = [
      ["PDF extraction", s.timing.pdf_extraction],
      ["Fact extraction (LLM)", s.timing.fact_extraction],
      ["Embeddings", s.timing.embeddings],
      ["Candidate retrieval", s.timing.candidate_retrieval],
      ["Relationship reasoning (LLM)", s.timing.relationship_reasoning],
      ["Database", s.timing.database],
    ];
    const maxT = Math.max(...timingRows.map((r) => r[1]), 0.001);
    rowHtml = `<tr class="perf-row"><td colspan="7">
        <div class="perf-panel">
          <div class="perf-grid">
            <div class="perf-stat"><div class="n">${s.pages}</div><div class="l">Pages</div></div>
            <div class="perf-stat"><div class="n">${s.chunks}</div><div class="l">Chunks</div></div>
            <div class="perf-stat"><div class="n">${s.facts}</div><div class="l">Facts</div></div>
            <div class="perf-stat"><div class="n">${s.candidate_pairs}</div><div class="l">Candidate pairs</div></div>
            <div class="perf-stat"><div class="n">${s.relationships_stored}</div><div class="l">Relationships</div></div>
            <div class="perf-stat"><div class="n">${s.ollama_calls}</div><div class="l">Ollama calls</div></div>
            <div class="perf-stat"><div class="n">${s.extraction_cache_hits + s.reasoning_cache_hits}</div><div class="l">Cache hits</div></div>
            <div class="perf-stat"><div class="n">${fmtSecs(s.timing.total)}</div><div class="l">Total time</div></div>
          </div>
          <div class="perf-timing">
            ${timingRows.map(([label, val]) => `
              <div class="perf-timing-row">
                <div class="tl">${escapeHtml(label)}</div>
                <div class="tt"><div class="tf" style="width:${Math.max(2, (val / maxT) * 100)}%"></div></div>
                <div class="tv">${fmtSecs(val)}</div>
              </div>`).join("")}
          </div>
        </div>
      </td></tr>`;
  }
  btn.closest("tr").insertAdjacentElement("afterend", el(rowHtml));
}

// ---------------- facts ----------------

async function loadFactFilters() {
  const docs = await fetch(`${API}/api/documents`).then((r) => r.json());
  const sel = document.getElementById("factDocFilter");
  const current = sel.value;
  sel.innerHTML = `<option value="">All documents</option>` +
    docs.map((d) => `<option value="${d.id}">${escapeHtml(d.original_name)}</option>`).join("");
  sel.value = current;
}

function factCard(f) {
  const metaParts = [];
  if (f.time_period) metaParts.push(`<span>${escapeHtml(f.time_period)}</span>`);
  if (f.scope) metaParts.push(`<span>${escapeHtml(f.scope)}</span>`);
  if (f.unit) metaParts.push(`<span>${escapeHtml(f.unit)}</span>`);
  if (f.confidence != null) metaParts.push(`<span>confidence ${Number(f.confidence).toFixed(2)}</span>`);
  return `
    <div class="fact-card${f.quote_grounded ? "" : " ungrounded"}">
      <div class="src">${ICONS.file}<a href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${escapeHtml(f.document_name)} · p.${f.page_number}</a></div>
      <div class="statement">${escapeHtml(f.statement)}</div>
      <div class="kv"><b>${escapeHtml(f.subject || "?")}</b> → ${escapeHtml(f.attribute || "?")} = ${escapeHtml(f.value || "?")}</div>
      <div class="meta">${metaParts.join("")}</div>
      <div class="quote ${f.quote_grounded ? "" : "ungrounded"}">${f.quote_grounded ? "" : `<span class="warn-tag">${ICONS.alertTriangle} not found verbatim on page — </span>`}"${escapeHtml(f.quote)}"</div>
    </div>
  `;
}

async function loadFacts() {
  const docId = document.getElementById("factDocFilter").value;
  const q = document.getElementById("factSearch").value;
  const params = new URLSearchParams();
  if (docId) params.set("document_id", docId);
  if (q) params.set("q", q);
  const facts = await fetch(`${API}/api/facts?${params}`).then((r) => r.json());
  const listEl = document.getElementById("factsList");
  listEl.innerHTML = facts.length ? facts.map(factCard).join("") : emptyState("No facts match.");
}
document.getElementById("factDocFilter").addEventListener("change", loadFacts);
document.getElementById("factSearch").addEventListener("input", () => {
  clearTimeout(window._factSearchT);
  window._factSearchT = setTimeout(loadFacts, 250);
});

// ---------------- relationships ----------------

const REL_ICON = {
  corroborates: ICONS.check,
  contradicts: ICONS.x,
  reconciled: ICONS.link,
};

function relCard(r) {
  const a = r.fact_a, b = r.fact_b;
  const box = (f) => `
    <div class="fact-box">
      <div class="doc">${ICONS.file}<a href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${escapeHtml(f.document_name)} · p.${f.page_number}</a></div>
      <div class="statement">${escapeHtml(f.statement)}</div>
      <div class="quote">"${escapeHtml(f.quote)}"</div>
    </div>
  `;
  return `
    <div class="rel-card">
      <div class="rel-card-head">
        <span class="badge rel-${r.relation_type}">${REL_ICON[r.relation_type] || ""}${r.relation_type}</span>
        <span class="small">similarity ${Number(r.similarity_score).toFixed(2)}${r.confidence != null ? ` · model confidence ${Number(r.confidence).toFixed(2)}` : ""}</span>
      </div>
      <div class="rel-card-body">
        <div class="rel-pair">${box(a)}${box(b)}</div>
        <div class="explanation"><b>Why:</b> ${escapeHtml(r.explanation)}</div>
        ${r.reconciliation_context ? `<div class="reconcile-context">${ICONS.link} Reconciled by: ${escapeHtml(r.reconciliation_context)}</div>` : ""}
      </div>
    </div>
  `;
}

async function loadRelationships() {
  const type = document.getElementById("relTypeFilter").value;
  const params = new URLSearchParams();
  if (type) params.set("relation_type", type);
  const rels = await fetch(`${API}/api/relationships?${params}`).then((r) => r.json());
  const listEl = document.getElementById("relList");
  listEl.innerHTML = rels.length ? rels.map(relCard).join("") : emptyState("No relationships of this type yet.");
}
document.getElementById("relTypeFilter").addEventListener("change", loadRelationships);

// ---------------- issues ----------------

async function loadIssues() {
  const issues = await fetch(`${API}/api/issues`).then((r) => r.json());
  const body = document.getElementById("issuesBody");
  body.innerHTML = issues.length
    ? issues.map((i) => `
        <tr>
          <td class="small">${i.document_id ?? "-"}</td>
          <td class="small">${i.page_number ?? "-"}</td>
          <td><span class="badge status-failed">${ICONS.alertTriangle}${escapeHtml(i.issue_type)}</span></td>
          <td class="small">${escapeHtml(i.detail)}${i.raw_excerpt ? `<div class="quote">${escapeHtml(i.raw_excerpt.slice(0, 300))}</div>` : ""}</td>
        </tr>
      `).join("")
    : `<tr><td colspan="4">${emptyState("No issues logged yet.")}</td></tr>`;
}

// ---------------- init ----------------

loadStats();
loadDocuments();
