"use strict";

const state = { documents: [], selectedId: null, detail: null, selectedPath: "fields.invoice_number", history: [] };
const $ = (id) => document.getElementById(id);

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = String(content);
  return element;
}

function notice(message, error = false) {
  const target = $("notice");
  target.textContent = message;
  target.classList.toggle("error", error);
  target.hidden = false;
}

async function request(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(data?.error || `Request failed (${response.status})`);
  return data;
}

function post(path, value) {
  return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}

function actor() {
  const value = $("actor").value.trim();
  if (!value) throw new Error("Enter a reviewer audit label first.");
  return value;
}

async function action(task, message) {
  try {
    await task();
    if (message) notice(message);
  } catch (error) {
    notice(error.message, true);
  }
}

async function refreshQueue() {
  state.documents = await request("/api/documents");
  $("queue-count").textContent = `${state.documents.length} total`;
  const queue = $("queue");
  queue.replaceChildren();
  for (const doc of state.documents) {
    const item = node("button", `queue-item${doc.id === state.selectedId ? " selected" : ""}`);
    item.type = "button";
    item.append(node("strong", "", doc.source_name));
    item.append(node("span", "", `${doc.status} · ${doc.current_revision ? `revision ${doc.current_revision}` : "awaiting parser"}`));
    if (doc.error_code) item.append(node("span", "", doc.error_code));
    item.addEventListener("click", () => action(() => selectDocument(doc.id)));
    queue.append(item);
  }
}

async function selectDocument(id) {
  state.selectedId = id;
  state.detail = null;
  state.selectedPath = "fields.invoice_number";
  $("downloads").replaceChildren();
  await refreshQueue();
  await refreshDocument();
}

async function refreshDocument() {
  const id = state.selectedId;
  $("empty").hidden = Boolean(id);
  $("document").hidden = !id;
  if (!id) return;
  const status = await request(`/api/documents/${id}/status`);
  $("doc-title").textContent = status.source_name;
  $("doc-meta").textContent = `${id.slice(0, 12)} · SHA-256 ${status.source_sha256.slice(0, 16)}…`;
  $("doc-status").textContent = status.status;
  $("doc-kind").textContent = status.job ? "UPLOADED DOCUMENT" : "TRUSTED SAMPLE · FRESH OCR";
  if (!status.current_revision) {
    $("pending").hidden = false;
    $("pending").replaceChildren(node("p", "", status.job?.error_code
      ? `Processing failed: ${status.job.error_code}. Fix the cause, then retry.`
      : `Job ${status.job?.status || "QUEUED"}. Use “Process next job” after building the parser image.`));
    if (status.job?.error_code) {
      const retry = node("button", "", "Retry job");
      retry.type = "button";
      retry.addEventListener("click", () => action(async () => {
        await post(`/api/documents/${id}/retry`, {});
        await refreshQueue();
        await refreshDocument();
      }, "Job queued for retry."));
      $("pending").append(retry);
    }
    $("review-grid").hidden = true;
    $("review-bottom").hidden = true;
    $("approve-button").hidden = true;
    return;
  }
  $("pending").hidden = true;
  $("review-grid").hidden = false;
  $("review-bottom").hidden = false;
  $("approve-button").hidden = false;
  state.detail = await request(`/api/documents/${id}`);
  state.history = await request(`/api/documents/${id}/history`);
  renderReview();
}

function fieldAt(path) {
  if (!state.detail) return null;
  const parts = path.split(".");
  if (parts[0] === "fields") return state.detail.record.fields[parts[1]] || null;
  const row = state.detail.record.line_items.find((item) => item.row_id === parts[1]);
  return row?.[parts[2]] || null;
}

function labelFor(path) {
  return path.replaceAll("_", " ").replaceAll(".", " / ");
}

function addField(parent, path, title, field) {
  const button = node("button", `field${state.selectedPath === path ? " selected" : ""}`);
  button.type = "button";
  button.dataset.path = path;
  button.append(node("span", "field-name", title));
  button.append(node("span", `field-value${field.value === null ? " missing" : ""}`, field.value ?? "Missing"));
  button.append(node("span", "field-origin", `${field.origin}${field.evidence_ids.length ? ` · ${field.evidence_ids.length} source span` : " · no source span"}`));
  button.addEventListener("click", () => selectField(path));
  parent.append(button);
}

function selectField(path) {
  if (!fieldAt(path)) return;
  state.selectedPath = path;
  document.querySelectorAll(".field").forEach((button) => button.classList.toggle("selected", button.dataset.path === path));
  const field = fieldAt(path);
  $("selection-label").textContent = labelFor(path);
  $("edit-value").value = field.value ?? "";
  renderHighlights();
}

function renderHighlights() {
  const target = $("highlights");
  target.replaceChildren();
  const field = fieldAt(state.selectedPath);
  if (!field) return;
  const spans = new Map(state.detail.page.spans.map((span) => [span.id, span]));
  let count = 0;
  for (const id of field.evidence_ids) {
    const span = spans.get(id);
    if (!span?.box) continue;
    const box = node("div", "highlight");
    box.style.left = `${span.box.left * 100}%`;
    box.style.top = `${span.box.top * 100}%`;
    box.style.width = `${(span.box.right - span.box.left) * 100}%`;
    box.style.height = `${(span.box.bottom - span.box.top) * 100}%`;
    target.append(box);
    count++;
  }
  $("evidence-note").textContent = count ? `${count} cited OCR line${count === 1 ? "" : "s"}` : "Precise source unavailable";
}

function renderIssues() {
  const target = $("issues");
  target.replaceChildren();
  const issues = state.detail.issues;
  const decisions = new Map(state.detail.decisions.map((decision) => [decision.issue_key, decision]));
  $("issue-count").textContent = `${issues.length} issue${issues.length === 1 ? "" : "s"}`;
  if (!issues.length) target.append(node("p", "muted", "No validation issues on this revision."));
  for (const issue of issues) {
    const key = `${issue.code}|${issue.path}`;
    const decision = decisions.get(key);
    const wrapper = node("div", "issue");
    const head = node("div", "issue-head");
    head.append(node("span", `issue-code${decision ? " resolved" : ""}`, issue.code));
    head.append(node("span", "issue-path", issue.path));
    head.append(node("span", "issue-path", decision ? "Acknowledged" : issue.blocking ? "Needs decision" : "Information"));
    wrapper.append(head, node("p", "", issue.detail));
    if (decision) wrapper.append(node("p", "muted", `Reason: ${decision.reason}`));
    if (issue.blocking && !decision && !state.detail.approval) {
      const input = node("input");
      input.placeholder = "Reason for acknowledging this issue";
      input.setAttribute("aria-label", `Reason for ${issue.code}`);
      const button = node("button", "", "Acknowledge");
      button.type = "button";
      button.addEventListener("click", () => action(async () => {
        await post(`/api/documents/${state.selectedId}/acknowledge`, {
          revision: state.detail.revision, code: issue.code, path: issue.path,
          reason: input.value, actor: actor(),
        });
        await refreshDocument();
      }, "Issue decision recorded."));
      wrapper.append(input, button);
    }
    target.append(wrapper);
  }
}

function renderHistory() {
  const target = $("history");
  target.replaceChildren();
  for (const event of state.history.slice().reverse()) {
    const item = node("div", "history-item", `${event.kind.replaceAll("_", " ")} · revision ${event.revision}`);
    item.append(node("span", "", `${event.actor} · ${new Date(event.created_at).toLocaleString()}`));
    target.append(item);
  }
}

function renderReview() {
  const detail = state.detail;
  $("doc-status").textContent = detail.approval ? "APPROVED" : "REVIEW_READY";
  $("revision-label").textContent = `Revision ${detail.revision}`;
  $("approve-button").disabled = Boolean(detail.approval);
  const frame = $("page-frame");
  frame.style.aspectRatio = `${detail.page.width_px} / ${detail.page.height_px}`;
  $("page-image").src = `/api/documents/${detail.document_id}/page`;
  const fields = $("fields");
  fields.replaceChildren();
  for (const [name, field] of Object.entries(detail.record.fields)) addField(fields, `fields.${name}`, name.replaceAll("_", " "), field);
  const rows = $("line-items");
  rows.replaceChildren();
  for (const row of detail.record.line_items) {
    rows.append(node("div", "row-heading", row.row_id));
    for (const name of ["description", "quantity", "unit_price", "line_total", "tax"])
      addField(rows, `line_items.${row.row_id}.${name}`, name.replaceAll("_", " "), row[name]);
  }
  if (!fieldAt(state.selectedPath)) state.selectedPath = "fields.invoice_number";
  selectField(state.selectedPath);
  renderIssues();
  renderHistory();
}

async function seed(fixture) {
  const data = await post("/api/demo/seed", { fixture });
  await selectDocument(data.document_id);
  notice("Trusted sample OCR complete. Review the source and fields.");
}

async function upload() {
  const file = $("upload").files[0];
  if (!file) throw new Error("Choose a PDF, PNG, or JPEG first.");
  const media = file.type || ({ pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg" })[file.name.split(".").pop().toLowerCase()];
  if (!media) throw new Error("The selected file type is unsupported.");
  const data = await request("/api/upload", {
    method: "POST", headers: { "Content-Type": media, "X-File-Name": encodeURIComponent(file.name) }, body: file,
  });
  await selectDocument(data.document_id);
  notice("Document queued. Process it after the parser image is ready.");
}

async function saveCorrection(value) {
  if (!state.detail) return;
  await post(`/api/documents/${state.selectedId}/edit`, {
    revision: state.detail.revision, path: state.selectedPath, value, actor: actor(),
  });
  await refreshQueue();
  await refreshDocument();
  notice("Correction saved as a new revision.");
}

async function exportRecord(format) {
  const manifest = await post(`/api/documents/${state.selectedId}/export`, { format });
  const target = $("downloads");
  target.replaceChildren();
  for (const file of manifest.files) {
    const link = node("a", "", file.path.split("/").pop());
    link.href = file.url;
    link.download = file.path.split("/").pop();
    target.append(link);
  }
  notice(`${format.toUpperCase()} export ready for revision ${manifest.revision}.`);
}

$("seed-clean").addEventListener("click", () => action(() => seed("clean")));
$("seed-conflict").addEventListener("click", () => action(() => seed("conflicting-total")));
$("upload-button").addEventListener("click", () => action(upload));
$("process-button").addEventListener("click", () => action(async () => {
  const result = await post("/api/process-one", {});
  await refreshQueue();
  if (result.document_id) await selectDocument(result.document_id);
  notice(result.status === "IDLE" ? "No queued jobs." : `Job status: ${result.status}${result.job?.error_code ? ` (${result.job.error_code})` : ""}.`, result.status === "FAILED");
}));
$("save-edit").addEventListener("click", () => action(() => saveCorrection($("edit-value").value)));
$("mark-missing").addEventListener("click", () => action(() => saveCorrection(null)));
$("approve-button").addEventListener("click", () => action(async () => {
  await post(`/api/documents/${state.selectedId}/approve`, { revision: state.detail.revision, actor: actor() });
  await refreshDocument();
  notice("Current revision approved.");
}));
$("export-json").addEventListener("click", () => action(() => exportRecord("json")));
$("export-csv").addEventListener("click", () => action(() => exportRecord("csv")));
document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLInputElement) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    const paths = [...document.querySelectorAll(".field")].map((button) => button.dataset.path);
    if (!paths.length) return;
    const current = paths.indexOf(state.selectedPath);
    selectField(paths[Math.max(0, Math.min(paths.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))]);
    event.preventDefault();
  } else if (event.key === "Enter" && state.detail) {
    $("edit-value").focus();
  }
});

action(async () => {
  await refreshQueue();
  if (state.documents.length) await selectDocument(state.documents[0].id);
});
