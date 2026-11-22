"""Lets the bridge be imported without ComfyUI.

ComfyUI's own modules (folder_paths, server, nodes, execution) are replaced with small fakes, and
the comfyless package is registered without running its __init__, which would register HTTP routes.
Each test gets fresh ComfyUI directories under tmp_path.

The installed custom node is one package: ComfyUI loads this directory, and bridge modules import
their siblings with `from ..backend` and `from .modal_client`. That only resolves when `bridge` and
`backend` are subpackages of that package, not top-level modules. This repo is that package (there
is no comfyless/ subdirectory, unlike byvfx/comfy-serverless), so the package path is the repo root.
The import name stays `comfyless`, the custom node folder name.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# Repository root holds bridge/ and backend/. Not REPO / "comfyless": that subdirectory only
# exists in byvfx/comfy-serverless.
PKG_DIR = REPO


def _fake_folder_paths() -> types.ModuleType:
    fp = types.ModuleType("folder_paths")
    fp.base_path = tempfile.mkdtemp(prefix="comfyless-tests-")
    fp.models_dir = os.path.join(fp.base_path, "models")
    fp.folder_names_and_paths = {}
    fp._dirs = {}

    def set_base(base: Path) -> None:
        fp.base_path = str(base)
        fp.models_dir = str(base / "models")
        fp._dirs = {name: str(base / name) for name in ("input", "output", "temp", "user")}
        for d in fp._dirs.values():
            os.makedirs(d, exist_ok=True)
        fp.folder_names_and_paths = {
            kind: ([str(base / "models" / kind)], {".safetensors", ".ckpt", ".pt", ".pth", ".bin"})
            for kind in ("checkpoints", "loras", "vae", "text_encoders", "diffusion_models", "upscale_models")
        }
        fp.folder_names_and_paths["custom_nodes"] = ([str(base / "custom_nodes")], set())
        fp.folder_names_and_paths["configs"] = ([str(base / "models" / "configs")], {".yaml"})
        for paths, _ in fp.folder_names_and_paths.values():
            os.makedirs(paths[0], exist_ok=True)

    _legacy = {"unet": "diffusion_models", "clip": "text_encoders"}

    def map_legacy(name: str) -> str:
        return _legacy.get(name, name)

    def get_folder_paths(kind: str) -> list[str]:
        return list(fp.folder_names_and_paths[map_legacy(kind)][0])

    def add_model_folder_path(kind: str, path: str, is_default: bool = False) -> None:
        kind = map_legacy(kind)
        paths, _ = fp.folder_names_and_paths.setdefault(kind, ([], set()))
        if path not in paths:
            paths.append(path)

    def get_filename_list(kind: str) -> list[str]:
        out = []
        for root in fp.folder_names_and_paths.get(map_legacy(kind), ([], set()))[0]:
            for dirpath, _dirs, files in os.walk(root):
                out += [os.path.relpath(os.path.join(dirpath, f), root) for f in files]
        return sorted(set(out))

    def get_full_path(kind: str, name: str) -> str | None:
        for root in fp.folder_names_and_paths.get(map_legacy(kind), ([], set()))[0]:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
        return None

    def annotated_filepath(name: str) -> tuple[str, str | None]:
        for tag, key in ((" [output]", "output"), (" [input]", "input"), (" [temp]", "temp")):
            if name.endswith(tag):
                return name[: -len(tag)], fp._dirs[key]
        return name, None

    fp.set_base = set_base
    fp.map_legacy = map_legacy
    fp.get_folder_paths = get_folder_paths
    fp.add_model_folder_path = add_model_folder_path
    fp.get_filename_list = get_filename_list
    fp.get_full_path = get_full_path
    fp.annotated_filepath = annotated_filepath
    fp.get_input_directory = lambda: fp._dirs["input"]
    fp.get_output_directory = lambda: fp._dirs["output"]
    fp.get_temp_directory = lambda: fp._dirs["temp"]
    fp.get_user_directory = lambda: fp._dirs["user"]
    set_base(Path(fp.base_path))
    return fp


def _fake_server() -> types.ModuleType:
    from aiohttp import web

    server = types.ModuleType("server")

    class PromptServer:
        instance = None

        def __init__(self) -> None:
            self.routes = web.RouteTableDef()
            self.client_id = None
            self.sent: list[tuple] = []

        def send_sync(self, event, data, sid=None) -> None:
            self.sent.append((event, data, sid))

        def send_bytes(self, event, data, sid=None) -> None:
            self.sent.append((event, data, sid))

    PromptServer.instance = PromptServer()
    server.PromptServer = PromptServer
    return server


def _install_fakes() -> None:
    sys.modules.setdefault("folder_paths", _fake_folder_paths())
    sys.modules.setdefault("server", _fake_server())
    nodes = types.ModuleType("nodes")
    nodes.NODE_CLASS_MAPPINGS = {}
    sys.modules.setdefault("nodes", nodes)
    sys.modules.setdefault("execution", types.ModuleType("execution"))

    pkg = types.ModuleType("comfyless")
    pkg.__path__ = [str(PKG_DIR)]
    pkg.__package__ = "comfyless"
    sys.modules.setdefault("comfyless", pkg)


_install_fakes()


@pytest.fixture(autouse=True)
def comfy_dirs(tmp_path, monkeypatch):
    """Fresh ComfyUI input/output/temp/user/models directories for each test."""
    import folder_paths

    folder_paths.set_base(tmp_path / "comfy")
    import comfyless.bridge.models_sync as models_sync

    monkeypatch.setattr(models_sync, "PLACEHOLDER_ROOT", Path(folder_paths.models_dir) / "modal_cloud")
    return tmp_path / "comfy"


@pytest.fixture
def node_types(monkeypatch):
    """Register fake node classes: node_types({"KSampler": cls, ...})."""
    import nodes

    mappings: dict = {}
    monkeypatch.setattr(nodes, "NODE_CLASS_MAPPINGS", mappings)
    return mappings.update
