"""Cloud model manager (spec §6.5, #21): list the models volume, add a model from a link, upload one
from this computer, delete one.

Downloads run in the cloud (backend download_model), so a 20 GB checkpoint never passes through this
computer; progress comes back on an events-queue partition. Uploads stream from disk with Modal's
volume uploader. Every transfer is tracked here and pushed to open tabs as `modal_bridge.models`
events; when one finishes, the placeholder sync runs so local dropdowns pick up the change.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Awaitable, Callable

import folder_paths
import modal
from server import PromptServer

from . import errors
from .modal_client import get_client
from .models_sync import _NON_MODEL_KINDS, PLACEHOLDER_ROOT

log = logging.getLogger("modal-bridge")

EVENT = "modal_bridge.models"
KEEP_FINISHED_S = 15 * 60
# Shown first in the folder picker; the rest follow alphabetically.
COMMON_FOLDERS = ["checkpoints", "loras", "vae", "text_encoders", "diffusion_models", "controlnet",
                  "upscale_models", "clip_vision", "embeddings"]
_ACTIVE = ("starting", "preparing", "running")
_REDEPLOY = "Update the cloud worker first: Extensions → Modal Cloud → Check setup → Deploy."


def model_folders() -> list[str]:
    """Folder kinds a user can put models in. Kinds a custom node pack registers inside its own folder
    (fonts, example prompts) are left out: those files reach the cloud with the pack."""
    kinds = [k for k, (paths, _exts) in folder_paths.folder_names_and_paths.items()
             if k not in _NON_MODEL_KINDS and not all(_in_custom_nodes(p) for p in paths)]
    return [k for k in COMMON_FOLDERS if k in kinds] + sorted(k for k in kinds if k not in COMMON_FOLDERS)


@functools.lru_cache(maxsize=1)
def _custom_nodes_roots() -> tuple[Path, ...]:
    return tuple(Path(root).resolve() for root in folder_paths.get_folder_paths("custom_nodes"))


def _in_custom_nodes(path: str) -> bool:
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    return any(resolved.is_relative_to(root) for root in _custom_nodes_roots())


def normalize_url(url: str) -> str:
    """The direct file URL for a pasted link. Blocking (a Civitai page link needs one API call)."""
    url = (url or "").strip()
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise errors.BridgeError("Paste an https:// link to the model file.")
    path = parsed.path
    if host in ("huggingface.co", "www.huggingface.co", "hf.co"):
        parts = [p for p in path.split("/") if p]
        if "blob" in parts:  # the file's page → its download link
            i = parts.index("blob")
            parts[i] = "resolve"
            return urllib.parse.urlunparse(parsed._replace(netloc="huggingface.co", path="/" + "/".join(parts), query=""))
        if "resolve" not in parts:
            raise errors.BridgeError("That's a Hugging Face repository, not a file. Open the file in the repository's "
                                     "Files tab and copy the link of its download button.")
        return url
    if host in ("civitai.com", "www.civitai.com", "civitai.green", "civitai.red"):
        if path.startswith("/api/download/models/"):
            return urllib.parse.urlunparse(parsed._replace(netloc="civitai.com"))
        m = re.match(r"^/models/(\d+)", path)
        if not m:
            raise errors.BridgeError("Paste the link of a Civitai model page or its download button.")
        version = urllib.parse.parse_qs(parsed.query).get("modelVersionId", [None])[0]
        if not (version and version.isdigit()):
            version = _civitai_latest_version(m.group(1))
        return f"https://civitai.com/api/download/models/{version}"
    return url


def _civitai_latest_version(model_id: str) -> str:
    req = urllib.request.Request(f"https://civitai.com/api/v1/models/{model_id}", headers={"User-Agent": "comfyless"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            versions = json.load(resp).get("modelVersions") or []
    except Exception as exc:
        raise errors.BridgeError(f"Couldn't look up that Civitai model ({exc}). Paste its download link instead.") from exc
    if not versions:
        raise errors.BridgeError("That Civitai model has no files to download.")
    return str(versions[0]["id"])


def _clean_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", (name or "").strip()).strip(" .")
    if name.endswith(".partial"):
        raise errors.BridgeError("Pick another file name.")
    return name


def _is_placeholder(path: str) -> bool:
    return Path(path).resolve().is_relative_to(PLACEHOLDER_ROOT.resolve())


class ModelManager:
    def __init__(self, sync: Callable[[], Awaitable[dict]]) -> None:
        self._sync = sync  # refreshes local placeholders and dropdown lists from the cloud
        self.transfers: dict[str, dict] = {}
        self._calls: dict[str, modal.FunctionCall] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def active(self) -> bool:
        return any(t["state"] in _ACTIVE for t in self.transfers.values())

    # -- listing --------------------------------------------------------------------------------

    async def listing(self) -> dict:
        files = await get_client().list_model_files()
        cloud = []
        cloud_names: dict[str, set[str]] = {}
        for path, size in files:
            top, _, name = path.partition("/")
            if not name:
                continue
            cloud.append({"path": path, "folder": top, "name": name, "size": size})
            cloud_names.setdefault(folder_paths.map_legacy(top), set()).add(name)
        cloud.sort(key=lambda m: (m["folder"], m["name"].lower()))
        local = await asyncio.to_thread(self._local_only, cloud_names)
        self._prune()
        return {"cloud": cloud, "local": local, "folders": model_folders(), "transfers": list(self.transfers.values())}

    def _local_only(self, cloud_names: dict[str, set[str]]) -> list[dict]:
        """Models on this computer that the cloud doesn't have, per folder."""
        out = []
        seen: set[tuple[str, str]] = set()
        for kind in model_folders():
            try:
                names = folder_paths.get_filename_list(kind)
            except Exception:
                continue
            for name in names:
                posix = name.replace("\\", "/")
                if posix in cloud_names.get(kind, ()):
                    continue
                full = folder_paths.get_full_path(kind, name)
                if not full or _is_placeholder(full) or _in_custom_nodes(full) or (kind, full) in seen:
                    continue
                seen.add((kind, full))
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                out.append({"folder": kind, "name": posix, "size": size})
        out.sort(key=lambda m: (m["folder"], m["name"].lower()))
        return out

    # -- transfers ------------------------------------------------------------------------------

    def _new(self, kind: str, folder: str, name: str) -> dict:
        t = {"id": uuid.uuid4().hex, "kind": kind, "folder": folder, "name": name, "state": "starting",
             "done": 0, "total": 0, "error": None, "started_at": time.time(), "finished_at": None}
        self.transfers[t["id"]] = t
        self._emit()
        return t

    def _emit(self) -> None:
        PromptServer.instance.send_sync(EVENT, {"transfers": list(self.transfers.values())})

    def _prune(self) -> None:
        cutoff = time.time() - KEEP_FINISHED_S
        for tid, t in list(self.transfers.items()):
            if t["finished_at"] and t["finished_at"] < cutoff:
                del self.transfers[tid]

    def _run(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finish(self, t: dict, error: str | None = None) -> None:
        t["state"] = "error" if error else ("cancelled" if t["state"] == "cancelling" else "done")
        t["error"] = error
        t["finished_at"] = time.time()
        if not error:
            try:
                await self._sync()
            except Exception as exc:
                log.warning("[modal-bridge] model sync after %s failed: %s", t["kind"], exc)
        self._emit()

    def _check_folder(self, folder: str) -> None:
        if folder not in model_folders():
            raise errors.BridgeError("Pick a model folder.")

    async def start_download(self, url: str, folder: str, filename: str = "") -> dict:
        self._check_folder(folder)
        direct = await asyncio.to_thread(normalize_url, url)
        filename = _clean_filename(filename)
        if filename and any(path == f"{folder}/{filename}" for path, _ in await get_client().list_model_files()):
            raise errors.BridgeError(f"{folder}/{filename} is already in your cloud.")
        shown = filename or urllib.parse.unquote(PurePosixPath(urllib.parse.urlparse(direct).path).name) or direct
        t = self._new("download", folder, shown)
        self._run(self._download(t, direct, f"{folder}/{filename}" if filename else f"{folder}/"))
        return t

    async def _download(self, t: dict, url: str, dest: str) -> None:
        client = get_client()
        key = f"models-{t['id']}"
        try:
            call = await client.download_fn.spawn.aio(url, dest, progress_key=key)
            self._calls[t["id"]] = call
            result = asyncio.ensure_future(call.get.aio())
            while not result.done():
                for event in await client.next_events(key, timeout=2):
                    if event.get("type") == "download_progress" and t["state"] != "cancelling":
                        data = event.get("data") or {}
                        t.update(state="running", done=data.get("done", 0), total=data.get("total", 0))
                        t["name"] = str(data.get("name", "")).partition("/")[2] or t["name"]
                        self._emit()
            info = result.result()
            t["name"] = info["path"].partition("/")[2]
            t["done"] = t["total"] = info["size"]
            await self._finish(t)
        except Exception as exc:
            if t["state"] == "cancelling":
                await self._finish(t)
            else:
                await self._finish(t, _download_error(exc))
        finally:
            self._calls.pop(t["id"], None)

    async def cancel(self, tid: str) -> None:
        t = self.transfers.get(tid)
        call = self._calls.get(tid)
        if not t or t["state"] not in _ACTIVE or call is None:
            raise errors.BridgeError("Only a running download can be stopped.")
        t["state"] = "cancelling"
        self._emit()
        await call.cancel.aio()

    async def start_upload(self, folder: str, name: str) -> dict:
        self._check_folder(folder)
        full = folder_paths.get_full_path(folder, name.replace("/", os.sep))
        if not full or _is_placeholder(full) or not os.path.isfile(full):
            raise errors.BridgeError(f"{name} isn't on this computer.")
        posix = name.replace("\\", "/")
        remote = f"{folder}/{posix}"
        if any(path == remote for path, _ in await get_client().list_model_files()):
            raise errors.BridgeError(f"{remote} is already in your cloud.")
        if any(x["kind"] == "upload" and x["state"] in _ACTIVE and f"{x['folder']}/{x['name']}" == remote
               for x in self.transfers.values()):
            raise errors.BridgeError(f"{posix} is already uploading.")
        t = self._new("upload", folder, posix)
        t["total"] = os.path.getsize(full)
        self._run(self._upload(t, full, remote))
        return t

    async def _upload(self, t: dict, full: str, remote: str) -> None:
        t["state"] = "preparing"  # Modal hashes the whole file before sending it
        self._emit()
        work = asyncio.ensure_future(asyncio.to_thread(self._upload_blocking, t, full, remote))
        while not work.done():
            await asyncio.wait([work], timeout=1)
            self._emit()
        try:
            work.result()
            t["done"] = t["total"]
            await self._finish(t)
        except FileExistsError:
            await self._finish(t, f"{remote} is already in your cloud.")
        except Exception as exc:
            await self._finish(t, errors.friendly(exc))

    @staticmethod
    def _upload_blocking(t: dict, full: str, remote: str) -> None:
        def progress(*_args, **kw):  # called on Modal's own thread
            if "name" in kw:
                t["state"] = "running"
                t["total"] = kw.get("size") or t["total"]
                return t["id"]
            if kw.get("reset"):
                t["done"] = 0
            elif "advance" in kw:
                t["done"] = max(0, min(t["total"], t["done"] + kw["advance"]))
            return None

        vol = get_client().models_vol
        try:
            # What `modal volume put` uses for its progress bar; batch_upload() takes no callback.
            from modal.volume import AbstractVolumeUploadContextManager

            batch_cm = AbstractVolumeUploadContextManager.resolve(
                vol._get_metadata().version, vol.object_id, vol.client, progress_cb=progress
            )
        except Exception as exc:  # internal API moved: upload without progress
            log.info("[modal-bridge] upload progress unavailable (%s)", exc)
            t["state"] = "running"
            batch_cm = vol.batch_upload()
        with batch_cm as batch:
            batch.put_file(full, remote)

    async def delete(self, path: str) -> None:
        if not any(p == path for p, _ in await get_client().list_model_files()):
            raise errors.BridgeError(f"{path} isn't in your cloud.")
        if any(f"{t['folder']}/{t['name']}" == path and t["state"] in _ACTIVE for t in self.transfers.values()):
            raise errors.BridgeError(f"{path} is still transferring.")
        await get_client().models_vol.remove_file.aio(path)
        await self._sync()


def _download_error(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, TypeError) and "progress_key" in text:
        return _REDEPLOY  # the deployed backend predates this version of the bridge
    if isinstance(exc, (PermissionError, FileExistsError, ValueError, errors.BridgeError)):
        return text
    if "404" in text or "Not Found" in text:
        return "Nothing to download at that link (404). Check that it points to a file."
    return errors.friendly(exc)
