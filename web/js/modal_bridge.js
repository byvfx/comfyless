// Modal Cloud Bridge: while "Run on Cloud" is on, queued prompts go to the bridge's
// /modal_bridge/prompt route instead of /prompt. That route mirrors /prompt's request and
// response, so the frontend's own queueing, error display and progress tracking keep working.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { openHealth, openMissingModels, openModels, openSetup, stopNow } from "./setup_wizard.js";

const EXT = "ModalBridge";
const SETTING = {
  enabled: `${EXT}.Enabled`,
  gpu: `${EXT}.GPU`,
  idleMinutes: `${EXT}.IdleMinutes`,
};
const TOGGLE_CLASS = "modal-bridge-toggle";

// prompt_ids of cloud jobs that haven't finished, so Interrupt/Cancel go to the bridge.
const cloudJobs = new Set();
// The prompt that is executing right now, local or cloud, so an Interrupt without an id goes to it.
let runningPromptId = null;
// Last /modal_bridge/status answer; updated by modal_bridge.connection events.
let status = { connected: false, setup_done: true };

const toggleButton = {
  icon: "icon-[lucide--cloud]",
  label: "Local",
  tooltip: "Run on Cloud",
  class: TOGGLE_CLASS,
  onClick: () => toggleCloud(),
};

const setting = (id) => app.extensionManager.setting.get(id);

function toast(severity, summary, detail, life = 4000) {
  const t = app.extensionManager.toast;
  const method = { success: "success", info: "info", warn: "warning", error: "error" }[severity];
  // Newer frontends (1.5x) have toast.<level>(title, options); older ones only toast.add.
  if (method && typeof t[method] === "function") t[method](summary, { description: detail, duration: life });
  else t.add({ severity, summary, detail, life });
}

function refreshToggle() {
  const on = !!setting(SETTING.enabled);
  toggleButton.label = on ? `Cloud · ${setting(SETTING.gpu)}` : "Local";
  for (const el of document.querySelectorAll(`.${TOGGLE_CLASS}`)) {
    el.style.color = on ? "var(--p-primary-color, #3b82f6)" : "";
    el.style.opacity = on ? "1" : "0.6";
    el.title = on
      ? `Run on Cloud: ON (${setting(SETTING.gpu)}). Click to run locally.`
      : "Run on Cloud: OFF. Click to run on your Modal GPU.";
    const label = el.querySelector("span:last-child");
    if (label) label.textContent = toggleButton.label;
  }
}

function showSetup() {
  openSetup({
    gpu: setting(SETTING.gpu),
    scaledownS: Math.round(Number(setting(SETTING.idleMinutes)) * 60),
    onDone: () => refreshStatus(),
  });
}

function showModels() {
  openModels({ onChanged: () => app.refreshComboInNodes?.() });
}

async function stopCloud() {
  try {
    const s = await (await api.fetchApi("/modal_bridge/status")).json();
    if ((s.running || s.pending) && !window.confirm("Stop all cloud GPUs now? This cancels your running and queued cloud jobs.")) return;
    toast("success", "Cloud GPUs stopped", await stopNow(), 6000);
  } catch (e) {
    toast("error", "Couldn't stop cloud GPUs", e.message, 12000);
  }
}

function showHealth() {
  openHealth({ onFix: () => showSetup() });
}

async function refreshStatus() {
  try {
    status = await (await api.fetchApi("/modal_bridge/status")).json();
  } catch (e) {
    console.warn("[modal-bridge] status check failed", e);
  }
  return status;
}

async function toggleCloud() {
  const on = !setting(SETTING.enabled);
  if (on && !status.connected) await refreshStatus(); // it may have connected since startup
  if (on && !status.connected) {
    // Turning cloud on without a working connection: send the user to setup instead.
    if (status.broken) return toast("error", "Modal Cloud unavailable", status.error, 10000);
    toast("warn", "Modal Cloud isn't set up yet", status.error || "Finish setup to run on the cloud.", 6000);
    return showSetup();
  }
  await app.extensionManager.setting.set(SETTING.enabled, on);
  refreshToggle();
  toast("info", on ? "Run on Cloud: ON" : "Run on Cloud: OFF", on ? `Prompts run on Modal (${setting(SETTING.gpu)})` : "Prompts run on this computer", 2500);
}

function installFetchHook() {
  const originalFetchApi = api.fetchApi.bind(api);
  api.fetchApi = async function (route, options) {
    const method = (options?.method || "GET").toUpperCase();
    if (method === "POST" && route === "/prompt" && setting(SETTING.enabled)) {
      const body = JSON.parse(options.body);
      body.modal_bridge = {
        gpu: setting(SETTING.gpu),
        scaledown_s: Math.round(Number(setting(SETTING.idleMinutes)) * 60),
      };
      const res = await originalFetchApi("/modal_bridge/prompt", { ...options, body: JSON.stringify(body) });
      if (res.ok) {
        const { prompt_id } = await res.clone().json();
        if (prompt_id) cloudJobs.add(prompt_id);
      } else {
        // Models only on this computer: offer to upload them, next to ComfyUI's own error display.
        const missing = (await res.clone().json().catch(() => ({})))?.error?.extra_info?.modal_bridge_missing_models;
        if (missing?.length) {
          setTimeout(() => openMissingModels(missing, {
            onRun: () => app.extensionManager.command.execute("Comfy.QueuePrompt"),
          }), 0);
        }
      }
      return res;
    }
    if (method !== "POST" || !cloudJobs.size) return originalFetchApi(route, options);

    if (route === "/interrupt") {
      const promptId = (options?.body ? JSON.parse(options.body).prompt_id : undefined) || runningPromptId;
      if (cloudJobs.has(promptId)) return (await cancelCloud([promptId])) || new Response(null, { status: 200 });
      return originalFetchApi(route, options); // a local run, even while cloud jobs wait
    }
    // The queue panel's per-job cancel (frontend 1.5x).
    const cancel = /^\/jobs\/([^/]+)\/cancel$/.exec(route);
    if (cancel && cloudJobs.has(decodeURIComponent(cancel[1]))) {
      return (await cancelCloud([decodeURIComponent(cancel[1])])) || new Response(null, { status: 200 });
    }
    // Bulk cancel, and the older queue panel's delete/clear: cancel the cloud jobs, pass on the rest.
    if (route === "/jobs/cancel" || route === "/queue") {
      const body = JSON.parse(options.body);
      const ids = route === "/queue" ? body.delete : body.job_ids;
      if (route === "/queue" && body.clear) {
        const failed = await cancelCloud([...cloudJobs], true);
        if (failed) return failed;
      }
      if (Array.isArray(ids)) {
        const cloud = ids.filter((id) => cloudJobs.has(id));
        if (cloud.length) {
          const failed = await cancelCloud(cloud);
          if (failed) return failed;
          const rest = ids.filter((id) => !cloudJobs.has(id));
          if (!rest.length && !body.clear) return new Response(null, { status: 200 });
          options = { ...options, body: JSON.stringify(route === "/queue" ? { ...body, delete: rest } : { ...body, job_ids: rest }) };
        }
      }
    }
    return originalFetchApi(route, options);
  };

  // Returns the first failed bridge response, or null if every cancel went through.
  async function cancelCloud(ids, queuedOnly = false) {
    let failed = null;
    for (const prompt_id of ids) {
      const res = await originalFetchApi("/modal_bridge/interrupt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_id, queued_only: queuedOnly }),
      });
      if (!res.ok && !failed) failed = res;
    }
    if (failed) toast("error", "Cloud cancel failed", `${failed.status} ${failed.statusText}. The cloud job may still be running.`, 8000);
    return failed;
  }
}

function listenForStatus() {
  api.addEventListener("modal_bridge.status", ({ detail }) => {
    const summary = { queued: "Cloud: queued", running: "Cloud: running", uploaded: "Cloud: inputs", done: "Cloud: done", warning: "Cloud" }[detail.state] || "Cloud";
    toast(detail.severity || "info", summary, detail.detail, detail.state === "done" ? 3000 : 4000);
  });
  api.addEventListener("modal_bridge.connection", async ({ detail }) => {
    const was = status.connected;
    status = { ...status, connected: detail.connected, error: detail.error };
    if (detail.connected && !was) {
      await app.refreshComboInNodes?.(); // cloud models just became available
      toast("success", "Modal Cloud connected", "Turn on Run on Cloud next to the Run button.", 4000);
    }
  });
  // Model uploads and downloads keep going after the Cloud models window closes; say when each ends.
  const transferStates = new Map();
  api.addEventListener("modal_bridge.models", async ({ detail }) => {
    for (const t of detail.transfers || []) {
      const was = transferStates.get(t.id);
      transferStates.set(t.id, t.state);
      if (!was || was === t.state) continue;
      const what = `${t.folder}/${t.name}`;
      if (t.state === "done") {
        await app.refreshComboInNodes?.();
        toast("success", t.kind === "upload" ? "Uploaded to the cloud" : "Downloaded to the cloud", what, 5000);
      } else if (t.state === "error") {
        toast("error", t.kind === "upload" ? "Upload failed" : "Cloud download failed", `${what}: ${t.error}`, 10000);
      }
    }
  });
  api.addEventListener("execution_start", ({ detail }) => (runningPromptId = detail?.prompt_id ?? null));
  const finished = ({ detail }) => {
    cloudJobs.delete(detail?.prompt_id);
    if (detail?.prompt_id === runningPromptId) runningPromptId = null;
  };
  api.addEventListener("execution_success", finished);
  api.addEventListener("execution_error", finished);
  api.addEventListener("execution_interrupted", finished);
}

app.registerExtension({
  name: "byvfx.ModalBridge",
  settings: [
    {
      id: SETTING.enabled,
      category: ["Modal Cloud", "Execution", "Enabled"],
      name: "Run on Cloud",
      tooltip: "Send queued workflows to a GPU in your Modal account instead of running them here.",
      type: "boolean",
      defaultValue: false,
      onChange: () => refreshToggle(),
    },
    {
      id: SETTING.gpu,
      category: ["Modal Cloud", "Execution", "GPU"],
      name: "Cloud GPU",
      tooltip: "L40S is the cheapest; H100 is about twice the price and faster. Large video models may need 80 GB (A100-80GB or H100).",
      type: "combo",
      options: ["L40S", "A100-80GB", "H100"],
      defaultValue: "L40S",
      onChange: () => refreshToggle(),
    },
    {
      id: SETTING.idleMinutes,
      category: ["Modal Cloud", "Execution", "Idle"],
      name: "Keep GPU warm for (minutes)",
      tooltip: "After a run the GPU stays on this long so the next run skips the ~45 s cold start. You pay for this idle time. Changing it starts a fresh GPU.",
      type: "slider",
      attrs: { min: 1, max: 20, step: 1 },
      defaultValue: 5,
    },
  ],
  commands: [
    { id: "ModalBridge.Toggle", label: "Toggle Run on Cloud", icon: "pi pi-cloud", function: toggleCloud },
    { id: "ModalBridge.Setup", label: "Setup (sign in, deploy, test)", icon: "pi pi-cog", function: showSetup },
    { id: "ModalBridge.Health", label: "Check setup and spending", icon: "pi pi-heart", function: showHealth },
    { id: "ModalBridge.Models", label: "Cloud models", icon: "pi pi-database", function: showModels },
    { id: "ModalBridge.StopNow", label: "Stop cloud GPUs now", icon: "pi pi-power-off", function: stopCloud },
    {
      id: "ModalBridge.SyncModels",
      label: "Refresh cloud model list",
      icon: "pi pi-refresh",
      function: async () => {
        const res = await api.fetchApi("/modal_bridge/models/sync", { method: "POST" });
        const body = await res.json();
        if (!res.ok) return toast("error", "Cloud models", body.error);
        await app.refreshComboInNodes?.();
        toast("success", "Cloud models", `${body.models} models in the cloud`);
      },
    },
  ],
  menuCommands: [
    {
      path: ["Extensions", "Modal Cloud"],
      commands: ["ModalBridge.Toggle", "ModalBridge.Setup", "ModalBridge.Health", "ModalBridge.Models", "ModalBridge.SyncModels", "ModalBridge.StopNow"],
    },
  ],
  actionBarButtons: [toggleButton],
  async setup() {
    installFetchHook();
    listenForStatus();
    // The action bar renders after setup; refresh once it exists.
    requestAnimationFrame(() => setTimeout(refreshToggle, 500));
    await refreshStatus();
    if (status.broken) {
      toast("error", "Modal Cloud unavailable", status.error, 12000);
    } else if (!status.setup_done && !status.connected) {
      showSetup(); // first run after install
    } else if (!status.connected && setting(SETTING.enabled)) {
      // The bridge connects in the background at startup; only complain if it's still down shortly after.
      setTimeout(async () => {
        if (!(await refreshStatus()).connected) toast("warn", "Modal Cloud not connected", status.error, 10000);
      }, 8000);
    }
  },
});
