"""Deploy the Modal backend into the signed-in Modal account.

    python deploy.py [--recreate]

The setup wizard runs this with ComfyUI's own Python as a subprocess, so the build log can be
streamed to the UI; scripts/dev.py calls deploy() directly. Prints one line per step.

Custom nodes are mirrored when COMFYLESS_NODES_MANIFEST names a manifest written by the bridge
(spec §6.7); without it, the cloud runs plain ComfyUI.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import modal

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also needed under embedded Python, which ignores the script dir

import app as backend  # noqa: E402

_MARK = {"ok": "OK", "partial": "PARTIAL", "failed": "FAILED"}


def deploy(recreate: bool = False) -> dict:
    manifest = backend.NODES_MANIFEST
    packs = manifest.get("packs", [])
    print(f"ComfyUI {backend.COMFYUI_VERSION}, {len(packs)} custom node pack(s) to mirror", flush=True)
    if manifest.get("comfyui_version") and manifest["comfyui_version"] != backend.COMFYUI_VERSION:
        print(f"  (this computer runs {manifest['comfyui_version']}; the cloud uses {backend.COMFYUI_VERSION}, "
              "the oldest release this backend supports)", flush=True)
    for skipped in manifest.get("skipped", []):
        print(f"  skipping {skipped['name']}: {skipped['reason']}", flush=True)

    # The app references this secret, so it must exist first. The setup wizard adds keys to it later.
    modal.Secret.objects.create(backend.SECRET_NAME, {"COMFY_BRIDGE": "1"}, allow_existing=True)
    # "rolling" lets already-warm containers keep serving the old code until they scale down;
    # "recreate" stops them so the next job is guaranteed to run the new version.
    with modal.enable_output():
        backend.app.deploy(strategy="recreate" if recreate else "rolling")

    built: dict = {}
    report_error = None
    if packs:
        print("Checking the custom node builds...", flush=True)
        try:
            built = modal.Function.from_name(backend.APP_NAME, "build_report").remote()
        except Exception as exc:
            report_error = f"{type(exc).__name__}: {exc}"
        for report in built.get("packs", []):
            line = f"  {_MARK.get(report['status'], report['status'])}  {report['name']}"
            if report.get("error"):
                line += f": {report['step']} failed. {report['error'][:300]}"
            elif report.get("warnings"):
                line += f": some requirements didn't install ({len(report['warnings'])})"
            print(line, flush=True)

    record = {
        "source_hash": backend.source_hash(),
        "comfyui_version": built.get("comfyui_version") or backend.COMFYUI_VERSION,
        "schema_version": backend.SCHEMA_VERSION,
        "deployed_at": time.time(),
        "manifest_hash": manifest.get("hash"),
        # None means "unknown": the bridge then doesn't judge custom nodes by build results.
        "packs": None if report_error else built.get("packs", []),
        "skipped": manifest.get("skipped", []),
    }
    modal.Dict.from_name(backend.META_DICT, create_if_missing=True).put("deployed", record)
    if report_error:
        # The setup wizard shows the last line containing "error".
        raise SystemExit(f"Error: deployed, but couldn't read the custom node build report ({report_error[:300]}). Deploy again to retry.")
    print(f"deployed {backend.APP_NAME}", flush=True)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true", help="stop warm containers so new code takes effect at once")
    deploy(parser.parse_args().recreate)
