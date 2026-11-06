"""First-run setup, deploy and health check, all from inside ComfyUI (spec §7.2, §7.5; #19, #22).

- Sign-in runs Modal's own browser token flow in this process and stores the token where the
  Modal CLI does (~/.modal.toml). The token is also put in this process's environment, because
  Modal reads its config file only once at import.
- Deploy runs backend/deploy.py with ComfyUI's Python in a subprocess, streaming its log to the UI.
- The test job runs a model-free workflow on the real GPU worker.
- The health check reports each part of the setup with a plain-language fix.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import datetime as dt
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import modal
import modal.exception as mx
from server import PromptServer

from ..backend import app as backend
from . import config, errors, pricing
from .modal_client import get_client, reset_client

log = logging.getLogger("modal-bridge")

BACKEND_DIR = Path(backend.__file__).resolve().parent
LOGIN_TIMEOUT_S = 10 * 60
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# A model-free workflow: proves the GPU worker boots, runs ComfyUI and returns an image.
TEST_PROMPT = {
    "1": {"class_type": "EmptyImage", "inputs": {"width": 256, "height": 256, "batch_size": 1, "color": 0x3B82F6}},
    "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
}


def _local_comfyui_version() -> str | None:
    try:
        import comfyui_version

        return comfyui_version.__version__
    except ImportError:
        return None


def _listening_beyond_this_computer() -> bool:
    try:
        from comfy.cli_args import args
    except ImportError:
        return False
    hosts = [h.strip() for h in (args.listen or "").split(",") if h.strip()]
    return any(h not in ("127.0.0.1", "localhost", "::1") for h in hosts)


class Setup:
    def __init__(self, on_connected, cloud_busy) -> None:
        self._on_connected = on_connected  # coroutine fn: reconnect + model sync after login/deploy
        self._cloud_busy = cloud_busy  # fn: why cloud jobs, a test run or a deploy are active, or None
        self.login: dict = {"state": "idle"}
        self.deploy: dict = {"state": "idle", "log": collections.deque(maxlen=400)}
        self._rates: dict[str, float] | None = None
        self.workspace: str | None = None
        self.tests_running = 0

    @property
    def switching_account(self) -> bool:
        """A sign-in was approved and waits for cloud work to finish before switching accounts."""
        return self.login.get("state") == "switching"

    # -- state ---------------------------------------------------------------------------------

    async def snapshot(self) -> dict:
        cfg = config.load()
        signed_in = bool(modal.config.config.get("token_id"))
        if signed_in and self.workspace is None:
            try:
                ws = modal.Workspace.from_context()
                await ws.hydrate.aio()
                self.workspace = ws.name
            except Exception:
                pass
        return {
            "signed_in": signed_in,
            "workspace": self.login.get("workspace") or self.workspace,
            "login": {k: v for k, v in self.login.items() if k in ("state", "url", "code", "workspace", "error")},
            "deploy": {"state": self.deploy["state"], "error": self.deploy.get("error"), "log": list(self.deploy["log"])[-60:]},
            "secret_keys": cfg["secret_keys"],
            "setup_done": cfg["setup_done"],
            "connected": get_client().ready,
        }

    # -- sign-in -------------------------------------------------------------------------------

    async def start_login(self) -> dict:
        if self.login.get("state") in ("starting", "waiting", "switching"):
            return self.login
        ready = threading.Event()
        self.login = {"state": "starting"}
        threading.Thread(target=self._login_blocking, args=(ready,), daemon=True).start()
        await asyncio.to_thread(ready.wait, 30)
        if self.login["state"] == "starting":
            self.login = {"state": "error", "error": "Modal didn't answer. Check your internet connection and try again."}
        return self.login

    def _login_blocking(self, ready: threading.Event) -> None:
        from modal.token_flow import TokenFlow

        try:
            server_url = modal.config.config.get("server_url")
            with modal.Client.anonymous(server_url) as anon:
                flow = TokenFlow(anon)
                with flow.start("comfyless") as (_flow_id, web_url, code):
                    self.login = {"state": "waiting", "url": web_url, "code": code}
                    ready.set()
                    deadline = time.time() + LOGIN_TIMEOUT_S
                    result = None
                    while result is None:
                        if time.time() > deadline:
                            raise errors.BridgeError("Sign-in wasn't finished within 10 minutes. Start it again.")
                        result = flow.finish(timeout=40)
            # Set before the switch is scheduled, so job admission (on the event loop) sees it first.
            self.login = {"state": "switching", "workspace": result.workspace_username}
            asyncio.run_coroutine_threadsafe(self._switch_account(result), PromptServer.instance.loop)
        except Exception as exc:
            self.login = {"state": "error", "error": errors.friendly(exc)}
            log.warning("[modal-bridge] Modal sign-in failed: %s", exc)
        finally:
            ready.set()

    async def _switch_account(self, result) -> None:
        """Start using the newly approved token once nothing is using the current account's client.

        Runs on the event loop, like job admission, setup tests and deploys, which are refused while
        the state is "switching", so no job is still using the old client's queue and volumes.
        """
        from modal.config import _store_user_config  # what `modal token new` uses to write ~/.modal.toml

        while self._cloud_busy():
            await asyncio.sleep(0.5)
        # No await from here to the switch, so nothing can start on the old client in between.
        profile = result.workspace_username or "comfyless"
        try:
            _store_user_config(
                {"token_id": result.token_id, "token_secret": result.token_secret},
                profile=profile,
                active_profile=profile,
            )
            # Modal read ~/.modal.toml at import; the environment wins over it, so this process
            # (and the deploy subprocess it starts) uses the new token without a restart.
            os.environ["MODAL_TOKEN_ID"] = result.token_id
            os.environ["MODAL_TOKEN_SECRET"] = result.token_secret
            modal.Client.set_env_client(None)
            reset_client()
        except Exception as exc:
            self.login = {"state": "error", "error": errors.friendly(exc)}
            log.warning("[modal-bridge] couldn't switch to the new Modal account: %s", exc)
            return
        self.workspace = None
        self.login = {"state": "done", "workspace": profile}
        log.info("[modal-bridge] signed in to Modal workspace %s", profile)
        await self._on_connected()

    # -- secrets ---------------------------------------------------------------------------------

    async def save_secrets(self, values: dict[str, str]) -> list[str]:
        """Add or replace keys in the Modal Secret the backend reads. Empty values are ignored."""
        values = {k: v.strip() for k, v in values.items() if k in ("HF_TOKEN", "CIVITAI_TOKEN") and v and v.strip()}
        if values:
            await modal.Secret.objects.create.aio(backend.SECRET_NAME, {"COMFY_BRIDGE": "1"}, allow_existing=True)
            await modal.Secret.from_name(backend.SECRET_NAME).update.aio(values)
        keys = sorted(set(config.load()["secret_keys"]) | set(values))
        config.update(secret_keys=keys)
        return keys

    # -- deploy ----------------------------------------------------------------------------------

    def start_deploy(self, recreate: bool) -> None:
        if self.deploy["state"] == "running":
            return
        self.deploy = {"state": "running", "log": collections.deque(maxlen=400), "started_at": time.time()}
        threading.Thread(target=self._deploy_blocking, args=(recreate,), daemon=True).start()

    def _deploy_blocking(self, recreate: bool) -> None:
        cmd = [sys.executable, "-u", str(BACKEND_DIR / "deploy.py")] + (["--recreate"] if recreate else [])
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "120"}
        self._emit_deploy(line="Starting deploy. The first one builds the cloud image and takes a few minutes.")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=BACKEND_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            buf = ""
            for chunk in iter(lambda: proc.stdout.read1(4096), b""):
                buf += chunk.decode("utf-8", "replace")
                *lines, buf = re.split(r"\r\n|\r|\n", buf)
                for line in lines:
                    if line := _ANSI.sub("", line).rstrip():
                        self._emit_deploy(line=line)
            if buf.strip():
                self._emit_deploy(line=_ANSI.sub("", buf).rstrip())
            code = proc.wait()
        except Exception as exc:
            self._finish_deploy(errors.friendly(exc))
            return
        if code != 0:
            tail = [l for l in self.deploy["log"] if "error" in l.lower()][-1:] or list(self.deploy["log"])[-1:]
            self._finish_deploy(f"Deploy failed (exit {code}). {tail[0] if tail else ''}".strip())
            return
        self._finish_deploy(None)
        reset_client()
        asyncio.run_coroutine_threadsafe(self._on_connected(), PromptServer.instance.loop)

    def _emit_deploy(self, **data) -> None:
        if "line" in data:
            self.deploy["log"].append(data["line"])
        PromptServer.instance.send_sync("modal_bridge.deploy", data)

    def _finish_deploy(self, error: str | None) -> None:
        self.deploy["state"] = "error" if error else "done"
        self.deploy["error"] = error
        self._emit_deploy(state=self.deploy["state"], error=error)

    # -- test job ----------------------------------------------------------------------------------

    async def run_test(self, gpu: str, scaledown_s: int) -> dict:
        self.tests_running += 1
        try:
            return await self._run_test(gpu, scaledown_s)
        finally:
            self.tests_running -= 1

    async def _run_test(self, gpu: str, scaledown_s: int) -> dict:
        client = get_client()
        if not client.ready:
            await client.warm_up()
            if not client.ready:
                raise errors.BridgeError(client.error_text)
        payload = {
            "schema_version": backend.SCHEMA_VERSION,
            "job_id": str(uuid.uuid4()),  # ComfyUI only accepts canonical UUID prompt ids
            "prompt": TEST_PROMPT,
            "workflow": None,
            "inputs": [],
            "gpu": gpu,
            "client": {"bridge_version": "0.1.0", "comfyui_version": backend.COMFYUI_VERSION},
        }
        t0 = time.time()
        result = await client.worker(gpu, scaledown_s).run.remote.aio(payload)
        wall = time.time() - t0
        if result.get("status") != "success":
            raise errors.BridgeError(f"The test workflow failed in the cloud: {result.get('error')}")
        rates = await self.rates()
        bill = pricing.charge(gpu, scaledown_s, result["timings"], t0, time.time())
        config.add_spend(bill.added_s, pricing.cost(gpu, bill.added_s, rates))
        usd = pricing.cost(gpu, bill.run_s, rates)
        config.update(setup_done=True)
        image = next((f["data"] for f in result["files"] if f.get("data")), None)
        return {
            "gpu": result["gpu"],
            "cold": result["timings"]["cold"],
            "seconds": round(wall, 1),
            "usd": usd,
            "usd_text": pricing.fmt_usd(usd),
            "idle_note": pricing.idle_note(gpu, scaledown_s, rates),
            "image": f"data:image/png;base64,{base64.b64encode(image).decode()}" if image else None,
        }

    # -- prices and spend ----------------------------------------------------------------------------

    async def rates(self) -> dict[str, float]:
        """USD per GPU-second, from Modal's own rate card when reachable."""
        if self._rates is None:
            try:
                live = await modal.Workspace.from_context().billing.rates.aio()
                self._rates = pricing.from_modal_rates(live)
            except Exception as exc:
                log.debug("[modal-bridge] using built-in GPU prices: %s", exc)
                return dict(pricing.USD_PER_SECOND)
        return self._rates

    async def actual_spend(self) -> dict | None:
        """This app's cost from Modal's billing report: today and this month (UTC). Lags an hour or so."""
        now = dt.datetime.now(dt.timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        report = await modal.Workspace.from_context().billing.report.aio(start=month_start, resolution="h")
        rows = [r for r in report if r.description == backend.APP_NAME]
        today = now.date()
        return {
            "month_usd": float(sum(r.cost for r in rows)),
            "today_usd": float(sum(r.cost for r in rows if r.interval_start.date() == today)),
        }

    # -- health check ------------------------------------------------------------------------------

    async def health(self) -> dict:
        checks: list[dict] = []

        def add(check_id, label, status, detail, action=None):
            checks.append({"id": check_id, "label": label, "status": status, "detail": detail, "action": action})

        client = get_client()
        signed_in = bool(modal.config.config.get("token_id"))
        workspace = None
        if not signed_in:
            add("auth", "Signed in to Modal", "fail", "No Modal sign-in on this computer.", "login")
        else:
            try:
                ws = modal.Workspace.from_context()
                await ws.hydrate.aio()
                workspace = ws.name
                add("auth", "Signed in to Modal", "ok", f"Workspace: {workspace}")
            except Exception as exc:
                add("auth", "Signed in to Modal", "fail", errors.friendly(exc), "login")
                signed_in = False

        deployed = False
        if signed_in:
            try:
                await modal.Cls.from_name(backend.APP_NAME, "ComfyWorker").hydrate.aio()
                deployed = True
            except mx.NotFoundError:
                add("deploy", "Cloud backend deployed", "fail", "Not deployed to your Modal account yet.", "deploy")
            except Exception as exc:
                add("deploy", "Cloud backend deployed", "fail", errors.friendly(exc), "deploy")
        if deployed:
            meta = None
            try:
                meta = await modal.Dict.from_name(backend.META_DICT).get.aio("deployed")
            except Exception:
                pass
            if not meta:
                add("deploy", "Cloud backend deployed", "warn", "Deployed, but by an older version of the bridge. Redeploy to update it.", "deploy")
            elif meta.get("source_hash") != backend.source_hash():
                add("deploy", "Cloud backend deployed", "warn", "A newer backend is bundled with this bridge. Redeploy to update the cloud.", "deploy")
            else:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.get("deployed_at", 0)))
                add("deploy", "Cloud backend deployed", "ok", f"Up to date (deployed {when}).")

        local = _local_comfyui_version()
        cloud = backend.COMFYUI_VERSION.lstrip("v")
        if local and local != cloud:
            add("version", "ComfyUI versions match", "warn", f"This computer runs ComfyUI {local}, the cloud runs {cloud}. Most workflows still work; nodes new in {local} may not.")
        else:
            add("version", "ComfyUI versions match", "ok", f"ComfyUI {cloud}")

        if deployed:
            try:
                count = len(await client.list_models())
                if count:
                    add("models", "Models in the cloud", "ok", f"{count} model file(s)")
                else:
                    add("models", "Models in the cloud", "warn", "No models in your cloud yet. Add a checkpoint before running workflows that need one.")
            except Exception as exc:
                add("models", "Models in the cloud", "fail", errors.friendly(exc))

        if _listening_beyond_this_computer():
            add("network", "Only this computer can start cloud jobs", "warn",
                "ComfyUI is reachable from your network (--listen), so others on it could start paid cloud jobs. Setup actions are limited to this computer.")

        spend = config.load()["spend"]
        spend_info = {"estimated": spend, "estimated_text": pricing.fmt_usd(spend["usd"])}
        if signed_in:
            try:
                actual = await self.actual_spend()
                spend_info.update(actual, month_text=pricing.fmt_usd(actual["month_usd"]), today_text=pricing.fmt_usd(actual["today_usd"]))
            except Exception as exc:
                log.debug("[modal-bridge] billing report unavailable: %s", exc)

        return {"checks": checks, "workspace": workspace, "spend": spend_info, "ok": all(c["status"] != "fail" for c in checks)}
