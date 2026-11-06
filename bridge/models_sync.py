"""Make cloud-only models selectable in local dropdowns (spec §6.5, issue #13).

Every file in the Modal models volume gets a zero-byte placeholder under models/modal_cloud/<folder>/,
registered as an extra search path for that model folder. Placeholders are skipped when a real local
file with the same name already exists.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import folder_paths

log = logging.getLogger("modal-bridge")

PLACEHOLDER_ROOT = Path(folder_paths.models_dir) / "modal_cloud"

# Folder kinds whose values are never model files.
_NON_MODEL_KINDS = {"custom_nodes", "configs"}


class CloudModels:
    def __init__(self) -> None:
        self.by_kind: dict[str, set[str]] | None = None  # None until the first successful sync
        self.register_existing()

    def register_existing(self) -> None:
        """Register placeholder folders from a previous run so dropdowns work before the first sync."""
        if PLACEHOLDER_ROOT.is_dir():
            for top in PLACEHOLDER_ROOT.iterdir():
                if top.is_dir():
                    folder_paths.add_model_folder_path(top.name, str(top))

    def apply(self, cloud_paths: list[str]) -> tuple[int, int]:
        """Mirror the cloud file list as placeholders. Returns (created, removed)."""
        by_kind: dict[str, set[str]] = {}
        wanted: set[Path] = set()
        created = 0
        for rel in cloud_paths:
            top, _, name = rel.replace("\\", "/").partition("/")
            if not name:
                continue
            kind = folder_paths.map_legacy(top)
            by_kind.setdefault(kind, set()).add(name)
            if self._has_real_local(kind, name):
                continue
            placeholder = PLACEHOLDER_ROOT / top / Path(*name.split("/"))
            wanted.add(placeholder)
            if not placeholder.exists():
                placeholder.parent.mkdir(parents=True, exist_ok=True)
                placeholder.touch()
                created += 1
            folder_paths.add_model_folder_path(top, str(PLACEHOLDER_ROOT / top))

        removed = 0
        if PLACEHOLDER_ROOT.is_dir():
            for path in PLACEHOLDER_ROOT.rglob("*"):
                if path.is_file() and path not in wanted:
                    path.unlink()
                    removed += 1

        self.by_kind = by_kind
        return created, removed

    def _has_real_local(self, kind: str, name: str) -> bool:
        full = folder_paths.get_full_path(kind, name.replace("/", os.sep))
        return full is not None and not Path(full).is_relative_to(PLACEHOLDER_ROOT)

    def check_and_rewrite(self, prompt: dict) -> tuple[dict, set[tuple[str, str]]]:
        """Find model values the cloud doesn't have, and rewrite model paths to forward slashes.

        Returns (node_errors in ComfyUI's format for models missing from the cloud, the
        (node_id, input_name) pairs that reference models). The missing-model check is skipped
        until a sync has succeeded.
        """
        kinds = [k for k in folder_paths.folder_names_and_paths if k not in _NON_MODEL_KINDS]
        local_lists = {k: set(folder_paths.get_filename_list(k)) for k in kinds}
        node_errors: dict = {}
        model_refs: set[tuple[str, str]] = set()
        options_cache: dict[str, dict[str, set[str]]] = {}
        for node_id, node in prompt.items():
            for input_name, value in node.get("inputs", {}).items():
                if not isinstance(value, str):
                    continue
                matched = [k for k in kinds if value in local_lists[k]]
                if not matched:
                    continue
                if len(matched) > 1:
                    # The same filename in several folders (e.g. a checkpoint and a LoRA): use the
                    # folder(s) this input's dropdown is built from, so a cloud LoRA can't stand in
                    # for a local-only checkpoint.
                    options = _combo_options(node.get("class_type"), options_cache).get(input_name)
                    if options:
                        narrowed = [k for k in matched if local_lists[k] <= options]
                        matched = narrowed or matched
                model_refs.add((node_id, input_name))
                posix = value.replace("\\", "/")
                node["inputs"][input_name] = posix  # the cloud runs Linux
                if self.by_kind is None:
                    continue
                if not any(posix in self.by_kind.get(k, ()) for k in matched):
                    # The folder the real file is in, so the frontend can offer to upload it.
                    folder = next((k for k in matched if self._has_real_local(k, posix)), matched[0])
                    node_errors.setdefault(
                        node_id,
                        {"errors": [], "dependent_outputs": [], "class_type": node.get("class_type")},
                    )["errors"].append(
                        {
                            # A type the frontend knows; it shows `details` verbatim for this one.
                            "type": "custom_validation_failed",
                            "message": "Model is only on this computer",
                            "details": f"{input_name}: {value} is only on this computer, not in your Modal cloud. "
                            "Upload it in Extensions > Modal Cloud > Cloud models, or pick a cloud model.",
                            "extra_info": {"input_name": input_name, "modal_bridge_model": {"folder": folder, "name": posix}},
                        }
                    )
        return node_errors, model_refs


def _combo_options(class_type: str | None, cache: dict[str, dict[str, set[str]]]) -> dict[str, set[str]]:
    """{input_name: set of dropdown values} for a node type's combo inputs."""
    if class_type in cache:
        return cache[class_type]
    import nodes

    result: dict[str, set[str]] = {}
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    try:
        spec = cls.INPUT_TYPES() if cls is not None else {}
        for section in ("required", "optional"):
            for name, info in (spec.get(section) or {}).items():
                if not (isinstance(info, (list, tuple)) and info):
                    continue
                values = info[0]
                if values == "COMBO" and len(info) > 1 and isinstance(info[1], dict):  # newer node API
                    values = info[1].get("options")
                if isinstance(values, (list, tuple)):
                    result[name] = {v for v in values if isinstance(v, str)}
    except Exception as exc:  # a broken custom node shouldn't block the check for everything else
        log.debug("[modal-bridge] INPUT_TYPES failed for %s: %s", class_type, exc)
    cache[class_type] = result
    return result


cloud_models = CloudModels()
