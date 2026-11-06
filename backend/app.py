"""Modal backend for Comfyless: a headless ComfyUI worker on a cloud GPU.

Deployed into the user's own Modal account. The local bridge talks to it only
through the Modal SDK (spec §5.4) -- there are no web endpoints.

Kept as a single file on purpose: Modal ships the deploy entrypoint into the
container, and sibling modules would not come along without extra packaging.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path, PurePosixPath

import modal

APP_NAME = "comfy-modal-bridge"
SCHEMA_VERSION = 1


def _load_nodes_manifest() -> dict:
    """What to mirror from the user's computer (spec §6.7): ComfyUI's version and custom node packs.

    The bridge writes it (bridge/nodes_manifest.py) and passes its path to deploy.py. It is read only
    on the deploying machine. In the container the image is already built, so the definition below
    falls back to plain ComfyUI there, and code that runs in the container reads what was built
    from BUILD_DIR instead.
    """
    path = os.environ.get("COMFYLESS_NODES_MANIFEST")
    if not path or not modal.is_local():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


NODES_MANIFEST = _load_nodes_manifest()

# The cloud runs the user's local ComfyUI release when it is at least the oldest one this backend is
# tested with; otherwise this default. Desktop installs ComfyUI releases, so the tag exists.
DEFAULT_COMFYUI_VERSION = "v0.44.0"
MIN_COMFYUI_VERSION = (0, 44, 0)


def _version_tuple(tag: str | None) -> tuple[int, ...] | None:
    try:
        return tuple(int(x) for x in (tag or "").lstrip("v").split("."))
    except ValueError:
        return None


def _comfyui_version(manifest: dict) -> str:
    tag = manifest.get("comfyui_version")
    parsed = _version_tuple(tag)
    return tag if parsed and parsed >= MIN_COMFYUI_VERSION else DEFAULT_COMFYUI_VERSION


COMFYUI_VERSION = _comfyui_version(NODES_MANIFEST)  # on the deploying machine; the container reads its own

DEFAULT_GPU = "L40S"
JOB_TIMEOUT_S = 60 * 60
SCALEDOWN_WINDOW_S = 5 * 60
MAX_CONTAINERS = 2

MODELS_VOLUME = "comfy-models"
IO_VOLUME = "comfy-io"
EVENTS_QUEUE = "comfy-events"
SECRET_NAME = "comfy-modal-bridge"
META_DICT = "comfy-bridge-meta"  # what was deployed, written by deploy.py, read by the health check

# Container paths are plain strings because image commands and volume mounts are built on the
# deploying machine; a pathlib.Path there becomes "\opt\ComfyUI" on Windows. Wrap in Path() only
# inside code that runs in the container.
MODELS_MOUNT = "/models"
IO_MOUNT = "/io"
COMFY_ROOT = "/opt/ComfyUI"
COMFY_DIR = Path(COMFY_ROOT)
COMFY_HOST = "127.0.0.1:8188"
BUILD_DIR = "/opt/comfyless"  # in the image: pip constraints, per-pack build reports, manifest hash

# Not uploaded with packs copied from the user's computer ("local" source).
LOCAL_PACK_IGNORE = ["**/.git", "**/__pycache__", "**/*.pyc", "**/.venv", "**/venv", "**/node_modules"]

# ComfyUI websocket events relayed to the local UI (spec §5.3).
FORWARDED_EVENTS = {
    "execution_start",
    "execution_cached",
    "executing",
    "progress",
    "progress_state",
    "executed",
    "execution_error",
    "execution_interrupted",
    "execution_success",
}
PROGRESS_MIN_INTERVAL_S = 0.25
INLINE_MAX_BYTES = 32 * 1024 * 1024
HISTORY_WAIT_S = 30
INTERRUPT = "interrupt"


def control_partition(job_id: str) -> str:
    """Queue partition the bridge writes INTERRUPT to; events for the job use the job_id itself."""
    return f"{job_id}-control"


def source_hash() -> str:
    """Fingerprint of this file, so the bridge can tell whether the deployed backend is current."""
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


app = modal.App(APP_NAME)

models_vol = modal.Volume.from_name(MODELS_VOLUME, create_if_missing=True)
io_vol = modal.Volume.from_name(IO_VOLUME, create_if_missing=True)
bridge_secret = modal.Secret.from_name(SECRET_NAME)

def _install_pack(pack: dict) -> None:
    """Image build step for one custom node pack (spec §5.1).

    Never raises: a failure is written to the pack's build report and the next pack still installs.
    Requirements are installed with torch pinned to ComfyUI's build, so a pack can't swap it out.
    """
    import io
    import json
    import subprocess
    import sys
    import urllib.parse
    import urllib.request
    import zipfile
    from pathlib import Path

    name = pack["name"]  # ComfyUI's module name: custom_nodes.<name>
    dest = Path(COMFY_ROOT) / "custom_nodes" / (pack.get("filename") or name)
    report = {"name": name, "source": pack["source"], "status": "ok", "step": None, "error": None, "warnings": []}

    def run(step: str, cmd: list[str], timeout: int = 1200) -> None:
        r = subprocess.run(cmd, cwd=dest if dest.is_dir() else None, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode:
            tail = " ".join(line.strip() for line in out.strip().splitlines()[-6:])
            raise RuntimeError(f"{step} failed (exit {r.returncode}): {tail[-800:]}")

    step = "download"
    try:
        if pack["source"] == "registry":
            query = urllib.parse.urlencode({"version": pack["version"]})
            url = f"https://api.comfy.org/nodes/{urllib.parse.quote(pack['id'])}/install?{query}"
            with urllib.request.urlopen(url, timeout=60) as resp:
                download_url = json.load(resp)["downloadUrl"]
            with urllib.request.urlopen(download_url, timeout=600) as resp:
                data = resp.read()
            dest.mkdir(parents=True, exist_ok=True)
            zipfile.ZipFile(io.BytesIO(data)).extractall(dest)  # extractall drops ".." and absolute paths
        elif pack["source"] == "git":
            run(step, ["git", "clone", "--filter=blob:none", "--no-checkout", pack["url"], str(dest)])
            run(step, ["git", "-C", str(dest), "checkout", "-q", pack["commit"]])
            if (dest / ".gitmodules").is_file():
                run(step, ["git", "-C", str(dest), "submodule", "update", "--init", "--recursive"])
        # "local" packs were copied into place by the previous image step.

        req = dest / "requirements.txt"
        if dest.is_dir() and req.is_file():
            step = "requirements"
            pip = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-c", f"{BUILD_DIR}/constraints.txt"]
            try:
                run(step, [*pip, "-r", str(req)])
            except RuntimeError as bulk_error:
                # One unavailable requirement (often Windows-only) shouldn't block the rest: like
                # ComfyUI-Manager, fall back to one line at a time and report what didn't install.
                # The pack is partial either way, since the file as a whole didn't install.
                report["status"] = "partial"
                report["warnings"].append(f"requirements.txt as a whole: {bulk_error}")
                for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    if line.startswith("-"):  # -r/-c/--index-url etc. can't be installed on their own
                        report["warnings"].append(f"{line}: skipped in the line-by-line retry")
                        continue
                    try:
                        run(step, [*pip, line])
                    except RuntimeError as exc:
                        report["warnings"].append(f"{line}: {exc}")
        if dest.is_dir() and (dest / "install.py").is_file():
            step = "install.py"
            run(step, [sys.executable, "install.py"])
    except Exception as exc:
        report.update(status="failed", step=step, error=str(exc)[-1500:])

    out = Path(BUILD_DIR) / "build" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report))
    print(f"[comfyless] custom node {name}: {report['status']}" + (f" at {report['step']}: {report['error']}" if report["error"] else ""))


def _with_packs(image: modal.Image, manifest: dict) -> modal.Image:
    """One cached build step per pack, in a stable order, then the manifest's fingerprint."""
    for pack in manifest.get("packs", []):
        dest = f"{COMFY_ROOT}/custom_nodes/{pack.get('filename') or pack['name']}"
        if pack["source"] == "local":
            if pack.get("is_file"):
                image = image.add_local_file(pack["path"], dest, copy=True)
            else:
                image = image.add_local_dir(pack["path"], dest, copy=True, ignore=LOCAL_PACK_IGNORE)
        spec = {k: v for k, v in pack.items() if k != "path"}  # content_hash keeps a local pack's step fresh
        image = image.run_function(_install_pack, kwargs={"pack": spec}, timeout=30 * 60)
    return image.run_commands(f"echo {manifest.get('hash') or 'core'} > {BUILD_DIR}/manifest_hash")


comfy_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "libgl1", "libglib2.0-0", "ffmpeg")
    .run_commands(
        f"git clone --depth 1 --branch {COMFYUI_VERSION} https://github.com/Comfy-Org/ComfyUI {COMFY_ROOT}",
        f"pip install --no-cache-dir -r {COMFY_ROOT}/requirements.txt",
    )
    .pip_install("websocket-client", "requests")
    .run_commands(
        f"mkdir -p {BUILD_DIR}/build",
        f"pip freeze | grep -iE '^(torch|torchvision|torchaudio)==' > {BUILD_DIR}/constraints.txt",
    )
)
comfy_image = _with_packs(comfy_image, NODES_MANIFEST)

download_image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")


def _built() -> dict:
    """In the container: what this image was built with."""
    root = Path(BUILD_DIR)
    hash_file = root / "manifest_hash"
    version = None
    version_file = COMFY_DIR / "comfyui_version.py"
    if version_file.is_file():
        for line in version_file.read_text().splitlines():
            if line.startswith("__version__"):
                version = "v" + line.split("=", 1)[1].strip().strip("'\"")
    return {
        "manifest_hash": hash_file.read_text().strip() if hash_file.is_file() else None,
        "comfyui_version": version,
        "packs": [json.loads(p.read_text()) for p in sorted((root / "build").glob("*.json"))],
    }


class _EventSender:
    """Ships job events to the Modal queue from a background thread, batching whatever has piled up.

    Each Queue.put is a network round trip (100+ ms). Sending inline from the websocket reader made
    it fall seconds behind ComfyUI, so a 5 s job looked like a 13 s one.
    """

    def __init__(self, events: modal.Queue, partition: str) -> None:
        import queue
        import threading

        self._pending: queue.SimpleQueue = queue.SimpleQueue()
        self._events = events
        self._partition = partition
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def emit(self, event_type: str, data: dict) -> None:
        self._pending.put({"type": event_type, "data": data, "t": time.time()})

    def close(self, timeout_s: float = 15) -> None:
        """Flush everything queued so far, then stop."""
        self._pending.put(None)
        self._thread.join(timeout_s)

    def _pump(self) -> None:
        import queue

        closing = False
        while not closing:
            batch = [self._pending.get()]
            while True:
                try:
                    batch.append(self._pending.get_nowait())
                except queue.Empty:
                    break
            closing = None in batch
            items = [b for b in batch if b is not None]
            if items:
                self._send(items)

    def _send(self, items: list[dict]) -> None:
        # The bridge also rebuilds the end-of-run events from the call result, so a batch that
        # still fails after retries only loses progress, never the job's outcome.
        for attempt in range(3):
            try:
                self._events.put_many(items, partition=self._partition)
                return
            except Exception as exc:
                if attempt == 2:
                    print(f"[modal-bridge] dropped {len(items)} events: {exc}", file=sys.stderr)
                else:
                    time.sleep(0.5 * (attempt + 1))


def _safe_join(root: Path, rel: str) -> Path:
    """Join a client-supplied relative path under root, refusing traversal."""
    path = (root / PurePosixPath(rel)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes {root}: {rel!r}")
    return path


@app.cls(
    image=comfy_image,
    gpu=DEFAULT_GPU,
    volumes={MODELS_MOUNT: models_vol.read_only(), IO_MOUNT: io_vol},
    timeout=JOB_TIMEOUT_S,
    scaledown_window=SCALEDOWN_WINDOW_S,
    max_containers=MAX_CONTAINERS,
)
class ComfyWorker:
    """One headless ComfyUI server per container, one prompt at a time."""

    @modal.enter()
    def start(self) -> None:
        self.started_at = time.time()
        self.jobs_run = 0
        self._link_model_dirs()
        self.proc = subprocess.Popen(
            [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch"],
            cwd=COMFY_DIR,
        )
        self._wait_healthy()
        self.boot_s = time.time() - self.started_at
        self.built = _built()
        import threading

        threading.Thread(target=self._record_nodes, daemon=True).start()

    @modal.exit()
    def stop(self) -> None:
        self.proc.terminate()

    @modal.method()
    def run(self, job: dict) -> dict:
        if job.get("schema_version") != SCHEMA_VERSION:
            return {"status": "error", "error": {"message": f"unsupported schema_version {job.get('schema_version')!r}"}}
        if self.proc.poll() is not None:
            raise RuntimeError(f"ComfyUI exited with code {self.proc.returncode}")

        sender = _EventSender(modal.Queue.from_name(EVENTS_QUEUE, create_if_missing=True), job["job_id"])
        try:
            return self._run_job(job, sender.emit)
        finally:
            sender.close()

    def _run_job(self, job: dict, emit) -> dict:
        import requests
        import websocket

        job_id = job["job_id"]
        cold = self.jobs_run == 0
        self.jobs_run += 1
        t0 = time.time()
        emit("worker_started", {"cold": cold, "boot_s": self.boot_s if cold else 0.0})

        io_vol.reload()
        self._stage_inputs(job.get("inputs", []))
        t_staged = time.time()

        extra_data = {}
        if job.get("workflow"):
            extra_data["extra_pnginfo"] = {"workflow": job["workflow"]}

        ws = websocket.create_connection(f"ws://{COMFY_HOST}/ws?clientId={job_id}")
        try:
            resp = requests.post(
                f"http://{COMFY_HOST}/prompt",
                # Reusing job_id as ComfyUI's prompt_id lets the local bridge relay events unchanged.
                json={
                    "prompt": job["prompt"],
                    "prompt_id": job_id,
                    "client_id": job_id,
                    "extra_data": extra_data,
                    **({"partial_execution_targets": job["partial_execution_targets"]} if job.get("partial_execution_targets") else {}),
                },
                timeout=60,
            )
            if resp.status_code != 200:
                body = resp.json()
                emit("validation_error", body)
                return {
                    "status": "error",
                    "error": body.get("error"),
                    "node_errors": body.get("node_errors", {}),
                }
            accepted = resp.json()
            prompt_id = accepted["prompt_id"]
            # A prompt can be accepted with errors on some output branches; the valid ones still run.
            node_errors = accepted.get("node_errors") or {}
            if node_errors:
                emit("validation_error", {"node_errors": node_errors})
            stop_watching = self._watch_for_interrupt(job_id)
            try:
                status, error = self._follow(ws, prompt_id, emit)
            finally:
                stop_watching.set()
        except BaseException:
            # Covers Modal input cancellation: stop ComfyUI so the next job isn't stuck behind this one.
            self._cancel(job_id)
            raise
        finally:
            ws.close()
        t_executed = time.time()

        outputs, files, wrote_volume = self._collect_outputs(prompt_id, job_id)
        if wrote_volume:
            io_vol.commit()
        t_done = time.time()

        return {
            "status": status,
            "error": error,
            "node_errors": node_errors,
            "prompt_id": prompt_id,
            "outputs": outputs,
            "files": files,
            "timings": {
                "cold": cold,
                "boot_s": self.boot_s if cold else 0.0,
                "stage_inputs_s": t_staged - t0,
                "execute_s": t_executed - t_staged,
                "collect_outputs_s": t_done - t_executed,
                "total_s": t_done - t0,
            },
            "gpu": job.get("gpu") or DEFAULT_GPU,
            "comfyui_version": self.built["comfyui_version"],
        }

    def _link_model_dirs(self) -> None:
        """Expose each top-level folder of the models volume as ComfyUI/models/<name>."""
        models_dir = COMFY_DIR / "models"
        models_dir.mkdir(exist_ok=True)
        for src in Path(MODELS_MOUNT).iterdir():
            if not src.is_dir():
                continue
            target = models_dir / src.name
            if target.is_symlink():
                continue
            if target.exists():
                shutil.rmtree(target)
            target.symlink_to(src, target_is_directory=True)

    def _record_nodes(self) -> None:
        """Publish which node types this image's ComfyUI loaded, for the bridge's pre-flight check.

        Runs once per container start, off the job path. Keyed by the manifest hash, so the bridge
        ignores records from containers of an older deploy.
        """
        import requests

        try:
            info = requests.get(f"http://{COMFY_HOST}/object_info", timeout=120).json()
            pack_classes: dict[str, int] = {}
            for node in info.values():
                module = node.get("python_module") or ""
                if module.startswith("custom_nodes."):
                    pack = module.split(".", 1)[1]
                    pack_classes[pack] = pack_classes.get(pack, 0) + 1
            modal.Dict.from_name(META_DICT, create_if_missing=True).put(
                "cloud_nodes",
                {
                    "manifest_hash": self.built["manifest_hash"],
                    "comfyui_version": self.built["comfyui_version"],
                    "classes": sorted(info),
                    "pack_classes": pack_classes,
                    "recorded_at": time.time(),
                },
            )
        except Exception as exc:
            print(f"[modal-bridge] couldn't record cloud nodes: {exc}", file=sys.stderr)

    def _wait_healthy(self, timeout_s: float = 300) -> None:
        import requests

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"ComfyUI exited during startup with code {self.proc.returncode}")
            try:
                if requests.get(f"http://{COMFY_HOST}/system_stats", timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"ComfyUI not healthy after {timeout_s}s")

    def _stage_inputs(self, inputs: list[dict]) -> None:
        input_dir = COMFY_DIR / "input"
        for item in inputs:
            src = Path(IO_MOUNT) / "inputs" / f"{item['sha256']}.{item['ext']}"
            if not src.is_file():
                raise FileNotFoundError(f"input not uploaded: {src.name} (for {item['as']})")
            dest = _safe_join(input_dir, item["as"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    def _follow(self, ws, prompt_id: str, emit) -> tuple[str, dict | None]:
        """Relay ComfyUI websocket events until our prompt finishes."""
        last_sent: dict[str, float] = {}  # per event type, so progress and progress_state throttle separately
        while True:
            raw = ws.recv()
            if isinstance(raw, bytes):
                continue  # binary latent previews; not relayed yet
            msg = json.loads(raw)
            event_type, data = msg.get("type"), msg.get("data") or {}
            if data.get("prompt_id") not in (None, prompt_id):
                continue

            if event_type in FORWARDED_EVENTS:
                if event_type in ("progress", "progress_state"):
                    now = time.time()
                    finished = event_type == "progress" and data.get("value") == data.get("max")
                    if now - last_sent.get(event_type, 0.0) < PROGRESS_MIN_INTERVAL_S and not finished:
                        continue
                    last_sent[event_type] = now
                emit(event_type, data)

            if event_type == "execution_success":
                return "success", None
            if event_type == "execution_error":
                return "error", data
            if event_type == "execution_interrupted":
                return "interrupted", data
            if event_type == "executing" and data.get("node") is None and data.get("prompt_id") == prompt_id:
                return "success", None

    def _collect_outputs(self, prompt_id: str, job_id: str) -> tuple[dict, list[dict], bool]:
        """Gather every output file of the prompt.

        Files up to INLINE_MAX_BYTES are returned inline as `data` -- the first read of a freshly
        committed volume file costs several seconds, which would dominate image jobs. Larger files
        (video) are written to the IO volume under `path`. Returns (outputs, files, wrote_volume).
        """
        import requests

        # ComfyUI sends execution_success/error before it records the prompt's history, so wait for it.
        deadline = time.time() + HISTORY_WAIT_S
        while True:
            history = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=30).json()
            if prompt_id in history:
                break
            if time.time() > deadline:
                raise TimeoutError(f"ComfyUI did not record history for {prompt_id} within {HISTORY_WAIT_S}s")
            time.sleep(0.05)
        outputs = history[prompt_id].get("outputs", {})
        out_root = Path(IO_MOUNT) / "outputs" / job_id

        files = []
        wrote_volume = False
        for node_id, node_out in outputs.items():
            for kind, items in node_out.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not (isinstance(item, dict) and "filename" in item):
                        continue
                    file_type = item.get("type", "output")
                    subfolder = item.get("subfolder", "")
                    entry = {
                        "node_id": node_id,
                        "kind": kind,
                        "filename": item["filename"],
                        "subfolder": subfolder,
                        "type": file_type,
                        "media_type": mimetypes.guess_type(item["filename"])[0] or "application/octet-stream",
                        "data": None,
                        "path": None,
                    }
                    # Fetch through /view rather than the filesystem: output/temp locations vary by
                    # ComfyUI version and launch flags, /view always knows where the file is.
                    with requests.get(
                        f"http://{COMFY_HOST}/view",
                        params={"filename": item["filename"], "subfolder": subfolder, "type": file_type},
                        stream=True,
                        timeout=60,
                    ) as resp:
                        resp.raise_for_status()
                        size = int(resp.headers.get("Content-Length", 0))
                        if 0 < size <= INLINE_MAX_BYTES:
                            entry["data"] = resp.content
                        else:
                            rel_in_job = PurePosixPath(file_type, subfolder, item["filename"])
                            dest = _safe_join(out_root, str(rel_in_job))
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with open(dest, "wb") as fh:
                                shutil.copyfileobj(resp.raw, fh, length=16 * 1024 * 1024)
                            size = dest.stat().st_size
                            entry["path"] = str(PurePosixPath("outputs", job_id) / rel_in_job)
                            wrote_volume = True
                    entry["size"] = size
                    files.append(entry)

        requests.post(f"http://{COMFY_HOST}/history", json={"delete": [prompt_id]}, timeout=30)
        return outputs, files, wrote_volume

    def _watch_for_interrupt(self, job_id: str):
        """Interrupt ComfyUI when the bridge posts INTERRUPT to this job's control partition.

        Cancelling the Modal call doesn't stop a running input, so Cancel in the UI goes through
        this channel instead and the job ends with ComfyUI's normal execution_interrupted.
        Returns an Event that stops the watcher. A cancel that arrives after the job finished is
        harmless: it is scoped to this job's id, so it can't reach the next job on this container.
        """
        import queue
        import threading

        stop = threading.Event()
        control = modal.Queue.from_name(EVENTS_QUEUE, create_if_missing=True)

        def watch() -> None:
            while not stop.is_set():
                try:
                    messages = control.get_many(10, timeout=1, partition=control_partition(job_id))
                except queue.Empty:
                    continue
                except Exception as exc:
                    print(f"[modal-bridge] control channel error: {exc}", file=sys.stderr)
                    stop.wait(1)
                    continue
                if INTERRUPT in messages and not stop.is_set():
                    self._cancel(job_id)
                    return

        threading.Thread(target=watch, daemon=True).start()
        return stop

    def _cancel(self, prompt_id: str) -> None:
        """Interrupt or dequeue this prompt only; a no-op once it has finished."""
        import requests

        try:
            requests.post(f"http://{COMFY_HOST}/api/jobs/{prompt_id}/cancel", timeout=5)
        except requests.RequestException:
            pass


@app.function(
    image=download_image,
    volumes={MODELS_MOUNT: models_vol},
    secrets=[bridge_secret],
    timeout=3 * 60 * 60,
)
def download_model(url: str, dest: str, progress_key: str | None = None) -> dict:
    """Stream a model from a URL straight into the models volume.

    dest is "folder/filename", or "folder/" to name the file after the download (Content-Disposition,
    then the URL path). Uses HF_TOKEN / CIVITAI_TOKEN from the bridge secret when present (gated
    repos, Civitai downloads). With progress_key, progress events go to that events-queue partition.
    """
    import os

    import requests

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""

    def is_host(domain: str) -> bool:
        return parsed.scheme == "https" and (host == domain or host.endswith("." + domain))

    # Credentials only go to the exact service over HTTPS. requests drops the Authorization
    # header itself when a redirect leaves that host (e.g. to a CDN).
    headers = {}
    if is_host("huggingface.co") and os.environ.get("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    elif is_host("civitai.com") and os.environ.get("CIVITAI_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['CIVITAI_TOKEN']}"

    events = modal.Queue.from_name(EVENTS_QUEUE, create_if_missing=True) if progress_key else None

    def report(data: dict) -> None:
        if events is not None:
            try:
                events.put({"type": "download_progress", "data": data, "t": time.time()}, partition=progress_key)
            except Exception as exc:  # progress is cosmetic; never fail the download over it
                print(f"[modal-bridge] progress event dropped: {exc}", file=sys.stderr)

    t0 = time.time()
    with requests.get(url, headers=headers, stream=True, allow_redirects=True, timeout=60) as resp:
        if resp.status_code in (401, 403):
            raise PermissionError(f"{host} refused the download ({resp.status_code}). It may need an access key "
                                  "(Setup, step 2) or accepting the model's license on its page.")
        resp.raise_for_status()
        if "text/html" in resp.headers.get("content-type", ""):
            raise ValueError("That link opened a web page, not a file. Use the model file's download link.")
        if dest.endswith("/"):
            dest += _download_filename(resp)
        target = _safe_join(Path(MODELS_MOUNT), dest)
        if target.exists():
            raise FileExistsError(f"{dest} is already in your cloud.")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        total = int(resp.headers.get("content-length") or 0)
        done = 0
        last = 0.0
        report({"name": dest, "done": 0, "total": total})
        try:
            with open(partial, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=16 * 1024 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 2:
                        last = time.time()
                        report({"name": dest, "done": done, "total": total})
            partial.rename(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    models_vol.commit()
    return {"path": dest, "size": target.stat().st_size, "seconds": time.time() - t0}


def _download_filename(resp) -> str:
    """A safe file name for a download: Content-Disposition first, then the final URL's path."""
    disposition = resp.headers.get("content-disposition", "")
    name = None
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disposition)
    if m:
        name = urllib.parse.unquote(m.group(1).strip().strip('"'))
    else:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition)
        if m:
            name = m.group(1).strip()
    if not name:
        name = urllib.parse.unquote(PurePosixPath(urllib.parse.urlparse(resp.url).path).name)
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name).strip(" .")
    if not name:
        raise ValueError("Couldn't tell the file name from the download. Enter one.")
    return name


@app.function(image=comfy_image, timeout=5 * 60)
def build_report() -> dict:
    """What the deployed worker image contains: ComfyUI version and each custom node pack's build result.

    A CPU container on the worker's image, called by deploy.py right after a deploy, so the result
    shows without waiting for a GPU.
    """
    return _built()
