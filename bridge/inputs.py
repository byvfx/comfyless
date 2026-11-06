"""Find local files a prompt references and upload the ones the cloud doesn't have (spec §6.3, #11).

Any string input naming an existing file in ComfyUI's input/ (or, via the " [output]" / " [temp]"
annotation, output/ or temp/) is treated as an input file. Files are stored in the IO volume by
content hash, so each one uploads once, and the prompt is rewritten to the name the worker stages
it under. The hash is taken from a snapshot made when the job is sent, and that snapshot is what
gets uploaded, so a file edited while the job waits can't end up stored under a stale hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import folder_paths

from .modal_client import ModalClient


@dataclass
class InputFile:
    local: Path
    ext: str
    staged_as: str  # path relative to the worker's ComfyUI input/ dir, forward slashes
    sha256: str = ""  # set by upload_missing from the snapshot it uploads

    def as_job_entry(self) -> dict:
        return {"sha256": self.sha256, "ext": self.ext, "as": self.staged_as}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve(value: str) -> tuple[Path, str] | None:
    """Return (local file, staged name) if value names a local input-like file."""
    # Returns (name, base directory), with None for an unannotated name (meaning input/).
    name, base = folder_paths.annotated_filepath(value)
    base = base or folder_paths.get_input_directory()
    if not name:
        return None
    path = Path(base, name)
    try:
        if not path.resolve().is_relative_to(Path(base).resolve()) or not path.is_file():
            return None
    except OSError:
        return None
    staged = PurePosixPath(*Path(name).parts)
    namespace = {
        os.path.normcase(os.path.abspath(folder_paths.get_output_directory())): "_output",
        os.path.normcase(os.path.abspath(folder_paths.get_temp_directory())): "_temp",
    }.get(os.path.normcase(os.path.abspath(base)))
    if namespace:
        staged = PurePosixPath(namespace) / staged
    return path, str(staged)


def collect(prompt: dict, skip: set[tuple[str, str]]) -> list[InputFile]:
    """Find input files in the prompt and rewrite their references in place.

    `skip` holds (node_id, input_name) pairs already identified as model references.
    """
    found: dict[Path, InputFile] = {}
    for node_id, node in prompt.items():
        for input_name, value in node.get("inputs", {}).items():
            if not isinstance(value, str) or (node_id, input_name) in skip:
                continue
            resolved = _resolve(value)
            if resolved is None:
                continue
            path, staged = resolved
            if path not in found:
                found[path] = InputFile(path, path.suffix.lstrip(".").lower() or "bin", staged)
            node["inputs"][input_name] = found[path].staged_as
    return list(found.values())


async def upload_missing(client: ModalClient, files: list[InputFile]) -> int:
    """Upload files whose hash isn't in the IO volume yet. Returns how many were uploaded."""
    if not files:
        return 0
    with tempfile.TemporaryDirectory(prefix="modal-bridge-") as tmp:
        snapshots = {}
        for i, f in enumerate(files):
            snap = Path(tmp, f"{i}.{f.ext}")
            await asyncio.to_thread(shutil.copyfile, f.local, snap)
            f.sha256 = await asyncio.to_thread(_sha256, snap)
            snapshots[f.sha256] = snap
        existing = await client.existing_inputs()
        missing = {h: snap for h, snap in snapshots.items() if f"{h}{snap.suffix}" not in existing}
        if missing:
            pairs = [(os.fspath(snap), f"inputs/{h}{snap.suffix}") for h, snap in missing.items()]
            await asyncio.to_thread(client.upload_inputs_blocking, pairs)
    return len(missing)
