// Modal Cloud setup wizard and "Check setup" panel (spec §7.2, §7.5). Everything a user must
// configure is done here, with no terminal: sign in, optional keys, deploy, test run.
// Imported by modal_bridge.js; this module registers nothing on its own.
import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

const BUDGETS_URL = "https://modal.com/docs/guide/budgets";
const STYLE_ID = "modal-bridge-style";

const CSS = `
.mb-dialog { background: var(--comfy-menu-bg, #202020); color: var(--fg-color, #ddd); border: 1px solid var(--border-color, #444); border-radius: 10px;
  padding: 0; margin: auto; width: min(640px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto;
  box-shadow: 0 10px 40px rgba(0,0,0,.5); font-size: 14px; line-height: 1.45; }
.mb-dialog::backdrop { background: rgba(0,0,0,.55); }
.mb-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--border-color, #444); }
.mb-head h2 { margin: 0; font-size: 17px; font-weight: 600; }
.mb-x { background: none; border: 0; color: inherit; font-size: 20px; cursor: pointer; opacity: .7; }
.mb-body { padding: 6px 18px 16px; }
.mb-step { border-bottom: 1px solid var(--border-color, #333); padding: 14px 0; }
.mb-step:last-child { border-bottom: 0; }
.mb-step h3 { margin: 0 0 6px; font-size: 15px; font-weight: 600; display: flex; gap: 8px; align-items: center; }
.mb-num { display: inline-flex; width: 22px; height: 22px; border-radius: 50%; align-items: center; justify-content: center; font-size: 12px;
  background: var(--border-color, #444); }
.mb-num.done { background: #16a34a; color: #fff; }
.mb-muted { opacity: .75; margin: 4px 0 8px; }
.mb-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.mb-btn { background: var(--p-primary-color, #3b82f6); color: #fff; border: 0; border-radius: 6px; padding: 7px 14px; cursor: pointer; font-size: 14px; }
.mb-btn.secondary { background: transparent; color: inherit; border: 1px solid var(--border-color, #555); }
.mb-btn:disabled { opacity: .5; cursor: default; }
.mb-input { flex: 1 1 220px; background: var(--comfy-input-bg, #111); color: inherit; border: 1px solid var(--border-color, #555); border-radius: 6px; padding: 6px 8px; }
.mb-log { background: #0b0b0b; color: #cfcfcf; font: 12px/1.4 ui-monospace, Consolas, monospace; padding: 8px; border-radius: 6px; max-height: 180px;
  overflow: auto; white-space: pre-wrap; word-break: break-all; margin-top: 8px; }
.mb-ok { color: #22c55e; } .mb-warn { color: #f59e0b; } .mb-fail { color: #ef4444; }
.mb-code { font: 600 15px ui-monospace, Consolas, monospace; letter-spacing: 1px; }
.mb-check { display: grid; grid-template-columns: 22px 1fr auto; gap: 4px 8px; padding: 8px 0; border-bottom: 1px solid var(--border-color, #333); align-items: start; }
.mb-check:last-child { border-bottom: 0; }
.mb-test-img { width: 64px; height: 64px; border-radius: 6px; border: 1px solid var(--border-color, #555); }
.mb-dialog a { color: var(--p-primary-color, #60a5fa); }
.mb-list { margin: 4px 0 8px; padding-left: 20px; font-size: 13px; }
.mb-select { background: var(--comfy-input-bg, #111); color: inherit; border: 1px solid var(--border-color, #555); border-radius: 6px; padding: 6px 8px; }
.mb-scroll { max-height: 240px; overflow: auto; margin-top: 6px; }
.mb-item { display: grid; grid-template-columns: 1fr auto auto; gap: 2px 10px; align-items: center; padding: 5px 0; border-bottom: 1px solid var(--border-color, #333); font-size: 13px; }
.mb-item:last-child { border-bottom: 0; }
.mb-item .mb-name { word-break: break-all; }
.mb-item .mb-btn { padding: 3px 10px; font-size: 13px; }
.mb-size { opacity: .7; white-space: nowrap; font-variant-numeric: tabular-nums; }
.mb-bar { grid-column: 1 / -1; height: 6px; border-radius: 3px; background: var(--border-color, #444); overflow: hidden; }
.mb-bar > div { height: 100%; background: var(--p-primary-color, #3b82f6); transition: width .4s; }
.mb-bar.indeterminate > div { width: 30% !important; animation: mb-slide 1.2s ease-in-out infinite; }
@keyframes mb-slide { from { margin-left: -30%; } to { margin-left: 100%; } }
.mb-panel { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; font-size: 14px; line-height: 1.45; color: var(--fg-color, #ddd); }
.mb-panel h2 { margin: 0 0 2px; font-size: 16px; font-weight: 600; }
.mb-panel .mb-btn { width: 100%; text-align: left; }
.mb-panel .mb-select { width: 100%; }
.mb-label { font-size: 12px; opacity: .7; margin-top: 8px; }
.mb-status { display: flex; gap: 8px; align-items: baseline; }
.mb-dot { flex: none; width: 9px; height: 9px; border-radius: 50%; background: #6b7280; }
.mb-dot.on { background: #22c55e; } .mb-dot.bad { background: #ef4444; }
.mb-btn.danger { background: transparent; color: #ef4444; border: 1px solid #ef4444; }
.mb-step.mb-featured { border: 1px solid var(--p-primary-color, #3b82f6); border-radius: 10px; padding: 12px 14px; margin: 0 0 14px;
  background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 8%, transparent); }
.mb-featured h3 .mb-badge { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .6px; padding: 2px 7px; border-radius: 999px;
  background: var(--p-primary-color, #3b82f6); color: #fff; }
.mb-packs > summary { cursor: pointer; font-size: 13px; font-weight: 600; padding: 6px 0; list-style: none; display: flex; gap: 8px; align-items: center; }
.mb-packs > summary::-webkit-details-marker { display: none; }
.mb-packs > summary::before { content: "▸"; opacity: .7; transition: transform .15s; }
.mb-packs[open] > summary::before { transform: rotate(90deg); }
.mb-packs > summary .mb-muted { font-weight: 400; margin: 0; }
.mb-group { font-size: 11px; opacity: .6; margin: 12px 0 2px; text-transform: uppercase; letter-spacing: .6px; }
.mb-pack { padding: 10px 12px; margin-top: 6px; border: 1px solid var(--border-color, #333); border-radius: 8px; background: var(--comfy-input-bg, rgba(0,0,0,.18)); font-size: 13px; }
.mb-pack.have { border-color: color-mix(in srgb, #22c55e 55%, var(--border-color, #333)); }
.mb-pack-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.mb-pack-head strong { font-size: 14px; }
.mb-pack .mb-muted { margin: 2px 0 0; }
.mb-pack .mb-row { margin-top: 6px; }
.mb-pack .mb-btn { padding: 3px 10px; font-size: 13px; }
.mb-pack details { font-size: 12px; opacity: .8; flex: 1 1 100%; }
.mb-pack details > summary { cursor: pointer; }
.mb-pack .mb-files { margin: 4px 0 0; padding-left: 18px; }
`;

// Build DOM without innerHTML, so server text (errors, log lines) is never parsed as HTML.
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

async function call(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await api.fetchApi(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

export function ensureStyle() {
  if (!document.getElementById(STYLE_ID)) document.head.append(h("style", { id: STYLE_ID }, CSS));
}

function dialog(title, onClose) {
  ensureStyle();
  const body = h("div", { class: "mb-body" });
  // A native modal dialog: it takes focus, keeps Tab and screen readers inside, and Escape closes it.
  const el = h("dialog", { class: "mb-dialog", "aria-label": title },
    h("div", { class: "mb-head" }, h("h2", {}, title), h("button", { class: "mb-x", title: "Close", "aria-label": "Close", onclick: () => close() }, "×")),
    body);
  const returnFocus = document.activeElement;
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    el.close();
    el.remove();
    if (returnFocus instanceof HTMLElement && returnFocus.isConnected) returnFocus.focus();
    onClose?.();
  }
  el.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  el.addEventListener("mousedown", (e) => {  // a click on the backdrop, outside the dialog's box
    const r = el.getBoundingClientRect();
    if (e.target === el && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) close();
  });
  document.body.append(el);
  el.showModal();
  return { body, close };
}

// What a deploy mirrors from this computer: ComfyUI's version and each custom node pack's source.
function nodesList(nodes) {
  if (!nodes) return null;
  const packs = nodes.packs || [];
  return h("div", { class: "mb-muted" },
    h("p", { class: "mb-muted" }, packs.length
      ? `Mirrors ComfyUI ${nodes.comfyui_version || ""} and ${packs.length} custom node pack(s) from this computer:`
      : `Mirrors ComfyUI ${nodes.comfyui_version || ""}. No custom node packs to mirror.`),
    packs.length ? h("ul", { class: "mb-list" }, packs.map((p) => h("li", {}, `${p.name}: ${p.label}`))) : null,
    (nodes.skipped || []).length
      ? h("p", { class: "mb-muted" }, "Not mirrored: " + nodes.skipped.map((s) => `${s.name} (${s.reason})`).join("; "))
      : null);
}

function step(num, done, title, ...content) {
  return h("div", { class: "mb-step" }, h("h3", {}, h("span", { class: `mb-num${done ? " done" : ""}` }, done ? "✓" : num), title), ...content);
}

// -- setup wizard ------------------------------------------------------------------------

let open = null; // the wizard, while shown

export async function openSetup({ gpu, scaledownS, onDone } = {}) {
  if (open) return;
  const ui = { state: null, busy: {}, error: {}, test: null, loginPoll: null };
  const onDeploy = ({ detail }) => {
    if (!ui.state) return;
    if (detail.line) ui.state.deploy.log.push(detail.line);
    if (detail.state) Object.assign(ui.state.deploy, { state: detail.state, error: detail.error });
    render();
  };
  const onConnection = () => refresh();
  api.addEventListener("modal_bridge.deploy", onDeploy);
  api.addEventListener("modal_bridge.connection", onConnection);
  const d = dialog("Modal Cloud setup", () => {
    api.removeEventListener("modal_bridge.deploy", onDeploy);
    api.removeEventListener("modal_bridge.connection", onConnection);
    clearInterval(ui.loginPoll);
    open = null;
    onDone?.();
  });
  open = d;

  async function refresh() {
    try {
      ui.state = await call("/modal_bridge/setup/state");
      ui.error.load = null;
    } catch (e) {
      ui.error.load = e.message;
    }
    render();
  }

  async function act(key, fn) {
    ui.busy[key] = true;
    ui.error[key] = null;
    render();
    try {
      await fn();
    } catch (e) {
      ui.error[key] = e.message;
    }
    ui.busy[key] = false;
    await refresh();
  }

  const signIn = () =>
    act("login", async () => {
      const login = await call("/modal_bridge/setup/login", {});
      if (login.url) window.open(login.url, "_blank", "noopener");
      clearInterval(ui.loginPoll);
      ui.loginPoll = setInterval(async () => {
        await refresh();
        const s = ui.state?.login?.state;
        if (!["starting", "waiting", "switching"].includes(s)) clearInterval(ui.loginPoll);
      }, 2000);
    });

  function render() {
    const s = ui.state;
    d.body.replaceChildren();
    if (!s) {
      d.body.append(h("p", { class: ui.error.load ? "mb-fail" : "mb-muted" }, ui.error.load || "Loading..."));
      return;
    }
    const login = s.login || {};
    const signedIn = s.signed_in || login.state === "done";
    const deployed = s.connected || s.deploy.state === "done";
    const err = (key) => ui.error[key] && h("p", { class: "mb-fail" }, ui.error[key]);

    // 1. Sign in
    const waiting = ["starting", "waiting", "switching"].includes(login.state);
    d.body.append(step(1, signedIn && !waiting, "Connect your Modal account",
      h("p", { class: "mb-muted" },
        "Modal rents out cloud GPUs by the second. Your workflows run in your own Modal account, and Modal bills you directly: " +
        "about $2 an hour while a GPU is on with the default L40S. Images run back to back cost about a cent each; a single image after a break " +
        "costs more (about 20 cents with the default settings), because the GPU stays on for a few minutes afterwards. Modal's free plan includes monthly credit."),
      login.state === "switching"
        ? h("p", { class: "mb-muted" }, "Approved. Switching to the new account as soon as your running cloud jobs finish...")
        : waiting
        ? h("div", {},
            h("p", {}, "Approve the sign-in in the browser tab that just opened. ",
              login.url ? h("a", { href: login.url, target: "_blank", rel: "noopener" }, "Open it again") : null),
            login.code ? h("p", {}, "If Modal asks for a code, enter ", h("span", { class: "mb-code" }, login.code)) : null,
            h("p", { class: "mb-muted" }, "Waiting for approval..."))
        : h("div", { class: "mb-row" },
            signedIn ? h("span", { class: "mb-ok" }, `Signed in${s.workspace ? ` to workspace ${s.workspace}` : ""}.`) : null,
            h("button", { class: signedIn ? "mb-btn secondary" : "mb-btn", disabled: ui.busy.login, onclick: signIn },
              signedIn ? "Use a different account" : "Sign in to Modal"),
            signedIn ? null : h("a", { href: "https://modal.com/signup", target: "_blank", rel: "noopener" }, "No account yet? Sign up free")),
      login.state === "error" ? h("p", { class: "mb-fail" }, login.error) : null,
      err("login")));

    // 2. Optional keys
    const hf = h("input", { class: "mb-input", type: "password", placeholder: "Hugging Face token (hf_...)", autocomplete: "off" });
    const cv = h("input", { class: "mb-input", type: "password", placeholder: "Civitai API key", autocomplete: "off" });
    const saved = s.secret_keys || [];
    d.body.append(step(2, saved.length > 0, "Download keys (optional)",
      h("p", { class: "mb-muted" },
        "Only needed to download gated Hugging Face models or Civitai models straight into your cloud. " +
        "Keys are stored in your Modal account as a Secret, never on this computer."),
      saved.length ? h("p", { class: "mb-ok" }, `Saved: ${saved.map((k) => (k === "HF_TOKEN" ? "Hugging Face" : "Civitai")).join(", ")}`) : null,
      h("div", { class: "mb-row" }, hf), h("div", { class: "mb-row" }, cv),
      h("div", { class: "mb-row" }, h("button", {
        class: "mb-btn secondary", disabled: !signedIn || ui.busy.keys,
        onclick: () => act("keys", () => call("/modal_bridge/setup/secrets", { HF_TOKEN: hf.value, CIVITAI_TOKEN: cv.value })),
      }, ui.busy.keys ? "Saving..." : "Save keys")),
      err("keys")));

    // 3. Deploy
    const dep = s.deploy;
    const running = dep.state === "running";
    const logBox = dep.log.length ? h("div", { class: "mb-log" }, dep.log.slice(-60).join("\n")) : null;
    d.body.append(step(3, deployed && !running, "Install the cloud worker",
      h("p", { class: "mb-muted" },
        "Sets up ComfyUI in your Modal account. The first time builds the cloud image and takes about 3 to 6 minutes. " +
        "Run it again after updating this extension or adding custom nodes."),
      nodesList(s.nodes),
      h("div", { class: "mb-row" },
        h("button", { class: deployed ? "mb-btn secondary" : "mb-btn", disabled: !signedIn || running || ui.busy.deploy,
          onclick: () => act("deploy", () => call("/modal_bridge/setup/deploy", {})) },
          running ? "Deploying..." : deployed ? "Deploy again" : "Deploy"),
        dep.state === "done" ? h("span", { class: "mb-ok" }, "Deployed.") : null),
      dep.state === "error" ? h("p", { class: "mb-fail" }, dep.error) : null,
      err("deploy"), logBox));
    if (logBox) requestAnimationFrame(() => (logBox.scrollTop = logBox.scrollHeight));

    // 4. Test
    const t = ui.test;
    d.body.append(step(4, !!t, "Run a test",
      h("p", { class: "mb-muted" },
        `Starts an ${gpu || "L40S"} GPU and runs a tiny workflow. About a minute the first time; costs about a cent.`),
      h("div", { class: "mb-row" },
        h("button", { class: t ? "mb-btn secondary" : "mb-btn", disabled: !deployed || running || ui.busy.test,
          onclick: () => act("test", async () => (ui.test = await call("/modal_bridge/setup/test", { gpu, scaledown_s: scaledownS }))) },
          ui.busy.test ? "Running on the cloud GPU..." : t ? "Run again" : "Run test"),
        t?.image ? h("img", { class: "mb-test-img", src: t.image, alt: "test output" }) : null,
        t ? h("span", { class: "mb-ok" }, `Worked: ${t.gpu}, ${t.seconds}s${t.cold ? " including GPU start-up" : ""}, about ${t.usd_text}. ${t.idle_note ?? ""}`) : null),
      err("test")));

    d.body.append(h("div", { class: "mb-step" },
      h("p", { class: "mb-muted" },
        "Tip: set a monthly spending limit in the Modal dashboard (Usage & Billing) so a forgotten GPU can't run up a bill. ",
        h("a", { href: BUDGETS_URL, target: "_blank", rel: "noopener" }, "How to set a budget")),
      h("div", { class: "mb-row" },
        h("button", { class: "mb-btn", onclick: async () => {
          await call("/modal_bridge/setup/finish", {}).catch(() => {});
          d.close();
        } }, t ? "Finish" : "Close"))));
  }

  render();
  await refresh();
}

// -- check setup -------------------------------------------------------------------------

const ICON = { ok: ["✓", "mb-ok"], warn: ["!", "mb-warn"], fail: ["✗", "mb-fail"] };

// Stop paying now (#22): cancels cloud jobs and downloads, then switches off this app's containers.
export async function stopNow() {
  const r = await call("/modal_bridge/stop_now", {});
  if (!r.jobs && !r.downloads && !r.containers) return "Nothing was running in the cloud.";
  const parts = [];
  if (r.jobs) parts.push(`${r.jobs} cloud job(s) cancelled`);
  if (r.downloads) parts.push(`${r.downloads} cloud download(s) stopped`);
  if (r.containers) parts.push(`${r.containers} cloud container(s) switched off`);
  const text = parts.join(", ");
  return text[0].toUpperCase() + text.slice(1) + ".";
}

export async function openHealth({ onFix } = {}) {
  const d = dialog("Modal Cloud: check setup");
  let stopped = null; // the result of Stop now, shown above the refreshed checks

  // Two clicks, since it cancels running jobs.
  function stopButton() {
    let armed = false;
    const btn = h("button", { class: "mb-btn secondary", title: "Switch off every cloud GPU of this app now and cancel running cloud jobs" }, "Stop now");
    btn.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        btn.className = "mb-btn";
        btn.textContent = "Really stop? Cancels running jobs";
        return;
      }
      btn.disabled = true;
      btn.textContent = "Stopping...";
      try {
        stopped = { ok: true, text: await stopNow() };
      } catch (e) {
        stopped = { ok: false, text: e.message };
      }
      await load();
    });
    return btn;
  }

  async function load() {
    d.body.replaceChildren(h("p", { class: "mb-muted" }, "Checking..."));
    let health;
    try {
      health = await call("/modal_bridge/health");
    } catch (e) {
      d.body.replaceChildren(h("p", { class: "mb-fail" }, e.message));
      return;
    }
    const rows = health.checks.map((c) => {
      const [icon, cls] = ICON[c.status] || ICON.warn;
      const fix = c.action === "stop" ? stopButton()
        : c.action && h("button", { class: "mb-btn secondary", onclick: () => { d.close(); onFix?.(c.action); } },
          c.action === "login" ? "Sign in" : "Deploy");
      return h("div", { class: "mb-check" }, h("span", { class: cls }, icon),
        h("div", {}, h("div", {}, c.label), h("div", { class: "mb-muted" }, c.detail)), fix || h("span"));
    });
    const sp = health.spend || {};
    const spend = h("div", { class: "mb-step" }, h("h3", {}, "Spending"),
      sp.month_text != null
        ? h("p", {}, `Modal billing for this app: ${sp.today_text} today, ${sp.month_text} this month (UTC). Modal's figures can lag about an hour.`)
        : h("p", { class: "mb-muted" }, "Modal's billing report isn't available right now."),
      h("p", { class: "mb-muted" },
        `Estimated GPU cost of ${sp.estimated?.runs ?? 0} cloud run(s) from this computer, including the time the GPU stays on ` +
        `after each run: ${sp.estimated_text ?? "$0.00"}. Modal's figures above are the real ones.`),
      h("div", { class: "mb-row" },
        stopButton(),
        h("span", { class: "mb-muted" }, "Stop paying now: switches off every cloud GPU of this app at once.")),
      h("p", { class: "mb-muted" }, h("a", { href: BUDGETS_URL, target: "_blank", rel: "noopener" }, "Set a spending limit in Modal")));
    const notice = stopped && h("div", { class: "mb-step" }, h("p", { class: stopped.ok ? "mb-ok" : "mb-fail" }, stopped.text));
    d.body.replaceChildren(...[notice, h("div", { class: "mb-step" }, ...rows), spend].filter(Boolean));
  }

  await load();
}

// -- cloud models (spec §6.5, #21) -----------------------------------------------------------

export function formatBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i >= 3 ? 2 : i ? 1 : 0)} ${units[i]}`;
}

const STATE_TEXT = { starting: "Starting...", preparing: "Preparing (reading the file)...", cancelling: "Stopping...",
  done: "Done", cancelled: "Stopped" };
const ACTIVE = ["starting", "preparing", "running", "cancelling"];
const LIST_LIMIT = 300;

// replaceChildren() would add "null" as text for the optional parts.
const fill = (el, ...children) => el.replaceChildren(...children.flat().filter((c) => c != null && c !== false && c !== ""));

// A transfer's status text and, while it runs, its progress bar.
function transferView(t) {
  const known = t.total > 0;
  const pct = known ? Math.min(100, (100 * t.done) / t.total) : 0;
  const active = ACTIVE.includes(t.state);
  const finishing = t.state === "running" && known && t.done >= t.total; // sent; Modal is saving it
  const status = t.state === "error" ? h("span", { class: "mb-fail" }, t.error)
    : finishing ? h("span", { class: "mb-muted" }, "Finishing...")
    : t.state === "running" ? h("span", { class: "mb-size" }, known ? `${formatBytes(t.done)} of ${formatBytes(t.total)}` : formatBytes(t.done))
    : h("span", { class: t.state === "done" ? "mb-ok" : "mb-muted" }, STATE_TEXT[t.state] ?? t.state);
  const bar = active
    ? h("div", { class: `mb-bar${known && t.state === "running" && !finishing ? "" : " indeterminate"}` }, h("div", { style: `width: ${pct}%` }))
    : null;
  return { status, bar, active };
}

let modelsOpen = null;

export async function openModels({ onChanged } = {}) {
  if (modelsOpen) return;
  const ui = { list: null, error: null, busy: {}, actionError: null, confirm: null, filter: "",
    packs: null, packsError: null, packsOpen: true, packMsg: null };
  const onTransfers = ({ detail }) => {
    if (!ui.list) return;
    const before = new Map((ui.list.transfers || []).map((t) => [t.id, t.state]));
    ui.list.transfers = detail.transfers || [];
    renderTransfers();
    renderLists();
    renderPacks();
    if (ui.list.transfers.some((t) => t.state === "done" && before.has(t.id) && before.get(t.id) !== "done")) load();
  };
  api.addEventListener("modal_bridge.models", onTransfers);
  const d = dialog("Cloud models", () => {
    api.removeEventListener("modal_bridge.models", onTransfers);
    modelsOpen = null;
  });
  modelsOpen = d;

  // Inputs are built once so typing survives the re-renders that progress events cause.
  const url = h("input", { class: "mb-input", placeholder: "Hugging Face file link or Civitai model page", autocomplete: "off",
    "aria-label": "Model link", onkeydown: (e) => e.key === "Enter" && add() });
  const folder = h("select", { class: "mb-select", "aria-label": "Model folder" });
  const filename = h("input", { class: "mb-input", placeholder: "File name (optional)", autocomplete: "off", "aria-label": "File name" });
  const addBtn = h("button", { class: "mb-btn", onclick: () => add() }, "Download to cloud");
  const addMsg = h("div");
  const filter = h("input", { class: "mb-input", placeholder: "Filter models", autocomplete: "off", "aria-label": "Filter models",
    oninput: () => { ui.filter = filter.value.trim().toLowerCase(); renderLists(); } });
  const packsBox = h("div");
  const transfersBox = h("div");
  const cloudBox = h("div");
  const localBox = h("div");
  const topMsg = h("div");

  d.body.append(
    topMsg,
    packsBox,
    h("div", { class: "mb-step" }, h("h3", {}, "Add a model from a link"),
      h("p", { class: "mb-muted" },
        "Paste a Hugging Face file link or a Civitai model page. Your Modal account downloads it directly, so nothing passes through this computer. " +
        "Gated or Civitai models may need a key (Setup, step 2)."),
      h("div", { class: "mb-row" }, url),
      h("div", { class: "mb-row" }, folder, filename, addBtn),
      addMsg),
    transfersBox,
    h("div", { class: "mb-step" }, h("div", { class: "mb-row" }, filter), cloudBox),
    h("div", { class: "mb-step" }, localBox));

  async function load() {
    const packs = call("/modal_bridge/models/packs").then((r) => { ui.packs = r.packs; ui.packsError = null; }, (e) => { ui.packsError = e.message; });
    try {
      ui.list = await call("/modal_bridge/models");
      ui.error = null;
    } catch (e) {
      ui.error = e.message;
    }
    await packs;
    render();
  }

  async function act(key, fn) {
    ui.busy[key] = true;
    ui.actionError = null;
    render();
    try {
      await fn();
    } catch (e) {
      ui.actionError = e.message;
    }
    ui.busy[key] = false;
    await load();
    onChanged?.();
  }

  function add() {
    if (!url.value.trim() || ui.busy.add) return;
    act("add", async () => {
      await call("/modal_bridge/models/download", { url: url.value.trim(), folder: folder.value, filename: filename.value.trim() });
      url.value = "";
      filename.value = "";
    });
  }

  const matches = (m) => !ui.filter || `${m.folder}/${m.name}`.toLowerCase().includes(ui.filter);

  function render() {
    fill(topMsg, ui.error ? h("p", { class: "mb-fail" }, ui.error) : ui.list ? "" : h("p", { class: "mb-muted" }, "Loading..."));
    if (ui.list && !folder.options.length) {
      for (const f of ui.list.folders) folder.append(h("option", { value: f }, f));
    }
    addBtn.disabled = !ui.list || !!ui.busy.add;
    addBtn.textContent = ui.busy.add ? "Starting..." : "Download to cloud";
    fill(addMsg, ui.actionError ? h("p", { class: "mb-fail" }, ui.actionError) : "");
    renderPacks();
    renderTransfers();
    renderLists();
  }

  // -- starter packs: curated open-weight model sets with the ComfyUI templates that use them --

  const PACK_INTRO = "Open-weight models that run on your Modal GPU, with ComfyUI's own templates for them. Add a pack, then open one " +
    "of its workflows: the model names already match the files in your cloud. Load your own image in the Load Image nodes.";

  function packStatus(pack) {
    const active = new Set((ui.list?.transfers || []).filter((t) => ACTIVE.includes(t.state)).map((t) => `${t.folder}/${t.name}`));
    const have = pack.files.filter((f) => f.have).length;
    const transferring = pack.files.some((f) => !f.have && (f.transferring || active.has(f.path)));
    return { have, transferring, complete: have === pack.files.length };
  }

  function renderPacks() {
    if (!ui.packs && !ui.packsError) return;
    const groups = [];
    for (const p of ui.packs || []) {
      const g = groups.find((x) => x.name === p.group) || groups[groups.push({ name: p.group, packs: [] }) - 1];
      g.packs.push(p);
    }
    const inCloud = (ui.packs || []).filter((p) => packStatus(p).complete).length;
    const details = h("details", { class: "mb-packs", ontoggle: (e) => { ui.packsOpen = e.target.open; } },
      h("summary", {}, h("span", { style: "white-space: nowrap" }, ui.packs ? `${ui.packs.length} packs` : "Starter packs"),
        h("span", { class: "mb-muted" }, ui.packs ? `${inCloud ? `${inCloud} in your cloud · ` : ""}Qwen Image, Flux Kontext, SDXL, depth and normal passes, mattes, upscaling, Wan video` : "")),
      ui.packsError ? h("p", { class: "mb-fail" }, ui.packsError) : null,
      ui.packMsg ? h("p", { class: "mb-fail" }, ui.packMsg) : null,
      groups.flatMap((g) => [h("div", { class: "mb-group" }, g.name), ...g.packs.map(packView)]));
    if (ui.packsOpen) details.open = true;
    fill(packsBox, h("div", { class: "mb-step mb-featured" },
      h("h3", {}, "Starter packs", h("span", { class: "mb-badge" }, "Start here")),
      h("p", { class: "mb-muted" }, PACK_INTRO), details));
  }

  function packView(pack) {
    const { have, transferring, complete } = packStatus(pack);
    const status = complete ? h("span", { class: "mb-ok" }, "In your cloud")
      : transferring ? h("span", { class: "mb-muted" }, "Downloading...")
      : have ? h("span", { class: "mb-muted" }, `${have} of ${pack.files.length} files in your cloud`) : null;
    const addLabel = complete ? "Added" : transferring ? "Downloading..." : have ? `Add the rest (${formatBytes(pack.missing_size)})` : `Add to cloud (${formatBytes(pack.size)})`;
    return h("div", { class: complete ? "mb-pack have" : "mb-pack" },
      h("div", { class: "mb-pack-head" }, h("strong", {}, pack.title), h("span", { class: "mb-size" }, `${formatBytes(pack.size)} · ${pack.gpu}`), status),
      h("div", { class: "mb-muted" }, pack.blurb),
      h("div", { class: "mb-row" },
        h("button", { class: complete ? "mb-btn secondary" : "mb-btn", disabled: complete || transferring || !!ui.busy[`pack-${pack.id}`],
          onclick: () => act(`pack-${pack.id}`, () => call("/modal_bridge/models/packs/download", { id: pack.id })) }, addLabel),
        pack.templates.map((t) => h("button", { class: "mb-btn secondary", disabled: !t.available || !!ui.busy[`tpl-${t.name}`],
          title: t.available ? `Open ComfyUI's "${t.name}" template` : "This ComfyUI doesn't have that template yet. Update ComfyUI.",
          onclick: () => openTemplate(t.name) }, `Open: ${t.label}`)),
        h("details", {}, h("summary", {}, `${pack.files.length} file${pack.files.length === 1 ? "" : "s"}`),
          h("ul", { class: "mb-files" }, pack.files.map((f) => h("li", {}, `${f.have ? "✓ " : ""}${f.path} (${formatBytes(f.size)})`))))));
  }

  async function openTemplate(name) {
    ui.busy[`tpl-${name}`] = true;
    ui.packMsg = null;
    renderPacks();
    try {
      const res = await fetch(api.fileURL ? api.fileURL(`/templates/${name}.json`) : `/templates/${name}.json`);
      if (!res.ok) throw new Error(`Couldn't load the template (${res.status}). Update ComfyUI to get it.`);
      const json = await res.json();
      d.close();
      await app.loadGraphData(json, true, true, name);
    } catch (e) {
      ui.busy[`tpl-${name}`] = false;
      ui.packMsg = e.message;
      renderPacks();
    }
  }

  function renderTransfers() {
    const ts = ui.list?.transfers || [];
    fill(transfersBox, ts.length ? h("div", { class: "mb-step" }, h("h3", {}, "Transfers"),
      ts.slice().reverse().map((t) => {
        const { status, bar, active } = transferView(t);
        return h("div", { class: "mb-item" },
          h("span", { class: "mb-name" }, `${t.kind === "upload" ? "↑" : "↓"} ${t.folder}/${t.name}`),
          status,
          t.kind === "download" && active && t.state !== "cancelling"
            ? h("button", { class: "mb-btn secondary", onclick: () => act(`cancel-${t.id}`, () => call("/modal_bridge/models/cancel", { id: t.id })) }, "Stop")
            : h("span"),
          bar);
      })) : "");
  }

  function renderLists() {
    if (!ui.list) return;
    const cloud = ui.list.cloud.filter(matches);
    const total = ui.list.cloud.reduce((n, m) => n + m.size, 0);
    fill(cloudBox,
      h("h3", {}, `In your cloud (${ui.list.cloud.length}, ${formatBytes(total)})`),
      ui.list.cloud.length ? null : h("p", { class: "mb-muted" }, "No models yet. Add one from a link above, or upload one from this computer below."),
      h("div", { class: "mb-scroll" }, cloud.slice(0, LIST_LIMIT).map((m) => {
        const confirming = ui.confirm === m.path;
        return h("div", { class: "mb-item" },
          h("span", { class: "mb-name" }, m.path),
          h("span", { class: "mb-size" }, formatBytes(m.size)),
          h("button", {
            class: confirming ? "mb-btn" : "mb-btn secondary", disabled: !!ui.busy[`del-${m.path}`],
            title: "Delete from your Modal cloud. A copy on this computer, if any, stays.",
            onclick: () => {
              if (!confirming) { ui.confirm = m.path; return renderLists(); }
              ui.confirm = null;
              act(`del-${m.path}`, () => call("/modal_bridge/models/delete", { path: m.path }));
            },
          }, confirming ? "Really delete?" : "Delete"));
      })),
      cloud.length > LIST_LIMIT ? h("p", { class: "mb-muted" }, `Showing ${LIST_LIMIT} of ${cloud.length}. Filter to find others.`) : null);

    const uploading = new Set((ui.list.transfers || []).filter((t) => t.kind === "upload" && ACTIVE.includes(t.state)).map((t) => `${t.folder}/${t.name}`));
    const local = ui.list.local.filter(matches);
    fill(localBox, 
      h("h3", {}, `Only on this computer (${ui.list.local.length})`),
      h("p", { class: "mb-muted" }, "Upload a model to use it in the cloud. Big files take a while on a home connection; you can close this window meanwhile."),
      h("div", { class: "mb-scroll" }, local.slice(0, LIST_LIMIT).map((m) => {
        const key = `${m.folder}/${m.name}`;
        return h("div", { class: "mb-item" },
          h("span", { class: "mb-name" }, key),
          h("span", { class: "mb-size" }, formatBytes(m.size)),
          h("button", { class: "mb-btn secondary", disabled: uploading.has(key) || !!ui.busy[`up-${key}`],
            onclick: () => act(`up-${key}`, () => call("/modal_bridge/models/upload", { folder: m.folder, name: m.name })) },
            uploading.has(key) ? "Uploading..." : "Upload"));
      })),
      local.length > LIST_LIMIT ? h("p", { class: "mb-muted" }, `Showing ${LIST_LIMIT} of ${local.length}. Filter to find others.`) : null);
  }

  render();
  await load();
}

// -- upload missing models -------------------------------------------------------------------
// Opened when a cloud run stops because the workflow uses models that are only on this computer.

let missingOpen = null;

export async function openMissingModels(models, { onRun } = {}) {
  // Dropdowns refresh and toasts show from the global modal_bridge.models listener in modal_bridge.js.
  if (missingOpen || modelsOpen) return; // the Cloud models window already offers uploads
  const key = (m) => `${m.folder}/${m.name}`;
  // ids: the uploads this dialog follows. The bridge keeps finished transfers for a while, so an old
  // "done" upload of a model deleted since then must not count; only ones running now or started here.
  const ui = { sizes: {}, transfers: {}, errors: {}, ids: new Set(), started: false, busy: false };
  const settled = (t) => t && (ACTIVE.includes(t.state) || t.state === "done");
  function track(t) {
    if (t.kind !== "upload" || !models.some((m) => key(m) === key(t))) return;
    if (!ui.ids.has(t.id) && !ACTIVE.includes(t.state)) return;
    ui.ids.add(t.id);
    ui.transfers[key(t)] = t;
    if (ACTIVE.includes(t.state)) ui.started = true; // e.g. an upload left running when this window was hidden
    if (settled(t)) delete ui.errors[key(t)]; // e.g. "already uploading" from a click before the listing arrived
  }
  const onTransfers = ({ detail }) => {
    for (const t of detail.transfers || []) track(t);
    render();
  };
  api.addEventListener("modal_bridge.models", onTransfers);
  const d = dialog("Models only on this computer", () => {
    api.removeEventListener("modal_bridge.models", onTransfers);
    missingOpen = null;
  });
  missingOpen = d;

  const intro = h("p", { class: "mb-muted" });
  const list = h("div", { class: "mb-scroll" });
  const msg = h("div");
  const actions = h("div", { class: "mb-row" });
  d.body.append(h("div", { class: "mb-step" }, intro, list, msg, actions));

  async function upload() {
    ui.started = true;
    ui.busy = true;
    render();
    for (const m of models) {
      if (settled(ui.transfers[key(m)])) continue;
      delete ui.errors[key(m)];
      try {
        const t = await call("/modal_bridge/models/upload", { folder: m.folder, name: m.name });
        ui.ids.add(t.id);
        if (!ui.transfers[key(m)] || ui.transfers[key(m)].id !== t.id) ui.transfers[key(m)] = t; // events may already be newer
      } catch (e) {
        // A transfer event can arrive before this answer; only show the error if nothing is under way.
        if (!settled(ui.transfers[key(m)])) ui.errors[key(m)] = e.message;
      }
      render();
    }
    ui.busy = false;
    render();
  }

  function render() {
    const states = models.map((m) => (ui.errors[key(m)] ? "error" : ui.transfers[key(m)]?.state));
    const done = states.every((s) => s === "done");
    const failed = states.some((s) => s === "error" || s === "cancelled");
    const running = states.some((s) => ACTIVE.includes(s));
    const total = models.reduce((n, m) => n + (ui.sizes[key(m)] || 0), 0);

    const one = models.length === 1;
    intro.textContent = done
      ? `Uploaded. ${one ? "It stays" : "They stay"} in your Modal cloud, so you only do this once.`
      : `This workflow uses ${one ? "a model that isn't" : `${models.length} models that aren't`} in your Modal cloud yet. ` +
        `Upload ${one ? "it" : "them"} once and ${one ? "it stays" : "they stay"} there. Big files take a while on a home connection; ` +
        "you can close this window and keep working meanwhile.";
    fill(list, models.map((m) => {
      const t = ui.transfers[key(m)];
      const view = t ? transferView(t) : null;
      return h("div", { class: "mb-item" },
        h("span", { class: "mb-name" }, key(m)),
        ui.errors[key(m)] ? h("span", { class: "mb-fail" }, ui.errors[key(m)]) : view ? view.status : h("span", { class: "mb-size" }, ui.sizes[key(m)] ? formatBytes(ui.sizes[key(m)]) : ""),
        h("span"),
        view?.bar);
    }));
    fill(msg, failed && !running && !ui.busy ? h("p", { class: "mb-fail" }, "Some uploads didn't finish. Try again, or pick models that are in the cloud.") : "");
    fill(actions,
      done
        ? h("button", { class: "mb-btn", onclick: () => { d.close(); onRun?.(); } }, "Run again")
        : !ui.started || (failed && !running && !ui.busy)
          ? h("button", { class: "mb-btn", disabled: ui.busy, onclick: upload },
              `${ui.started ? "Try again" : "Upload"}${total ? ` (${formatBytes(total)})` : ""}`)
          : h("span", { class: "mb-muted" }, "Uploading..."),
      h("button", { class: "mb-btn secondary", onclick: () => d.close() }, done ? "Close" : ui.started ? "Hide" : "Not now"));
  }

  render();
  try {
    const listing = await call("/modal_bridge/models");
    for (const m of listing.local) ui.sizes[key(m)] = m.size;
    for (const t of listing.transfers) track(t); // uploads already under way
  } catch {
    // Sizes are only shown for information; uploading still works.
  }
  render();
}
