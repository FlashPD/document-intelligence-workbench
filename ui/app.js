"use strict";

const state = { documents: [], selectedId: null, detail: null, selectedPath: "fields.invoice_number", pageNumber: 1, history: [], rotations: new Map(), pageImages: new Map(), renderToken: 0, reviewPilot: false, background: false, replay: false, statusKey: null, batchId: null, deletions: new Set(), polling: false };
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
    if (doc.status === "REVIEW_READY" && doc.review_priority) {
      const leading = doc.review_priority.signals[0];
      const priority = node("span", "", `Triage ${doc.review_priority.points}${leading ? ` · ${leading.code}` : " · no validation signal"}`);
      priority.title = "Triage points rank validation signals; zero points do not establish correctness.";
      item.append(priority);
    }
    if (doc.error_code) item.append(node("span", "", doc.error_code));
    item.addEventListener("click", () => action(() => selectDocument(doc.id)));
    queue.append(item);
  }
}

async function selectDocument(id) {
  state.selectedId = id;
  state.detail = null;
  state.statusKey = null;
  state.selectedPath = "fields.invoice_number";
  state.pageNumber = 1;
  state.rotations.clear();
  state.pageImages.clear();
  state.renderToken++;
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
  if (id !== state.selectedId) return;
  state.statusKey = JSON.stringify([status.status, status.current_revision, status.job?.stage, status.job?.error_code]);
  $("doc-title").textContent = status.source_name;
  $("doc-meta").textContent = `${id.slice(0, 12)} · SHA-256 ${status.source_sha256.slice(0, 16)}…`;
  $("doc-status").textContent = status.status;
  $("doc-kind").textContent = status.job ? "UPLOADED DOCUMENT" : "TRUSTED SAMPLE · FRESH OCR";
  const processing = !["REVIEW_READY", "APPROVED"].includes(status.status);
  $("lifecycle-actions").hidden = !status.job || state.reviewPilot || state.replay;
  $("cancel-button").hidden = !["QUEUED", "PROCESSING"].includes(status.job?.status) || status.status === "CANCELLING";
  $("reprocess-button").hidden = ["QUEUED", "PROCESSING"].includes(status.job?.status);
  $("reparse").parentElement.hidden = $("reprocess-button").hidden;
  if (!status.current_revision || processing) {
    state.detail = null;
    $("pending").hidden = false;
    $("pending").replaceChildren(node("p", "", status.job?.error_code
      ? `Processing failed: ${status.job.error_code}. Fix the cause, then retry.`
      : `Job ${status.job?.stage || status.job?.status || "QUEUED"}.${state.background ? " Processing runs automatically." : " Use Process next job after building the parser image."}`));
    if (status.job?.status === "FAILED" && status.job?.stage !== "CLEANUP_REQUIRED") {
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
  const detail = await request(`/api/documents/${id}`);
  const history = await request(`/api/documents/${id}/history`);
  if (id !== state.selectedId) return;
  state.detail = detail;
  state.pageImages.clear();
  state.history = history;
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
  const cited = state.detail.pages.flatMap((page) => page.spans).find((span) => field.evidence_ids.includes(span.id));
  if (cited) setPage(cited.page);
  else renderHighlights();
}

function setPage(number) {
  const pages = state.detail?.pages || [];
  if (!pages.some((page) => page.number === number)) return;
  state.pageNumber = number;
  const page = pages[number - 1];
  $("page-select").value = String(number);
  $("page-previous").disabled = number === 1;
  $("page-next").disabled = number === pages.length;
  const rotation = state.rotations.get(number) || 0;
  const sideways = rotation === 90 || rotation === 270;
  $("page-frame").style.aspectRatio = sideways
    ? `${page.height_px} / ${page.width_px}` : `${page.width_px} / ${page.height_px}`;
  $("rotation-label").textContent = `${rotation}°`;
  $("highlights").replaceChildren();
  $("evidence-note").textContent = "Loading source page…";
  drawPage(page, rotation);
}

async function drawPage(page, rotation) {
  const token = ++state.renderToken;
  const canvas = $("page-canvas");
  canvas.hidden = true;
  let image = state.pageImages.get(page.number);
  if (!image) {
    image = new Image();
    image.src = `/api/documents/${state.detail.document_id}/pages/${page.number}?revision=${state.detail.revision}`;
    state.pageImages.set(page.number, image);
  }
  try {
    await image.decode();
    if (token !== state.renderToken) return;
    if (image.naturalWidth !== page.width_px || image.naturalHeight !== page.height_px)
      throw new Error("Page image dimensions do not match its evidence coordinates.");
    const width = page.width_px;
    const height = page.height_px;
    const sideways = rotation === 90 || rotation === 270;
    const viewWidth = sideways ? height : width;
    const viewHeight = sideways ? width : height;
    const scale = Math.min(1, 8192 / Math.max(width, height), Math.sqrt(8_000_000 / (width * height)));
    canvas.width = Math.max(1, Math.round(viewWidth * scale));
    canvas.height = Math.max(1, Math.round(viewHeight * scale));
    canvas.setAttribute("aria-label", `Invoice page ${page.number} of ${state.detail.pages.length}, rotated ${rotation} degrees clockwise`);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Page preview is unavailable in this browser.");
    context.setTransform(scale, 0, 0, scale, 0, 0);
    if (rotation === 90) { context.translate(height, 0); context.rotate(Math.PI / 2); }
    else if (rotation === 180) { context.translate(width, height); context.rotate(Math.PI); }
    else if (rotation === 270) { context.translate(0, width); context.rotate(-Math.PI / 2); }
    context.drawImage(image, 0, 0);
    canvas.hidden = false;
    renderHighlights();
  } catch (error) {
    if (token === state.renderToken) {
      state.pageImages.delete(page.number);
      $("evidence-note").textContent = "Page preview unavailable";
      notice(error.message || "Page preview failed to load.", true);
    }
  }
}

function rotatedBox(box, rotation) {
  if (rotation === 90) return { left: 1 - box.bottom, top: box.left, right: 1 - box.top, bottom: box.right };
  if (rotation === 180) return { left: 1 - box.right, top: 1 - box.bottom, right: 1 - box.left, bottom: 1 - box.top };
  if (rotation === 270) return { left: box.top, top: 1 - box.right, right: box.bottom, bottom: 1 - box.left };
  return box;
}

function rotatePage(step) {
  if (!state.detail) return;
  const number = state.pageNumber;
  state.rotations.set(number, ((state.rotations.get(number) || 0) + step + 360) % 360);
  setPage(number);
}

function renderHighlights() {
  const target = $("highlights");
  target.replaceChildren();
  const field = fieldAt(state.selectedPath);
  if (!field) return;
  const spans = new Map(state.detail.pages[state.pageNumber - 1].spans.map((span) => [span.id, span]));
  const rotation = state.rotations.get(state.pageNumber) || 0;
  let count = 0;
  for (const id of field.evidence_ids) {
    const span = spans.get(id);
    if (!span?.box) continue;
    const bounds = rotatedBox(span.box, rotation);
    const highlight = node("div", "highlight");
    highlight.style.left = `${bounds.left * 100}%`;
    highlight.style.top = `${bounds.top * 100}%`;
    highlight.style.width = `${(bounds.right - bounds.left) * 100}%`;
    highlight.style.height = `${(bounds.bottom - bounds.top) * 100}%`;
    target.append(highlight);
    count++;
  }
  $("evidence-note").textContent = count ? `${count} cited OCR line${count === 1 ? "" : "s"} on this page` : "No cited line on this page";
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
          reason: input.value,
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
  $("doc-kind").textContent = detail.extraction?.profile === "replay_ocr_rules"
    ? (state.reviewPilot ? "AUTHOR PILOT · RECORDED OCR RULES" : "RECORDED OCR RULES · REPLAY")
    : detail.extraction?.profile === "span_llm"
    ? `LOCAL SPAN MODEL · ${detail.extraction.model_id}`
    : (state.documents.find((item) => item.id === detail.document_id)?.job_status ? "UPLOADED DOCUMENT · OCR RULES" : "TRUSTED SAMPLE · FRESH OCR");
  $("doc-status").textContent = detail.approval ? "APPROVED" : "REVIEW_READY";
  $("revision-label").textContent = `Revision ${detail.revision}`;
  $("approve-button").disabled = Boolean(detail.approval);
  const pageSelect = $("page-select");
  pageSelect.replaceChildren(...detail.pages.map((page) => {
    const option = node("option", "", `${page.number} of ${detail.pages.length}`);
    option.value = String(page.number);
    return option;
  }));
  setPage(Math.min(state.pageNumber, detail.pages.length));
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
  const files = [...$("upload").files];
  if (!files.length || files.length > 20) throw new Error("Choose between 1 and 20 PDFs, PNGs, or JPEGs.");
  const extractor = $("extractor").value;
  const batch = await post("/api/batches", { count: files.length, extractor });
  state.batchId = batch.batch_id;
  $("upload-button").disabled = true;
  let selected = null;
  try {
    for (const [position, file] of files.entries()) {
      const media = file.type || ({ pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg" })[file.name.split(".").pop().toLowerCase()] || "application/octet-stream";
      try {
        const data = await request("/api/upload", {
          method: "POST", headers: { "Content-Type": media, "X-File-Name": encodeURIComponent(file.name),
            "X-Batch-Id": batch.batch_id, "X-Batch-Position": String(position) }, body: file,
        });
        selected = selected || data.document_id;
      } catch (error) {
        notice(`${file.name}: ${error.message}`, true);
      }
    }
    await refreshQueue();
    if (selected) await selectDocument(selected);
    await refreshBatch();
    notice(state.background ? "Batch submitted. Processing runs automatically; each outcome is listed below." : "Batch submitted. Use Process next job to process queued documents.");
  } finally {
    $("upload-button").disabled = false;
  }
}

async function refreshBatch() {
  if (!state.batchId) return;
  const batch = await request(`/api/batches/${state.batchId}`);
  $("batch-status").hidden = false;
  $("batch-status").textContent = batch.items.map((item) => `${item.position + 1}: ${item.stage || item.status}${item.error_code ? ` (${item.error_code})` : ""}`).join(" · ");
}

async function pollProcessing() {
  if (!state.background || state.polling || document.hidden) return;
  state.polling = true;
  try {
    await refreshQueue();
    if (state.selectedId) {
      const id = state.selectedId;
      const status = await request(`/api/documents/${id}/status`);
      const key = JSON.stringify([status.status, status.current_revision, status.job?.stage, status.job?.error_code]);
      if (id === state.selectedId && key !== state.statusKey) await refreshDocument();
    }
    await refreshBatch();
    const storage = await request("/api/storage");
    $("storage-status").hidden = false;
    $("storage-status").textContent = `Workbench storage: ${(storage.used_bytes / 1024**2).toFixed(1)} MiB of ${(storage.max_bytes / 1024**2).toFixed(0)} MiB`;
    $("deletions").replaceChildren();
    for (const deletion of await request("/api/deletions")) {
      $("deletions").append(node("p", "muted", `Deletion ${deletion.document_id.slice(0, 8)}: ${deletion.status}${deletion.error_code ? ` (${deletion.error_code})` : ""}`));
    }
    const runtime = await request("/api/runtime");
  $("actor").value = runtime.principal.actor;
    if (runtime.worker?.last_error) notice(`Processing needs attention: ${runtime.worker.last_error}`, true);
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.polling = false;
  }
}

async function saveCorrection(value) {
  if (!state.detail) return;
  await post(`/api/documents/${state.selectedId}/edit`, {
    revision: state.detail.revision, path: state.selectedPath, value,
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
$("cancel-button").addEventListener("click", () => action(async () => {
  await post(`/api/documents/${state.selectedId}/cancel`, {});
  await refreshDocument();
}, "Cancellation requested. Owned work must stop before it is settled."));
$("reprocess-button").addEventListener("click", () => action(async () => {
  const status = await request(`/api/documents/${state.selectedId}/status`);
  await post(`/api/documents/${state.selectedId}/reprocess`, {
    extractor: $("extractor").value, revision: status.current_revision, reparse: $("reparse").checked,
  });
  state.detail = null;
  state.pageImages.clear();
  $("downloads").replaceChildren();
  await refreshDocument();
}, "Reprocessing queued. The new candidate needs fresh approval; historical exports remain unchanged."));
$("delete-button").addEventListener("click", () => action(async () => {
  const id = state.selectedId;
  const title = $("doc-title").textContent;
  if (!window.confirm(`Delete ${title} and its local review history, pages and exports? External backups and downloaded copies remain.`)) return;
  await post(`/api/documents/${id}/delete`, {});
  state.deletions.add(id);
  state.selectedId = null;
  state.detail = null;
  state.pageImages.clear();
  state.renderToken++;
  await refreshQueue();
  await refreshDocument();
  notice("Deletion queued. It resumes after active work stops or after a restart.");
}));
let managedModel = false;
$("extractor").addEventListener("change", () => {
  $("model-settings").hidden = managedModel || $("extractor").value !== "span_llm";
});
$("process-button").addEventListener("click", () => action(async () => {
  const extractor = $("extractor").value;
  const input = { extractor };
  if (extractor === "span_llm" && !managedModel) {
    input.model_endpoint = $("model-endpoint").value.trim();
    input.model_id = $("model-id").value.trim();
  }
  const result = await post("/api/process-one", input);
  await refreshQueue();
  if (result.document_id) await selectDocument(result.document_id);
  notice(result.status === "IDLE" ? "No queued jobs." : `Job status: ${result.status}${result.job?.error_code ? ` (${result.job.error_code})` : ""}.`, result.status === "FAILED");
}));
$("save-edit").addEventListener("click", () => action(() => saveCorrection($("edit-value").value)));
$("mark-missing").addEventListener("click", () => action(() => saveCorrection(null)));
$("approve-button").addEventListener("click", () => action(async () => {
  await post(`/api/documents/${state.selectedId}/approve`, { revision: state.detail.revision });
  await refreshDocument();
  notice("Current revision approved.");
}));
$("export-json").addEventListener("click", () => action(() => exportRecord("json")));
$("export-csv").addEventListener("click", () => action(() => exportRecord("csv")));
$("page-select").addEventListener("change", () => setPage(Number($("page-select").value)));
$("page-previous").addEventListener("click", () => setPage(state.pageNumber - 1));
$("page-next").addEventListener("click", () => setPage(state.pageNumber + 1));
$("rotate-left").addEventListener("click", () => rotatePage(-90));
$("rotate-right").addEventListener("click", () => rotatePage(90));
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const fieldButton = event.target.closest?.("button.field");
  if (!fieldButton && event.target !== document.body && event.target !== $("page-canvas")) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    const buttons = [...document.querySelectorAll("button.field")];
    if (!buttons.length) return;
    const current = buttons.findIndex((button) => button.dataset.path === state.selectedPath);
    const next = buttons[Math.max(0, Math.min(buttons.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))];
    selectField(next.dataset.path);
    next.focus();
    event.preventDefault();
  } else if (event.key === "Enter" && state.detail) {
    if (fieldButton) selectField(fieldButton.dataset.path);
    $("edit-value").focus();
    event.preventDefault();
  }
});

action(async () => {
  const runtime = await request("/api/runtime");
  $("actor").value = runtime.principal.actor;
  state.reviewPilot = Boolean(runtime.review_pilot);
  state.background = Boolean(runtime.background_processing);
  state.replay = Boolean(runtime.demo_replay);
  $("process-button").hidden = state.background;
  if (state.background && !runtime.managed_model) {
    $("extractor").querySelector('option[value="span_llm"]').disabled = true;
  }
  managedModel = runtime.managed_model;
  if (runtime.demo_replay) {
    $("replay-panel").hidden = false;
    $("intake-panel").hidden = true;
    document.querySelector(".mode").textContent = "Recorded OCR replay · human approval";
    for (const fixture of runtime.demo_replay.cases) {
      const button = node("button", "queue-item", fixture.label);
      button.type = "button";
      button.addEventListener("click", () => action(() => selectDocument(fixture.document_id)));
      $("replay-cases").append(button);
    }
  }
  if (managedModel) {
    $("managed-model").hidden = false;
    $("managed-model").textContent = `Pinned local model ready: ${runtime.model_id}. Choose Local span model to use it.`;
    $("model-settings").hidden = true;
  }
  await refreshQueue();
  if (state.background) {
    const batches = await request("/api/batches");
    state.batchId = batches[0]?.batch_id || null;
    await refreshBatch();
    setInterval(pollProcessing, 1000);
  }
  if (runtime.review_pilot) await initializePilot();
  else if (runtime.demo_replay) await selectDocument(runtime.demo_replay.cases[0].document_id);
  else if (state.documents.length) await selectDocument(state.documents[0].id);
});
