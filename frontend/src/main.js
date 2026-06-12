import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:5000";

const state = {
  health: null,
  summary: null,
  headers: [],
  items: [],
  loading: true,
  error: "",
  search: "",
  files: [],
  includeDebug: false,
  uploading: false,
  message: "",
  lastResult: null,
};

const root = document.getElementById("root");

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function statusClass(status) {
  if (status === "Completed") return "success";
  if (status === "Failed") return "danger";
  return "warning";
}

function escapeHtml(value) {
  return formatValue(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function icon(label) {
  return `<span class="icon-glyph" aria-hidden="true">${label}</span>`;
}

async function loadDashboard() {
  state.loading = true;
  state.error = "";
  render();

  try {
    const [healthRes, summaryRes, headersRes, itemsRes] = await Promise.all([
      fetch(`${API_BASE}/health`),
      fetch(`${API_BASE}/database-summary`),
      fetch(`${API_BASE}/headers?limit=25`),
      fetch(`${API_BASE}/items?limit=25`),
    ]);

    if (!healthRes.ok) throw new Error("API health check failed.");

    const [healthData, summaryData, headersData, itemsData] = await Promise.all([
      healthRes.json(),
      summaryRes.json(),
      headersRes.json(),
      itemsRes.json(),
    ]);

    state.health = healthData;
    state.summary = summaryData;
    state.headers = headersData.data || [];
    state.items = itemsData.items || [];
  } catch (err) {
    state.error = err.message || "Could not connect to Flask API.";
    state.health = null;
    state.summary = null;
    state.headers = [];
    state.items = [];
  } finally {
    state.loading = false;
    render();
  }
}

async function submitUpload() {
  if (!state.files.length || state.uploading) return;

  state.uploading = true;
  state.message = "";
  state.lastResult = null;
  render();

  const formData = new FormData();
  state.files.forEach((file) => formData.append("files", file));

  try {
    const res = await fetch(`${API_BASE}/extract?include_debug=${state.includeDebug}`, {
      method: "POST",
      body: formData,
    });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.message || "Upload failed.");

    state.lastResult = payload;
    state.message = payload.message || "Documents processed.";
    await loadDashboard();
  } catch (err) {
    state.message = err.message || "Could not process documents.";
  } finally {
    state.uploading = false;
    render();
  }
}

function metricCard(label, value, iconLabel, tone = "neutral") {
  return `
    <section class="metric ${tone}">
      <div class="metric-icon">${icon(iconLabel)}</div>
      <div>
        <div class="metric-label">${escapeHtml(label)}</div>
        <div class="metric-value">${escapeHtml(value)}</div>
      </div>
    </section>
  `;
}

function statusPill(label, ok) {
  return `
    <span class="status-pill ${ok ? "ok" : "bad"}">
      ${icon(ok ? "OK" : "!")}
      ${escapeHtml(label)}
    </span>
  `;
}

function uploadPanel() {
  const selectedRows = state.files
    .map(
      (file) => `
        <tr>
          <td>${escapeHtml(file.name)}</td>
          <td>${escapeHtml((file.size / 1024).toFixed(1))} KB</td>
        </tr>
      `,
    )
    .join("");

  const resultCards = (state.lastResult?.documents || [])
    .map(
      (doc) => `
        <div class="result-card">
          <strong>${escapeHtml(doc.file_name)}</strong>
          <span>${escapeHtml(doc.data?.po_number)}</span>
        </div>
      `,
    )
    .join("");

  return `
    <section class="panel upload-panel">
      <div class="panel-title-row">
        <div>
          <h2>Manual Upload</h2>
          <p>Send PO PDFs to the existing Flask extraction flow.</p>
        </div>
        ${icon("UP")}
      </div>

      <button class="drop-zone" type="button" data-action="choose-files">
        ${icon("PDF")}
        <span>Select PDF files</span>
        <small>Multiple files supported</small>
      </button>
      <input id="pdf-input" type="file" accept="application/pdf" multiple hidden />

      ${
        selectedRows
          ? `
            <div class="mini-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Size</th>
                  </tr>
                </thead>
                <tbody>${selectedRows}</tbody>
              </table>
            </div>
          `
          : ""
      }

      <label class="checkbox-row">
        <input id="include-debug" type="checkbox" ${state.includeDebug ? "checked" : ""} />
        Include debug details
      </label>

      <button class="primary-button" type="button" data-action="submit-upload" ${
        !state.files.length || state.uploading ? "disabled" : ""
      }>
        ${state.uploading ? '<span class="spinner"></span>' : icon("UP")}
        Process Documents
      </button>

      ${state.message ? `<div class="inline-message">${escapeHtml(state.message)}</div>` : ""}
      ${resultCards ? `<div class="result-strip">${resultCards}</div>` : ""}
    </section>
  `;
}

function recordsTable(title, rows, type) {
  const query = state.search.toLowerCase();
  const visibleRows = query
    ? rows.filter((row) => JSON.stringify(row).toLowerCase().includes(query))
    : rows;
  const columns =
    type === "items"
      ? ["file_name", "po_number", "item_no", "item_description", "quantity", "unit_price", "tax_percent", "line_total"]
      : ["file_name", "po_number", "po_date", "buyer_name", "billing_gst_number", "extraction_status", "warnings"];

  const body = visibleRows.length
    ? visibleRows
        .map(
          (row, index) => `
            <tr>
              ${columns
                .map((column) => {
                  const value = escapeHtml(row[column]);
                  if (column === "extraction_status") {
                    return `<td><span class="record-status ${statusClass(row[column])}">${value}</span></td>`;
                  }
                  return `<td>${value}</td>`;
                })
                .join("")}
            </tr>
          `,
        )
        .join("")
    : `<tr><td colSpan="${columns.length}" class="empty-cell">No records found</td></tr>`;

  return `
    <section class="panel table-panel">
      <div class="panel-title-row compact">
        <h2>${escapeHtml(title)}</h2>
        <span>${visibleRows.length} records</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>${columns.map((column) => `<th>${escapeHtml(column.replaceAll("_", " "))}</th>`).join("")}</tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>
  `;
}

function watcherPanel() {
  const folders = [
    { name: "incoming_pdfs", role: "Drop PDFs here", icon: "IN" },
    { name: "processed_pdfs", role: "Successful PDFs move here", icon: "OK" },
    { name: "failed_pdfs", role: "Failed PDFs move here", icon: "ERR" },
    { name: "outputs", role: "Clean JSON files are written here", icon: "JSON" },
  ];

  return `
    <section class="panel watcher-panel">
      <div class="panel-title-row">
        <div>
          <h2>Watcher Workflow</h2>
          <p>Run the watcher beside Flask to automate folder-based processing.</p>
        </div>
        ${icon("SYNC")}
      </div>
      <div class="folder-grid">
        ${folders
          .map(
            (folder) => `
              <div class="folder-card">
                ${icon(folder.icon)}
                <strong>${escapeHtml(folder.name)}</strong>
                <span>${escapeHtml(folder.role)}</span>
              </div>
            `,
          )
          .join("")}
      </div>
      <div class="command-box">python watcher.py</div>
    </section>
  `;
}

function appTemplate() {
  const databaseConnected = Boolean(state.health?.database?.connected || state.summary?.connected);

  return `
    <main class="app-shell">
      <header class="topbar">
        <div>
          <h1>PO Info Extractor</h1>
          <p>Dashboard connected to your current Flask backend.</p>
        </div>
        <div class="top-actions">
          ${statusPill(state.health ? "API Online" : "API Offline", Boolean(state.health))}
          ${statusPill(databaseConnected ? "Database Connected" : "Database Unavailable", databaseConnected)}
          <button class="icon-button" type="button" data-action="reload" aria-label="Refresh dashboard">
            ${icon("R")}
          </button>
        </div>
      </header>

      ${
        state.error
          ? `
            <section class="alert-panel">
              ${icon("!")}
              <span>${escapeHtml(state.error)}</span>
            </section>
          `
          : ""
      }

      <section class="metrics-grid">
        ${metricCard("API Base", API_BASE.replace("http://", ""), "API")}
        ${metricCard("Header Records", state.summary?.total_headers_in_database ?? 0, "DB", "blue")}
        ${metricCard("Item Records", state.summary?.total_items_in_database ?? 0, "TXT", "green")}
        ${metricCard("Latest Rows Loaded", state.headers.length + state.items.length, "OK", "amber")}
      </section>

      <section class="main-grid">
        ${uploadPanel()}
        ${watcherPanel()}
      </section>

      <section class="search-row">
        ${icon("S")}
        <input id="record-search" value="${escapeHtml(state.search)}" placeholder="Search latest database records" />
        ${state.loading ? '<span class="spinner"></span>' : ""}
      </section>

      ${recordsTable("Latest PO Headers", state.headers, "headers")}
      ${recordsTable("Latest Line Items", state.items, "items")}
    </main>
  `;
}

function bindEvents() {
  root.querySelector('[data-action="reload"]')?.addEventListener("click", loadDashboard);
  root.querySelector('[data-action="choose-files"]')?.addEventListener("click", () => {
    root.querySelector("#pdf-input")?.click();
  });
  root.querySelector("#pdf-input")?.addEventListener("change", (event) => {
    state.files = Array.from(event.target.files || []);
    render();
  });
  root.querySelector("#include-debug")?.addEventListener("change", (event) => {
    state.includeDebug = event.target.checked;
  });
  root.querySelector('[data-action="submit-upload"]')?.addEventListener("click", submitUpload);
  root.querySelector("#record-search")?.addEventListener("input", (event) => {
    state.search = event.target.value;
    render();
  });
}

function render() {
  root.innerHTML = appTemplate();
  bindEvents();
}

render();
loadDashboard();
