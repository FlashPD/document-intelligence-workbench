"use strict";

let pilotData = null;
let pilotRequests = Promise.resolve();
let pilotLastInteraction = 0;

function openPilotTrial() {
  return pilotData?.trials.find((trial) => trial.status === "RUNNING" || trial.status === "PAUSED");
}

function renderPilot() {
  if (!pilotData) return;
  const trial = openPilotTrial();
  const completed = pilotData.trials.filter((t) => t.status === "COMPLETE").length;
  const incomplete = pilotData.trials.filter((t) => t.status === "ABANDONED").length;
  $("pilot-progress").textContent = `${completed}/${pilotData.scheduled} finished · ${incomplete} incomplete${trial ? ` · ${trial.status.toLowerCase()}` : ""}`;
  $("pilot-timing").textContent = trial
    ? `Recorded active ${Math.round(trial.active_seconds)} s · paused ${Math.round(trial.paused_seconds)} s · idle ${Math.round(trial.idle_seconds)} s`
    : pilotData.next_document ? `Next: invoice ${pilotData.next_document.ordinal} of ${pilotData.scheduled}` : "All scheduled invoices accounted for. Export the pilot report from the terminal.";
  $("pilot-start").hidden = Boolean(trial) || !pilotData.next_document;
  $("pilot-pause").hidden = trial?.status !== "RUNNING";
  $("pilot-resume").hidden = trial?.status !== "PAUSED";
  $("pilot-finish").hidden = trial?.status !== "RUNNING";
  $("pilot-abandon").hidden = !trial;
  $("actor").disabled = Boolean(trial);
  if (state.detail && trial?.status === "PAUSED") {
    $("review-grid").hidden = true;
    $("review-bottom").hidden = true;
    $("approve-button").hidden = true;
  }
}

function pilotEvent(kind) {
  const trial = openPilotTrial();
  if (!trial) return Promise.resolve();
  const event = { trial_id: trial.id, kind, event_id: crypto.randomUUID().replaceAll("-", "") };
  // Keep pause/resume/finish ordered after throttled interaction events.
  const next = pilotRequests.then(async () => {
    const current = openPilotTrial();
    if (!current || current.id !== event.trial_id) return;
    if ((kind === "pause" && current.status === "PAUSED") || (kind === "interaction" && current.status !== "RUNNING")) return;
    pilotData = await request("/api/pilot/event", {
      method: "POST", keepalive: true,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(event),
    });
    renderPilot();
  });
  pilotRequests = next.catch((error) => notice(`Pilot timing: ${error.message}`, true));
  return next;
}

async function initializePilot() {
  pilotData = await request("/api/pilot");
  $("pilot-panel").hidden = false;
  $("queue").closest(".panel").hidden = true;
  $("seed-clean").closest(".actions").hidden = true;
  $("empty").querySelector("h2").textContent = "Begin the timed pilot";
  $("empty").querySelector("p").textContent = "Start the next invoice to open its source and suggestions. Review every field and row, then approve and export JSON.";
  document.querySelector(".mode").textContent = "Author pilot · recorded OCR rules";
  if ($("actor").value === "demo-reviewer") $("actor").value = "project-author";
  const trial = openPilotTrial();
  if (trial) {
    $("actor").value = trial.actor;
    if (trial.status === "RUNNING") await pilotEvent("pause");
    await selectDocument(trial.document_id);
  }
  renderPilot();
}

$("pilot-start").addEventListener("click", () => action(async () => {
  $("pilot-start").disabled = true;
  try {
    pilotData = await post("/api/pilot/start", { document_id: pilotData.next_document.document_id, actor: actor() });
    renderPilot();
    await selectDocument(openPilotTrial().document_id);
    notice("Timing started. Review all headers and every line item against the source.");
  } finally { $("pilot-start").disabled = false; }
}));
$("pilot-pause").addEventListener("click", () => action(() => pilotEvent("pause")));
$("pilot-resume").addEventListener("click", () => action(async () => {
  await pilotEvent("resume");
  await refreshDocument();
  renderPilot();
}));
$("pilot-finish").addEventListener("click", () => action(async () => {
  await pilotEvent("finish");
  $("document").hidden = true;
  $("empty").hidden = false;
  $("empty").querySelector("h2").textContent = "Trial recorded";
  $("empty").querySelector("p").textContent = "Start the next invoice when ready. Every trial remains in the pilot report.";
  notice("Trial saved with its approved JSON export.");
}));
$("pilot-abandon").addEventListener("click", () => action(async () => {
  await pilotEvent("abandon");
  $("document").hidden = true;
  $("empty").hidden = false;
  notice("Trial retained as incomplete. It cannot be repeated in this pilot.");
}));

function pilotInteraction(event) {
  if (openPilotTrial()?.status !== "RUNNING" || event.target.closest?.("#pilot-panel")) return;
  const now = performance.now();
  if (now - pilotLastInteraction < 2500) return;
  pilotLastInteraction = now;
  pilotEvent("interaction");
}
document.addEventListener("pointerdown", pilotInteraction);
document.addEventListener("keydown", pilotInteraction);
document.addEventListener("visibilitychange", () => {
  if (document.hidden && openPilotTrial()?.status === "RUNNING") pilotEvent("pause");
});
window.addEventListener("pagehide", () => {
  if (openPilotTrial()?.status === "RUNNING") pilotEvent("pause");
});
