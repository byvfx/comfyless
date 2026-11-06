"""Runs cloud jobs one at a time and mirrors their execution in the local UI (spec §3, §6.4, §6.6).

Jobs are serialized like ComfyUI's own queue, so the frontend only ever sees one running prompt.
Cloud websocket events are relayed with send_sync to the browser tab that queued the prompt. The
exceptions are `executed` and the end-of-run events, which are held back until the output files
have been downloaded under local names. The end-of-run event is rebuilt from the call result if
the worker's copy never arrived, so the browser always learns that the job finished.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import folder_paths
from server import PromptServer

from ..backend import app as backend
from . import config, errors, pricing
from . import inputs as inputs_mod
from .modal_client import ModalClient

log = logging.getLogger("modal-bridge")

STARTUP_TIMEOUT_S = 600
_COUNTER_NAME = re.compile(r"^(?P<prefix>.+)_(?P<counter>\d{5,})_(?P<ext>\.[^.]+)$")


@dataclass
class CloudJob:
    prompt_id: str
    prompt: dict
    workflow: dict | None
    client_id: str | None
    gpu: str
    scaledown_s: int
    partial_execution_targets: list[str] | None
    input_files: list[inputs_mod.InputFile] = field(default_factory=list)


class CloudRunner:
    def __init__(self, client: ModalClient) -> None:
        self.client = client
        self.pending: list[CloudJob] = []  # a list, not asyncio.Queue, so queued jobs can be cancelled
        self._wake = asyncio.Event()
        self.current: CloudJob | None = None
        self.current_call = None
        self._interrupted = False
        self._worker_started = False
        self._task: asyncio.Task | None = None

    def submit(self, job: CloudJob) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())
        self.pending.append(job)
        self._wake.set()

    async def interrupt(self, prompt_id: str | None = None) -> bool:
        """Cancel a cloud job: the running one by default, or a queued one by prompt_id.

        The worker gets INTERRUPT on its control channel, since cancelling the Modal call doesn't
        stop a running input. Until the worker is known to have started, the call is cancelled too
        in case it is still waiting for a GPU.
        """
        if prompt_id and not (self.current and self.current.prompt_id == prompt_id):
            for job in self.pending:
                if job.prompt_id == prompt_id:
                    self.pending.remove(job)
                    self._send(job, "execution_interrupted", {"prompt_id": job.prompt_id, "node_id": None, "node_type": None, "executed": []})
                    return True
            return False
        if self.current is None:
            return False
        self._interrupted = True
        if self.current_call is not None:  # otherwise _run sees the flag once spawn returns
            await self._stop_call(self.current)
        return True

    async def _stop_call(self, job: CloudJob) -> None:
        await self.client.events.put.aio(backend.INTERRUPT, partition=backend.control_partition(job.prompt_id))
        if not self._worker_started:
            await self.current_call.cancel.aio()

    # -- internals ---------------------------------------------------------------------------

    def _send(self, job: CloudJob, event: str, data: dict) -> None:
        PromptServer.instance.send_sync(event, data, job.client_id)

    def _status(self, job: CloudJob, state: str, detail: str, severity: str = "info") -> None:
        self._send(job, "modal_bridge.status", {"prompt_id": job.prompt_id, "state": state, "detail": detail, "severity": severity})

    def _fail(self, job: CloudJob, message: str) -> None:
        # node_id None makes the frontend show this as a prompt-level error.
        self._send(
            job,
            "execution_error",
            {
                "prompt_id": job.prompt_id,
                "node_id": None,
                "node_type": None,
                "executed": [],
                "exception_type": "ModalBridgeError",
                "exception_message": message,
                "traceback": [],
                "current_inputs": {},
                "current_outputs": {},
            },
        )

    async def _loop(self) -> None:
        while True:
            while not self.pending:
                self._wake.clear()
                await self._wake.wait()
            job = self.pending.pop(0)
            self.current = job
            try:
                await self._run(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._interrupted:
                    self._send(job, "execution_interrupted", {"prompt_id": job.prompt_id, "node_id": None, "node_type": None, "executed": []})
                else:
                    log.exception("[modal-bridge] job %s failed", job.prompt_id)
                    self._fail(job, errors.friendly(exc))
            finally:
                self.current = None
                self.current_call = None
                self._interrupted = False
                self._worker_started = False

    async def _run(self, job: CloudJob) -> None:
        self._send(job, "execution_start", {"prompt_id": job.prompt_id, "timestamp": int(time.time() * 1000)})
        uploaded = await inputs_mod.upload_missing(self.client, job.input_files)
        if uploaded:
            self._status(job, "uploaded", f"Uploaded {uploaded} input file(s)")
        if self._interrupted:  # cancelled while uploading, before anything was sent to Modal
            raise RuntimeError("interrupted before start")

        payload = {
            "schema_version": backend.SCHEMA_VERSION,
            "job_id": job.prompt_id,
            "prompt": job.prompt,
            "workflow": job.workflow,
            "inputs": [f.as_job_entry() for f in job.input_files],
            "partial_execution_targets": job.partial_execution_targets,
            "gpu": job.gpu,
            "client": {"bridge_version": "0.3.0", "comfyui_version": backend.COMFYUI_VERSION},
        }
        worker = self.client.worker(job.gpu, job.scaledown_s)
        sent_at = time.time()
        self.current_call = await worker.run.spawn.aio(payload)
        if self._interrupted:  # cancelled while spawn was in flight
            await self._stop_call(job)
            raise RuntimeError("interrupted before start")
        self._status(job, "queued", f"Sent to Modal ({job.gpu}). Waiting for a GPU...")

        held_executed: list[dict] = []
        held_end: list[tuple[str, dict]] = []
        deadline = time.time() + STARTUP_TIMEOUT_S
        result = None
        while result is None:
            try:
                result = await self.current_call.get.aio(timeout=0)
                break
            except TimeoutError:
                pass
            for event in await self.client.next_events(job.prompt_id, timeout=0.5):
                self._worker_started = self._relay(job, event, held_executed, held_end) or self._worker_started
            if not self._worker_started and time.time() > deadline:
                await self.current_call.cancel.aio()
                raise TimeoutError(f"no cloud GPU started within {STARTUP_TIMEOUT_S}s; check `modal app logs {backend.APP_NAME}`")
        # Events still in flight when the call returned; next_events returns at most one batch.
        while events := await self.client.next_events(job.prompt_id, timeout=0.2):
            for event in events:
                self._relay(job, event, held_executed, held_end)

        status = result["status"]
        bill = None
        if t := result.get("timings"):  # it ran on a GPU, even if it failed there
            bill = pricing.charge(job.gpu, job.scaledown_s, t, sent_at, time.time())
            config.add_spend(bill.added_s, pricing.cost(job.gpu, bill.added_s))
        if status != "success" and result.get("prompt_id") is None:  # rejected before running
            err = result.get("error") or {}
            self._fail(job, f"Cloud rejected the workflow: {err.get('message', err)} {err.get('details', '')}".strip())
            return

        # Branches that finished before an error or interrupt still produced files; show them.
        renames = await self._save_outputs(result.get("files") or [])
        self._emit_outputs(job, result, held_executed, renames)
        if status == "success":
            self._send(job, "executing", {"node": None, "prompt_id": job.prompt_id})
            self._send(job, "execution_success", {"prompt_id": job.prompt_id, "timestamp": int(time.time() * 1000)})
            t = result["timings"]
            self._status(
                job,
                "done",
                f"{result['gpu']} {'cold start' if t['cold'] else 'warm'}: {t['execute_s']:.1f}s on GPU, "
                f"about {pricing.fmt_usd(pricing.cost(job.gpu, bill.run_s))}{' including start-up' if t['cold'] else ''}. "
                f"{pricing.idle_note(job.gpu, job.scaledown_s)}",
                "success",
            )
            return

        if held_end:
            kind, data = held_end[-1]
        elif status == "interrupted":
            kind, data = "execution_interrupted", {"prompt_id": job.prompt_id, "node_id": None, "node_type": None, "executed": []}
        elif result.get("error"):
            kind, data = "execution_error", {**result["error"], "prompt_id": job.prompt_id}
        else:
            self._fail(job, "The cloud job failed without an error message")
            return
        self._send(job, kind, data)
        self._send(job, "executing", {"node": None, "prompt_id": job.prompt_id})
        if kind == "execution_error" and (hint := errors.friendly_execution_error(data)):
            self._status(job, "warning", hint, "warn")

    def _relay(self, job: CloudJob, event: dict, held_executed: list[dict], held_end: list[tuple[str, dict]]) -> bool:
        """Forward one worker event to the UI. Returns True if it shows the worker has started."""
        kind, data = event["type"], event["data"]
        if kind == "worker_started":
            self._status(job, "running", "Cold start: GPU booted" if data.get("cold") else "Running on a warm GPU")
            return True
        if kind == "validation_error":
            self._status(job, "warning", "Some outputs failed validation in the cloud and were skipped", "warn")
            return False
        if kind == "executed":
            held_executed.append(data)
            return True
        if kind in ("execution_error", "execution_interrupted"):
            held_end.append((kind, data))  # sent by _run after any finished outputs
            return True
        if kind == "execution_success" or (kind == "executing" and data.get("node") is None):
            return True  # sent by _run once files are local
        if kind == "execution_start":
            return True  # _run already sent it when the job left the local queue
        self._send(job, kind, data)
        return True

    async def _save_outputs(self, files: list[dict]) -> dict[tuple[str, str, str], tuple[str, str]]:
        """Write cloud outputs into local output/ or temp/. Returns {(type, subfolder, filename): (subfolder, new filename)}."""
        renames = {}
        for f in files:
            base = folder_paths.get_temp_directory() if f["type"] == "temp" else folder_paths.get_output_directory()
            folder = Path(base, *f["subfolder"].split("/")) if f["subfolder"] else Path(base)
            if not folder.resolve().is_relative_to(Path(base).resolve()):
                raise ValueError(f"cloud output subfolder escapes {base}: {f['subfolder']!r}")
            if Path(f["filename"]).name != f["filename"] or f["filename"] in ("", ".", ".."):
                raise ValueError(f"unsafe cloud output filename: {f['filename']!r}")
            folder.mkdir(parents=True, exist_ok=True)
            target = _free_name(folder, f["filename"])
            if f["data"] is not None:
                await asyncio.to_thread(target.write_bytes, f["data"])
            else:
                await asyncio.to_thread(self.client.download_blocking, f["path"], os.fspath(target))
            renames[(f["type"], f["subfolder"], f["filename"])] = (f["subfolder"], target.name)
        return renames

    def _emit_outputs(self, job: CloudJob, result: dict, held_executed: list[dict], renames: dict) -> None:
        def localize(output: dict) -> dict:
            out = {}
            for kind, items in output.items():
                if isinstance(items, list):
                    items = [_rename_item(item, renames) for item in items]
                out[kind] = items
            return out

        seen = set()
        for data in held_executed:
            seen.add(str(data.get("node")))
            self._send(job, "executed", {**data, "output": localize(data.get("output") or {})})
        # Cached output nodes may not have sent `executed`; fall back to the history record.
        for node_id, output in (result.get("outputs") or {}).items():
            if str(node_id) not in seen:
                self._send(job, "executed", {"node": node_id, "display_node": node_id, "output": localize(output), "prompt_id": job.prompt_id})


def _rename_item(item, renames: dict):
    if not (isinstance(item, dict) and "filename" in item):
        return item
    key = (item.get("type", "output"), item.get("subfolder", ""), item["filename"])
    if key not in renames:
        return item
    subfolder, filename = renames[key]
    return {**item, "subfolder": subfolder, "filename": filename}


def _free_name(folder: Path, filename: str) -> Path:
    """A path in folder that doesn't overwrite an existing file.

    Cloud containers restart their save counters on every cold start, so "x_00001_.png" will
    collide with earlier runs; continue the local counter instead, as ComfyUI's SaveImage would.
    """
    target = folder / filename
    if not target.exists():
        return target
    m = _COUNTER_NAME.match(filename)
    if m:
        prefix, ext = m["prefix"], m["ext"]
        pattern = re.compile(re.escape(prefix) + r"_(\d+)_" + re.escape(ext) + "$")
        used = [int(p.group(1)) for p in (pattern.match(n) for n in os.listdir(folder)) if p]
        counter = max(used, default=0) + 1
        return folder / f"{prefix}_{counter:05}_{ext}"
    stem, suffix = os.path.splitext(filename)
    n = 1
    while (folder / f"{stem}_{n}{suffix}").exists():
        n += 1
    return folder / f"{stem}_{n}{suffix}"
