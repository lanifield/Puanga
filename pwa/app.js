import { getQueuedEntries, getSetting, queueEntry, removeQueuedEntries, setSetting } from "./db.js";

// The local service's address isn't stable across networks (and DHCP can
// reassign it even on the home network unless a router reservation is set —
// see the spec doc). Kept as a setting rather than hardcoded so it can be
// corrected from the phone without a redeploy.
const DEFAULT_SERVICE_URL = "https://puanga.local:8420";
const SYNC_INTERVAL_MS = 30_000;

let serviceUrl = DEFAULT_SERVICE_URL;
let runningEntry = null; // { ...entry fields, source: "live" | "offline" }
let options = []; // [{value, label, client}] — from the vault via GET /options

const els = {};

function uuid() {
  return crypto.randomUUID();
}

async function serviceReachable() {
  try {
    const res = await fetch(`${serviceUrl}/health`, { method: "GET", cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

// The project/course list comes from the vault, but the phone needs to be
// able to start an entry offline too — so the last-fetched list is cached
// and used as a fallback when the service can't be reached.
async function refreshOptions() {
  try {
    const res = await fetch(`${serviceUrl}/options`, { cache: "no-store" });
    if (!res.ok) throw new Error(`options fetch failed: ${res.status}`);
    const body = await res.json();
    options = body.options;
    await setSetting("cached_options", options);
  } catch {
    options = await getSetting("cached_options", []);
  }
  renderProjectOptions();
}

function renderProjectOptions() {
  const previousValue = els.projectId.value;
  els.projectId.innerHTML = "";
  if (options.length === 0) {
    els.projectId.appendChild(new Option("No projects found", ""));
    els.client.textContent = "—";
    return;
  }
  els.projectId.appendChild(new Option("Select a project or course…", ""));
  for (const opt of options) {
    els.projectId.appendChild(new Option(opt.label, opt.value));
  }
  if (options.some((o) => o.value === previousValue)) {
    els.projectId.value = previousValue;
  }
  updateClientDisplay();
}

function updateClientDisplay() {
  const selected = options.find((o) => o.value === els.projectId.value);
  els.client.textContent = selected ? selected.client : "—";
}

async function trySync() {
  const queued = await getQueuedEntries();
  setStatus(queued.length > 0 ? `${queued.length} entr${queued.length === 1 ? "y" : "ies"} queued` : "up to date");
  if (queued.length === 0) return;

  if (!(await serviceReachable())) {
    setStatus(`${queued.length} queued — service unreachable`);
    return;
  }

  try {
    const res = await fetch(`${serviceUrl}/entries/sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries: queued }),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`sync failed: ${res.status} ${body}`);
    }
    await removeQueuedEntries(queued.map((e) => e.id));
    setStatus("synced");
  } catch (err) {
    // Fails loudly to the console and status line rather than swallowing the
    // error the way the old apiPost did — that silent failure is exactly
    // what this rebuild exists to remove.
    console.error(err);
    setStatus("sync error — see console");
  }
}

function setStatus(text) {
  if (els.status) els.status.textContent = text;
}

function baseEntryFields() {
  const projectId = els.projectId.value;
  const selected = options.find((o) => o.value === projectId);
  return {
    id: uuid(),
    client_id: selected ? selected.client : "",
    project_id: projectId,
    task: els.task.value.trim(),
    notes: null,
    start_time: new Date().toISOString(),
    end_time: null,
    duration_minutes: null,
  };
}

async function startEntry() {
  const projectId = els.projectId.value;
  const task = els.task.value.trim();
  const selected = options.find((o) => o.value === projectId);
  if (!selected || !task) {
    setStatus("a project/course and a task are both required");
    return;
  }

  if (await serviceReachable()) {
    try {
      const res = await fetch(`${serviceUrl}/entries/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId, task }),
      });
      if (res.status === 409) {
        setStatus("an entry is already running");
        return;
      }
      if (!res.ok) throw new Error(`start failed: ${res.status} ${await res.text()}`);
      const entry = await res.json();
      runningEntry = { ...entry, source: "live" };
      await setSetting("running_entry", runningEntry);
      renderRunning();
      return;
    } catch (err) {
      console.error(err);
      // fall through to offline start below
    }
  }

  runningEntry = { ...baseEntryFields(), created_offline: true, source: "offline" };
  await setSetting("running_entry", runningEntry);
  renderRunning();
}

async function stopEntry() {
  if (!runningEntry) return;
  const entry = runningEntry;

  if (entry.source === "live") {
    try {
      const res = await fetch(`${serviceUrl}/entries/${entry.id}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`stop failed: ${res.status} ${await res.text()}`);
      runningEntry = null;
      await setSetting("running_entry", null);
      renderRunning();
      setStatus("stopped");
      return;
    } catch (err) {
      console.error(err);
      setStatus("couldn't reach service to stop — completing locally");
      // fall through: finish it locally and let /entries/sync reconcile it
    }
  }

  const endTime = new Date();
  const completed = {
    id: entry.id,
    client_id: entry.client_id,
    project_id: entry.project_id,
    task: entry.task,
    notes: entry.notes ?? null,
    start_time: entry.start_time,
    end_time: endTime.toISOString(),
    duration_minutes: (endTime - new Date(entry.start_time)) / 60000,
    created_offline: entry.created_offline ?? entry.source === "offline",
  };
  await queueEntry(completed);
  runningEntry = null;
  await setSetting("running_entry", null);
  renderRunning();
  trySync();
}

function renderRunning() {
  const disabled = !!runningEntry;
  els.startBtn.disabled = disabled;
  els.stopBtn.disabled = !disabled;
  els.projectId.disabled = disabled;
  els.running.textContent = runningEntry
    ? `Running: ${runningEntry.client_id} / ${runningEntry.project_id} — ${runningEntry.task} since ${new Date(runningEntry.start_time).toLocaleTimeString()}`
    : "Nothing running";
}

async function init() {
  els.projectId = document.getElementById("project-id");
  els.client = document.getElementById("client-display");
  els.task = document.getElementById("task");
  els.startBtn = document.getElementById("start-btn");
  els.stopBtn = document.getElementById("stop-btn");
  els.running = document.getElementById("running");
  els.status = document.getElementById("status");
  els.serviceUrl = document.getElementById("service-url");

  serviceUrl = await getSetting("service_url", DEFAULT_SERVICE_URL);
  els.serviceUrl.value = serviceUrl;
  els.serviceUrl.addEventListener("change", async () => {
    serviceUrl = els.serviceUrl.value.trim() || DEFAULT_SERVICE_URL;
    await setSetting("service_url", serviceUrl);
    refreshOptions();
    trySync();
  });

  els.projectId.addEventListener("change", updateClientDisplay);

  options = await getSetting("cached_options", []);
  renderProjectOptions();
  await refreshOptions();

  runningEntry = await getSetting("running_entry", null);
  renderRunning();

  els.startBtn.addEventListener("click", startEntry);
  els.stopBtn.addEventListener("click", stopEntry);

  // iOS Safari has no Background Sync API, so sync (and picking up newly
  // added vault projects) is driven by foreground signals (visibility/online)
  // plus a plain interval rather than relying on a background event.
  window.addEventListener("online", () => {
    refreshOptions();
    trySync();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshOptions();
      trySync();
    }
  });
  setInterval(() => {
    refreshOptions();
    trySync();
  }, SYNC_INTERVAL_MS);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./sw.js").catch(console.error);
  }
}

init();
