// The Modal Cloud sidebar tab: everything Comfyless does, one click away, without the
// Extensions menu. Imported by modal_bridge.js; this module registers nothing on its own.
import { ensureStyle, h } from "./setup_wizard.js";

const POLL_MS = 5000;

// Renders into the sidebar's container and keeps itself current while the tab is open.
// state() returns { status, cloudOn, gpu }; actions has the same functions as the menu commands.
export function renderCloudPanel(container, { state, refreshStatus, actions }) {
  ensureStyle();
  const statusLine = h("span");
  const dot = h("span", { class: "mb-dot" });
  const jobsLine = h("div", { class: "mb-muted" });
  const toggle = h("button", { class: "mb-btn", onclick: () => actions.toggle() });
  const gpu = h("select", { class: "mb-select", "aria-label": "Cloud GPU", onchange: () => actions.setGpu(gpu.value) });
  const button = (label, onclick, cls = "mb-btn secondary") => h("button", { class: cls, onclick }, label);

  container.replaceChildren(h("div", { class: "mb-panel" },
    h("h2", {}, "Modal Cloud"),
    h("div", { class: "mb-status" }, dot, statusLine),
    jobsLine,
    toggle,
    h("div", { class: "mb-label" }, "Cloud GPU"),
    gpu,
    h("div", { class: "mb-label" }, "Setup"),
    button("Set up: sign in, deploy, test", actions.setup),
    button("Check setup and spending", actions.health),
    h("div", { class: "mb-label" }, "Models"),
    button("Cloud models", actions.models),
    button("Refresh cloud model list", actions.syncModels),
    h("div", { class: "mb-label" }, "Spending"),
    button("Stop cloud GPUs now", actions.stop, "mb-btn danger"),
    h("div", { class: "mb-muted" }, "How long a GPU stays warm after a run, and the rest: Settings → Modal Cloud."),
  ));

  function update() {
    const { status, cloudOn, gpu: current } = state();
    dot.className = `mb-dot${status.connected ? " on" : status.broken || status.error ? " bad" : ""}`;
    statusLine.textContent = status.connected
      ? "Connected to your Modal app"
      : status.unreachable ? status.error
      : !status.setup_done ? "Not set up yet. Start with Set up below."
      : status.error || "Not connected";
    jobsLine.textContent = status.running
      ? `A cloud job is running${status.pending ? `, ${status.pending} queued` : ""}.`
      : status.pending ? `${status.pending} cloud jobs queued.` : "";
    jobsLine.hidden = !jobsLine.textContent;
    toggle.textContent = cloudOn ? `Run on Cloud: ON (${current})` : "Run on Cloud: OFF";
    toggle.className = cloudOn ? "mb-btn" : "mb-btn secondary";
    toggle.title = cloudOn ? "Click to run on this computer instead." : "Click to run on your Modal GPU.";
    const gpus = status.gpus?.length ? [...status.gpus] : [];
    if (!gpus.includes(current)) gpus.push(current);
    if ([...gpu.options].map((o) => o.value).join() !== gpus.join()) gpu.replaceChildren(...gpus.map((g) => h("option", { value: g }, g)));
    gpu.value = current;
  }

  update();
  refreshStatus().then(update);
  const timer = setInterval(() => refreshStatus().then(update), POLL_MS);
  return { update, dispose: () => clearInterval(timer) };
}
