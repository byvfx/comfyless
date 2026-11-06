"""Thin async wrapper over the Modal SDK objects the bridge uses (spec §5.4).

Uses the Modal token from the user's ~/.modal.toml. Lookups are cached for the life of the
ComfyUI process; the first Modal call in a process costs seconds, so `warm_up()` runs at startup.
After sign-in or a deploy, `reset_client()` drops the cached handles (in place, so holders of the
client keep a working object).
"""

from __future__ import annotations

import asyncio
import logging
import queue
from typing import Any

import modal

from ..backend import app as backend
from . import errors

log = logging.getLogger("modal-bridge")


class ModalClient:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.models_vol = modal.Volume.from_name(backend.MODELS_VOLUME)
        self.io_vol = modal.Volume.from_name(backend.IO_VOLUME)
        self.events = modal.Queue.from_name(backend.EVENTS_QUEUE, create_if_missing=True)
        self._worker_cls = modal.Cls.from_name(backend.APP_NAME, "ComfyWorker")
        self.download_fn = modal.Function.from_name(backend.APP_NAME, "download_model")
        self._variants: dict[tuple[str, int], Any] = {}
        self.ready = False
        self.error: str | None = None
        self._exc: BaseException | None = None

    async def warm_up(self) -> None:
        """Resolve the deployed app, queue and volumes so the first job doesn't pay for it."""
        try:
            await asyncio.gather(
                self._worker_cls.hydrate.aio(),
                self.events.hydrate.aio(),
                self.models_vol.hydrate.aio(),
                self.io_vol.hydrate.aio(),
            )
            self.ready = True
            self.error = None
            self._exc = None
            log.info("[modal-bridge] connected to Modal app %s", backend.APP_NAME)
        except Exception as exc:
            self.ready = False
            self._exc = exc
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("[modal-bridge] could not reach Modal app %s: %s", backend.APP_NAME, self.error)

    @property
    def error_text(self) -> str | None:
        """Why the bridge isn't connected, in plain language with a next step."""
        if self.ready:
            return None
        return errors.friendly(self._exc) if self._exc else "Still connecting to Modal..."

    def worker(self, gpu: str, scaledown_s: int):
        """A ComfyWorker instance for the requested GPU and idle window (cached per combination)."""
        key = (gpu, scaledown_s)
        if key not in self._variants:
            cls = self._worker_cls.with_options(gpu=gpu, scaledown_window=scaledown_s)
            self._variants[key] = cls()
        return self._variants[key]

    async def next_events(self, job_id: str, timeout: float) -> list[dict]:
        try:
            return await self.events.get_many.aio(100, timeout=timeout, partition=job_id)
        except queue.Empty:
            return []

    async def list_models(self) -> list[str]:
        """Relative paths of every file in the models volume, e.g. "checkpoints/x.safetensors"."""
        return [path for path, _size in await self.list_model_files()]

    async def list_model_files(self) -> list[tuple[str, int]]:
        """(path, size) of every finished file in the models volume; downloads in progress are left out."""
        entries = await self.models_vol.listdir.aio("/", recursive=True)
        return [(e.path, e.size) for e in entries
                if e.type == modal.volume.FileEntryType.FILE and not e.path.endswith(".partial")]

    async def existing_inputs(self) -> set[str]:
        try:
            entries = await self.io_vol.listdir.aio("inputs")
        except modal.exception.NotFoundError:
            return set()
        return {e.path.rsplit("/", 1)[-1] for e in entries}

    def upload_inputs_blocking(self, files: list[tuple[str, str]]) -> None:
        """Upload (local_path, remote_path) pairs. Blocking; call via asyncio.to_thread."""
        with self.io_vol.batch_upload() as batch:
            for local, remote in files:
                batch.put_file(local, remote)

    def download_blocking(self, remote_path: str, local_path: str) -> None:
        with open(local_path, "wb") as fh:
            self.io_vol.read_file_into_fileobj(remote_path, fh)

    # -- running containers ("stop paying now", #22) ---------------------------------------------
    # The SDK has no public call for these; `modal container list` / `modal container stop` use the
    # same two requests. Only containers of this app are ever listed or stopped.

    async def app_containers(self) -> list[str]:
        """Ids of this app's containers that are running now (GPU workers, downloads)."""
        app = await modal.App.lookup.aio(backend.APP_NAME)
        return await asyncio.to_thread(_list_tasks, app.app_id)

    async def stop_containers(self) -> int:
        """Stop every running container of this app. Returns how many were stopped."""
        ids = await self.app_containers()
        for task_id in ids:
            await asyncio.to_thread(_stop_task, task_id)
        return len(ids)


def _list_tasks(app_id: str) -> list[str]:
    from modal._object import _get_environment_name
    from modal._utils.async_utils import synchronizer
    from modal.client import _Client
    from modal_proto import api_pb2

    @synchronizer.create_blocking
    async def run() -> list[str]:
        client = await _Client.from_env()
        request = api_pb2.TaskListRequest(environment_name=_get_environment_name(None) or "", app_id=app_id)
        res = await client._stub.TaskList(request)
        return [t.task_id for t in res.tasks if t.app_id == app_id]  # never another app's containers

    return run()


def _stop_task(task_id: str) -> None:
    from modal._utils.async_utils import synchronizer
    from modal.client import _Client
    from modal_proto import api_pb2

    @synchronizer.create_blocking
    async def run() -> None:
        client = await _Client.from_env()
        await client._stub.ContainerStop(api_pb2.ContainerStopRequest(task_id=task_id))

    run()


_client: ModalClient | None = None


def get_client() -> ModalClient:
    global _client
    if _client is None:
        _client = ModalClient()
    return _client


def reset_client() -> ModalClient:
    client = get_client()
    client.reset()
    return client
