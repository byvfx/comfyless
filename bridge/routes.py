"""HTTP routes the frontend extension calls instead of ComfyUI's own when cloud mode is on.

POST /modal_bridge/prompt mirrors POST /prompt: same request body, same response shape (including
400 + node_errors), so the frontend's error display and node highlighting work unchanged.

/modal_bridge/setup/*, /modal_bridge/health and the cloud model manager (/modal_bridge/models,
/modal_bridge/models/*) drive the setup dialogs. They only answer requests
from this computer: with --listen, someone else on the network could otherwise read a sign-in link
and attach this ComfyUI to their own Modal account, or redeploy it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import execution
from aiohttp import web
from server import PromptServer

from ..backend import app as backend
from . import config, errors
from . import inputs as inputs_mod
from .cloud_nodes import cloud_nodes
from .modal_client import get_client
from .models_manager import ModelManager
from .models_sync import cloud_models
from .runner import CloudJob, CloudRunner
from .setup import Setup

log = logging.getLogger("modal-bridge")

GPUS = ("L40S", "A100-80GB", "H100")
DEFAULT_GPU = "L40S"
DEFAULT_SCALEDOWN_S = 300
MODAL_APPS_URL = "https://modal.com/apps"

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

_runner: CloudRunner | None = None
_setup: Setup | None = None
_models: ModelManager | None = None
_stopping = False  # Stop now is cancelling work and stopping containers; nothing new may start


def _bad_request(error_type: str, message: str, details: str = "", node_errors: dict | None = None,
                 extra_info: dict | None = None):
    error = {"type": error_type, "message": message, "details": details, "extra_info": extra_info or {}}
    return web.json_response({"error": error, "node_errors": node_errors or {}}, status=400)


async def _sync_models() -> dict:
    paths = await get_client().list_models()
    created, removed = await asyncio.to_thread(cloud_models.apply, paths)
    log.info("[modal-bridge] cloud models: %d files (%d placeholders added, %d removed)", len(paths), created, removed)
    return {"models": len(paths), "created": created, "removed": removed}


def _local_only(request: web.Request, mutating: bool = True):
    """None if the request may use setup routes, else the error response to return.

    Compare the result with `is None`: an aiohttp Response is a mapping, so it is falsy.
    """
    if request.remote not in _LOOPBACK:
        return web.json_response({"error": "Setup can only be used on the computer running ComfyUI."}, status=403)
    if mutating and request.content_type != "application/json":  # a cross-site form can't send JSON
        return web.json_response({"error": "expected application/json"}, status=415)
    return None


def _busy(*kinds: str) -> str | None:
    """Why one of these is in progress, in plain language, or None.

    Switching accounts after a sign-in replaces this process's token and client, and deploying
    with recreate stops the running GPUs, so neither may overlap cloud jobs, a test run or each
    other. Callers check and then start their operation with no await in between.
    """
    if "jobs" in kinds and (_runner.current or _runner.pending):
        return "Wait for your cloud jobs to finish first."
    if "test" in kinds and _setup.tests_running:
        return "Wait for the test run to finish first."
    if "deploy" in kinds and _setup.deploy["state"] == "running":
        return "Wait for the cloud worker install (deploy) to finish first."
    if "login" in kinds and _setup.switching_account:
        return "Switching to your new Modal account once running cloud work finishes. Try again in a moment."
    if "models" in kinds and _models.active:
        return "Wait for the model uploads and downloads to finish first."
    if "stopping" in kinds and _stopping:
        return "Switching off the cloud GPUs (Stop now). Try again in a moment."
    return None


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json() if request.can_read_body else {}
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


async def _connect() -> None:
    """(Re)connect to the deployed app and refresh the cloud model list; tells open tabs the result."""
    client = get_client()
    await client.warm_up()
    if client.ready:
        try:
            await _sync_models()
        except Exception as exc:
            log.warning("[modal-bridge] model sync failed: %s", exc)
        try:
            await _setup.rates()
        except Exception:
            pass
        try:
            await cloud_nodes.refresh()
        except Exception as exc:
            log.warning("[modal-bridge] couldn't read the cloud's node list: %s", exc)
    PromptServer.instance.send_sync("modal_bridge.connection", {"connected": client.ready, "error": client.error_text})


def setup() -> None:
    global _runner, _setup, _models
    server = PromptServer.instance
    routes = server.routes
    _runner = CloudRunner(get_client())
    _setup = Setup(_connect, lambda: _busy("jobs", "test", "deploy", "models", "stopping"))
    _models = ModelManager(_sync_models)
    server.loop.create_task(_connect())

    @routes.post("/modal_bridge/prompt")
    async def post_prompt(request: web.Request):
        client = get_client()
        if not client.ready:
            await client.warm_up()
            if not client.ready:
                return _bad_request("modal_not_connected", "Can't reach your Modal app", client.error_text)

        json_data = server.trigger_on_prompt(await request.json())  # run custom nodes' on_prompt hooks, as /prompt does
        prompt = json_data.get("prompt")
        if not prompt:
            return _bad_request("no_prompt", "No prompt provided")
        options = json_data.get("modal_bridge") or {}
        gpu = options.get("gpu") if options.get("gpu") in GPUS else DEFAULT_GPU
        scaledown_s = int(min(max(float(options.get("scaledown_s") or DEFAULT_SCALEDOWN_S), 60), 1200))

        prompt_id = str(json_data.get("prompt_id") or uuid.uuid4())
        partial = json_data.get("partial_execution_targets")
        if hasattr(server, "node_replace_manager"):
            server.node_replace_manager.apply_replacements(prompt)

        valid = await execution.validate_prompt(prompt_id, prompt, partial)
        if not valid[0]:
            return web.json_response({"error": valid[1], "node_errors": valid[3]}, status=400)

        await cloud_nodes.refresh_if_stale()
        if unavailable := cloud_nodes.check(prompt):
            return _bad_request(
                "prompt_outputs_failed_validation",
                "Some nodes aren't available in the cloud",
                "Mirror your custom nodes with Deploy in Extensions > Modal Cloud > Check setup, or run this workflow locally.",
                unavailable,
            )

        missing, model_refs = cloud_models.check_and_rewrite(prompt)
        if missing:
            models = []
            for node in missing.values():
                for err in node["errors"]:
                    model = err["extra_info"].get("modal_bridge_model")
                    if model and model not in models:
                        models.append(model)
            return _bad_request(
                "prompt_outputs_failed_validation",
                "Some models are only on this computer",
                "Upload them in Extensions > Modal Cloud > Cloud models, or choose models that are in the cloud.",
                missing,
                # The frontend offers to upload these (Upload missing models).
                {"modal_bridge_missing_models": models},
            )

        input_files = await asyncio.to_thread(inputs_mod.collect, prompt, model_refs)
        extra = json_data.get("extra_data") or {}
        if reason := _busy("login", "deploy", "stopping"):
            return _bad_request("modal_busy", "Modal Cloud setup is in progress", reason)
        _runner.submit(
            CloudJob(
                prompt_id=prompt_id,
                prompt=prompt,
                workflow=(extra.get("extra_pnginfo") or {}).get("workflow"),
                client_id=json_data.get("client_id"),
                gpu=gpu,
                scaledown_s=scaledown_s,
                partial_execution_targets=partial,
                input_files=input_files,
            )
        )
        number = len(_runner.pending)
        return web.json_response({"prompt_id": prompt_id, "number": number, "node_errors": valid[3]})

    @routes.post("/modal_bridge/interrupt")
    async def post_interrupt(request: web.Request):
        try:
            body = await request.json() if request.can_read_body else {}
        except ValueError:
            body = {}
        prompt_id = body.get("prompt_id")
        if body.get("queued_only") and _runner.current and _runner.current.prompt_id == prompt_id:
            return web.json_response({"interrupted": False})  # like /queue clear, which leaves the running job
        return web.json_response({"interrupted": await _runner.interrupt(prompt_id)})

    @routes.post("/modal_bridge/models/sync")
    async def post_sync_models(_request: web.Request):
        try:
            return web.json_response(await _sync_models())
        except Exception as exc:
            return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=502)

    @routes.get("/modal_bridge/status")
    async def get_status(_request: web.Request):
        client = get_client()
        return web.json_response(
            {
                "connected": client.ready,
                "error": client.error_text,
                "setup_done": config.load()["setup_done"],
                "running": _runner.current.prompt_id if _runner.current else None,
                "pending": len(_runner.pending),
                "gpus": list(GPUS),
            }
        )

    # -- setup wizard and health check (spec §7.2, §7.5) ---------------------------------------

    @routes.get("/modal_bridge/setup/state")
    async def get_setup_state(request: web.Request):
        if (denied := _local_only(request, mutating=False)) is not None:
            return denied
        return web.json_response(await _setup.snapshot())

    @routes.post("/modal_bridge/setup/login")
    async def post_login(request: web.Request):
        if (denied := _local_only(request)) is not None:
            return denied
        state = await _setup.start_login()
        return web.json_response(state, status=502 if state.get("state") == "error" else 200)

    @routes.post("/modal_bridge/setup/secrets")
    async def post_secrets(request: web.Request):
        if (denied := _local_only(request)) is not None:
            return denied
        body = await _json_body(request)
        try:
            keys = await _setup.save_secrets({k: v for k, v in body.items() if isinstance(v, str)})
        except Exception as exc:
            return web.json_response({"error": errors.friendly(exc)}, status=502)
        return web.json_response({"secret_keys": keys})

    @routes.post("/modal_bridge/setup/deploy")
    async def post_deploy(request: web.Request):
        if (denied := _local_only(request)) is not None:
            return denied
        if _setup.deploy["state"] != "running" and (reason := _busy("jobs", "test", "login", "models", "stopping")):
            return web.json_response({"error": reason}, status=409)
        # "recreate" stops warm GPUs so the next job runs the new code; no job is running now.
        _setup.start_deploy(recreate=True)
        return web.json_response({"state": "running"})

    @routes.post("/modal_bridge/setup/test")
    async def post_test(request: web.Request):
        if (denied := _local_only(request)) is not None:
            return denied
        body = await _json_body(request)
        gpu = body.get("gpu") if body.get("gpu") in GPUS else DEFAULT_GPU
        scaledown_s = int(min(max(float(body.get("scaledown_s") or DEFAULT_SCALEDOWN_S), 60), 1200))
        if reason := _busy("login", "deploy", "stopping"):
            return web.json_response({"error": reason}, status=409)
        try:
            return web.json_response(await _setup.run_test(gpu, scaledown_s))
        except Exception as exc:
            log.warning("[modal-bridge] setup test failed: %s", exc)
            return web.json_response({"error": errors.friendly(exc)}, status=502)

    @routes.post("/modal_bridge/setup/finish")
    async def post_finish(request: web.Request):
        if (denied := _local_only(request)) is not None:
            return denied
        config.update(setup_done=True)
        return web.json_response({"setup_done": True})

    @routes.get("/modal_bridge/health")
    async def get_health(request: web.Request):
        if (denied := _local_only(request, mutating=False)) is not None:
            return denied
        return web.json_response(await _setup.health())

    @routes.post("/modal_bridge/stop_now")
    async def post_stop_now(request: web.Request):
        """Stop paying now (spec §7.5, #22): cancel cloud jobs and downloads, then stop this app's containers.

        Containers are stopped only after every call that could still hold an input is cancelled:
        Modal reschedules an input whose container is stopped. Nothing new may start meanwhile.
        """
        global _stopping
        if (denied := _local_only(request)) is not None:
            return denied
        # A setup test's call isn't tracked by the runner, so it can't be cancelled first.
        if reason := _busy("login", "deploy", "test", "stopping"):
            return web.json_response({"error": reason}, status=409)
        _stopping = True
        jobs = downloads = 0
        try:
            jobs = await _runner.cancel_all()
            downloads = await _models.cancel_downloads()
            stopped = await get_client().stop_containers()
        except Exception as exc:
            log.warning("[modal-bridge] stop now failed: %s", exc)
            return web.json_response({
                "error": f"Couldn't switch everything off from here ({errors.friendly(exc)}). "
                         f"Stop {backend.APP_NAME} on Modal's Apps page: {MODAL_APPS_URL}",
                "jobs": jobs, "downloads": downloads,
            }, status=502)
        finally:
            _stopping = False
        log.info("[modal-bridge] stop now: %d job(s), %d download(s) cancelled, %d container(s) stopped", jobs, downloads, stopped)
        return web.json_response({"jobs": jobs, "downloads": downloads, "containers": stopped})

    # -- cloud model manager (spec §6.5, #21) ---------------------------------------------------

    async def _models_action(request: web.Request, action, *busy_kinds: str):
        if (denied := _local_only(request)) is not None:
            return denied
        if reason := _busy(*busy_kinds):
            return web.json_response({"error": reason}, status=409)
        body = await _json_body(request)
        try:
            result = await action({k: v for k, v in body.items() if isinstance(v, str)})
        except Exception as exc:
            return web.json_response({"error": errors.friendly(exc)}, status=400 if isinstance(exc, errors.BridgeError) else 502)
        return web.json_response(result or {})

    @routes.get("/modal_bridge/models")
    async def get_models(request: web.Request):
        if (denied := _local_only(request, mutating=False)) is not None:
            return denied
        try:
            return web.json_response(await _models.listing())
        except Exception as exc:
            return web.json_response({"error": errors.friendly(exc)}, status=502)

    @routes.post("/modal_bridge/models/download")
    async def post_model_download(request: web.Request):
        return await _models_action(
            request, lambda b: _models.start_download(b.get("url", ""), b.get("folder", ""), b.get("filename", "")),
            "login", "deploy", "stopping")

    @routes.post("/modal_bridge/models/upload")
    async def post_model_upload(request: web.Request):
        return await _models_action(
            request, lambda b: _models.start_upload(b.get("folder", ""), b.get("name", "")), "login", "deploy", "stopping")

    @routes.post("/modal_bridge/models/cancel")
    async def post_model_cancel(request: web.Request):
        return await _models_action(request, lambda b: _models.cancel(b.get("id", "")))

    @routes.post("/modal_bridge/models/delete")
    async def post_model_delete(request: web.Request):
        # A running job may be reading the file.
        return await _models_action(request, lambda b: _models.delete(b.get("path", "")), "jobs", "login", "deploy")
