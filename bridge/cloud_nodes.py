"""Which node types the cloud can run, and the pre-flight check that names what's missing (spec §6.7, #14).

Two records in the backend's meta Dict:
- "deployed" (written by deploy.py): the manifest hash and each custom node pack's build result.
- "cloud_nodes" (written by each worker as it starts): every node type its ComfyUI loaded.

The node-type list is exact, but only exists once a worker has started on the current deploy.
Until then the check falls back to the pack build results.
"""

from __future__ import annotations

import logging
import time

import modal
import modal.exception

from ..backend import app as backend

log = logging.getLogger("modal-bridge")

REFRESH_S = 30  # another deploy (dev.py, another computer) can change what's in the cloud at any time


class CloudNodes:
    def __init__(self) -> None:
        self.deployed: dict | None = None
        self.record: dict | None = None
        self._fetched_at = 0.0

    async def refresh(self) -> None:
        meta = modal.Dict.from_name(backend.META_DICT)
        try:
            self.deployed = await meta.get.aio("deployed")
            self.record = await meta.get.aio("cloud_nodes")
        except modal.exception.NotFoundError:
            self.deployed = self.record = None
        self._fetched_at = time.time()

    @property
    def current(self) -> bool:
        """The node-type list comes from a worker of the current deploy."""
        if not (self.deployed and self.record):
            return False
        return self.record.get("manifest_hash") == (self.deployed.get("manifest_hash") or "core")

    async def refresh_if_stale(self) -> None:
        if time.time() - self._fetched_at > REFRESH_S:
            try:
                await self.refresh()
            except Exception as exc:
                log.debug("[modal-bridge] cloud node refresh failed: %s", exc)

    def check(self, prompt: dict) -> dict:
        """node_errors in ComfyUI's format for nodes the cloud can't run. Empty when unknown."""
        if not self.deployed:
            return {}
        import nodes

        classes = set(self.record["classes"]) if self.current else None
        packs = self.deployed.get("packs")
        if classes is None and packs is None and self.deployed.get("manifest_hash"):
            return {}  # the deploy couldn't read its build report; don't guess
        built = {p["name"]: p for p in packs or []}
        skipped = {s["name"]: s["reason"] for s in self.deployed.get("skipped") or []}
        errors: dict = {}
        for node_id, node in prompt.items():
            class_type = node.get("class_type")
            module = getattr(nodes.NODE_CLASS_MAPPINGS.get(class_type), "RELATIVE_PYTHON_MODULE", "nodes")
            pack = module.split(".", 1)[1] if module.startswith("custom_nodes.") else None
            if classes is not None:
                if class_type in classes:
                    continue
            elif pack is None or built.get(pack, {}).get("status") in ("ok", "partial"):
                continue  # core nodes match the cloud's version; a built pack is assumed to load
            errors[node_id] = {
                "errors": [{
                    # A type the frontend knows; it shows `details` verbatim for this one.
                    "type": "custom_validation_failed",
                    "message": "Node isn't available in the cloud",
                    "details": self._reason(class_type, pack, built, skipped),
                    "extra_info": {},
                }],
                "dependent_outputs": [],
                "class_type": class_type,
            }
        return errors

    def _reason(self, class_type: str, pack: str | None, built: dict, skipped: dict) -> str:
        if pack is None:
            version = (self.record or self.deployed).get("comfyui_version") or "version"
            return f"{class_type} isn't in the cloud's ComfyUI {version}. It may be newer than the cloud's ComfyUI."
        if pack in skipped:
            return f"{class_type} comes from {pack}, which isn't mirrored to the cloud: {skipped[pack]}"
        report = built.get(pack)
        if report is None:
            return f"{class_type} comes from {pack}, which isn't in your cloud yet. In the Modal Cloud tab (the cloud icon on the left), open Check setup and click Deploy."
        if report.get("status") == "failed":
            return f"{pack} failed to install in the cloud ({report.get('step')}): {(report.get('error') or '')[:300]}"
        return (f"{pack} is installed in the cloud, but its {class_type} node didn't load there. "
                "It may need a system package or a GPU feature the cloud doesn't have.")


cloud_nodes = CloudNodes()
