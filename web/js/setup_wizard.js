// Modal Cloud setup wizard and "Check setup" panel (spec §7.2, §7.5). Everything a user must
// configure is done here, with no terminal: sign in, optional keys, deploy, test run.
// Imported by modal_bridge.js; this module registers nothing on its own.
import { api } from "../../scripts/api.js";

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
`;

// Build DOM without innerHTML, so server text (errors, log lines) is never parsed as HTML.
function h(tag, props = {}, ...children) {
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

function dialog(title, onClose) {
  if (!document.getElementById(STYLE_ID)) document.head.append(h("style", { id: STYLE_ID }, CSS));
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
        "Run it again after updating this extension."),
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
        `Starts a ${gpu || "L40S"} GPU and runs a tiny workflow. About a minute the first time; costs about a cent.`),
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

export async function openHealth({ onFix } = {}) {
  const d = dialog("Modal Cloud: check setup");
  d.body.append(h("p", { class: "mb-muted" }, "Checking..."));
  let health;
  try {
    health = await call("/modal_bridge/health");
  } catch (e) {
    d.body.replaceChildren(h("p", { class: "mb-fail" }, e.message));
    return;
  }
  const rows = health.checks.map((c) => {
    const [icon, cls] = ICON[c.status] || ICON.warn;
    const fix = c.action && h("button", { class: "mb-btn secondary", onclick: () => { d.close(); onFix?.(c.action); } },
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
    h("p", { class: "mb-muted" }, h("a", { href: BUDGETS_URL, target: "_blank", rel: "noopener" }, "Set a spending limit in Modal")));
  d.body.replaceChildren(h("div", { class: "mb-step" }, ...rows), spend);
}
