"""Deploy the Modal backend into the signed-in Modal account.

    python deploy.py [--recreate]

The setup wizard runs this with ComfyUI's own Python as a subprocess, so the build log can be
streamed to the UI; scripts/dev.py calls deploy() directly. Prints one line per step.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import modal

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also needed under embedded Python, which ignores the script dir

import app as backend  # noqa: E402


def deploy(recreate: bool = False) -> None:
    # The app references this secret, so it must exist first. The setup wizard adds keys to it later.
    modal.Secret.objects.create(backend.SECRET_NAME, {"COMFY_BRIDGE": "1"}, allow_existing=True)
    # "rolling" lets already-warm containers keep serving the old code until they scale down;
    # "recreate" stops them so the next job is guaranteed to run the new version.
    with modal.enable_output():
        backend.app.deploy(strategy="recreate" if recreate else "rolling")
    modal.Dict.from_name(backend.META_DICT, create_if_missing=True).put(
        "deployed",
        {
            "source_hash": backend.source_hash(),
            "comfyui_version": backend.COMFYUI_VERSION,
            "schema_version": backend.SCHEMA_VERSION,
            "deployed_at": time.time(),
        },
    )
    print(f"deployed {backend.APP_NAME}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true", help="stop warm containers so new code takes effect at once")
    deploy(parser.parse_args().recreate)
