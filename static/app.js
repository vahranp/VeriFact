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
    .map(([k, v]) => `${k}: <b>${v}</b>`).join(" &middot; ");
  document.getElementById("stats").innerHTML = `
    <span>docs: <b>${s.documents_done}/${s.documents}</b></span>
    <span>facts: <b>${s.facts}</b>${s.ungrounded_facts ? ` (${s.ungrounded_facts} ungrounded)` : ""}</span>
    <span>relationships: <b>${s.relationships}</b>${byType ? " — " + byType : ""}</span>
    <span>issues: <b>${s.issues}</b></span>
  `;
}

// ---------------- upload ----------------

document.getElementById("uploadBtn").addEventListener("click", async () => {
  const input = document.getElementById("pdfInput");
  const maxPages = document.getElementById("maxPages").value;
  const pagesSpec = document.getElementById("pagesSpec").value;
  const statusEl = document.getElementById("uploadStatus");
  if (!input.files.length) { statusEl.textContent = "Choose a PDF first."; return; }

  const fd = new FormData();
  fd.append("file", input.files[0]);
  const params = new URLSearchParams();
  if (pagesSpec) params.set("pages", pagesSpec);
  else if (maxPages) params.set("max_pages", maxPages);
  let url = `${API}/api/documents`;
  if ([...params].length) url += `?${params}`;

  statusEl.textContent = "Uploading...";
  const resp = await fetch(url, { method: "POST", body: fd });
  if (!resp.ok) {
    statusEl.textContent = `Upload failed: ${await resp.text()}`;
    return;
  }
  const doc = await resp.json();
  statusEl.textContent = `Uploaded (doc #${doc.id}). Processing in background — switch to Documents to watch.`;
  input.value = "";
  document.querySelector('nav button[data-view="documents"]').click();
});

// ---------------- documents ----------------

let pollTimer = null;

async function loadDocuments() {
  const docs = await fetch(`${API}/api/documents`).then((r) => r.json());
  const body = document.getElementById("documentsBody");
  body.innerHTML = "";
  let anyInFlight = false;

  for (const d of docs) {
    if (d.status === "pending" || d.status === "processing") anyInFlight = true;
    const row = el(`
      <tr>
        <td>${escapeHtml(d.original_name)}</td>
        <td><span class="badge status-${d.status}">${d.status}</span></td>
        <td>${d.num_pages ?? "-"}</td>
        <td>${d.fact_count}</td>
        <td class="small">${new Date(d.uploaded_at * 1000).toLocaleString()}</td>
        <td><a href="${pdfLink(d.id, 1)}" target="_blank">open PDF</a></td>
      </tr>
    `);
    if (d.status === "failed" && d.error_message) {
      const detail = el(`<tr><td colspan="6" class="small" style="color:#ef5563;">${escapeHtml(d.error_message)}</td></tr>`);
      body.appendChild(row);
      body.appendChild(detail);
    } else {
      body.appendChild(row);
    }
  }

  if (!docs.length) body.appendChild(el(`<tr><td colspan="6" class="empty">No documents yet — upload one.</td></tr>`));

  clearTimeout(pollTimer);
  if (anyInFlight) pollTimer = setTimeout(loadDocuments, 3000);
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
    <div class="card">
      <div class="small"><a href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${escapeHtml(f.document_name)} · p.${f.page_number}</a></div>
      <div style="margin-top:4px;">${escapeHtml(f.statement)}</div>
      <div class="meta">
        <span><b>${escapeHtml(f.subject || "?")}</b> → ${escapeHtml(f.attribute || "?")} = ${escapeHtml(f.value || "?")}</span>
        ${metaParts.join("")}
      </div>
      <div class="quote ${f.quote_grounded ? "" : "ungrounded"}">${f.quote_grounded ? "" : "⚠ not found verbatim on page — "}"${escapeHtml(f.quote)}"</div>
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
  listEl.innerHTML = facts.length
    ? facts.map(factCard).join("")
    : `<div class="empty">No facts match.</div>`;
}
document.getElementById("factDocFilter").addEventListener("change", loadFacts);
document.getElementById("factSearch").addEventListener("input", () => {
  clearTimeout(window._factSearchT);
  window._factSearchT = setTimeout(loadFacts, 250);
});

// ---------------- relationships ----------------

function relCard(r) {
  const a = r.fact_a, b = r.fact_b;
  const box = (f) => `
    <div class="fact-box">
      <div class="doc"><a href="${pdfLink(f.document_id, f.page_number)}" target="_blank">${escapeHtml(f.document_name)} · p.${f.page_number}</a></div>
      <div class="statement">${escapeHtml(f.statement)}</div>
      <div class="quote">"${escapeHtml(f.quote)}"</div>
    </div>
  `;
  return `
    <div class="card">
      <span class="badge rel-${r.relation_type}">${r.relation_type}</span>
      <span class="small"> similarity ${Number(r.similarity_score).toFixed(2)}${r.confidence != null ? ` · model confidence ${Number(r.confidence).toFixed(2)}` : ""}</span>
      <div class="rel-pair">${box(a)}${box(b)}</div>
      <div class="explanation"><b>Why:</b> ${escapeHtml(r.explanation)}</div>
      ${r.reconciliation_context ? `<div class="reconcile-context">Reconciled by: ${escapeHtml(r.reconciliation_context)}</div>` : ""}
    </div>
  `;
}

async function loadRelationships() {
  const type = document.getElementById("relTypeFilter").value;
  const params = new URLSearchParams();
  if (type) params.set("relation_type", type);
  const rels = await fetch(`${API}/api/relationships?${params}`).then((r) => r.json());
  const listEl = document.getElementById("relList");
  listEl.innerHTML = rels.length
    ? rels.map(relCard).join("")
    : `<div class="empty">No relationships of this type yet.</div>`;
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
          <td><span class="badge status-failed">${escapeHtml(i.issue_type)}</span></td>
          <td class="small">${escapeHtml(i.detail)}${i.raw_excerpt ? `<div class="quote">${escapeHtml(i.raw_excerpt.slice(0, 300))}</div>` : ""}</td>
        </tr>
      `).join("")
    : `<tr><td colspan="4" class="empty">No issues logged yet.</td></tr>`;
}

// ---------------- init ----------------

loadStats();
loadDocuments();
