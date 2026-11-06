"""Bridge state kept in the ComfyUI user directory (spec §6.1).

Never holds secrets: the Modal token lives in ~/.modal.toml and HF/Civitai keys only in a Modal
Secret. This file only records which of those keys were set, setup progress, and estimated spend.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import folder_paths

log = logging.getLogger("modal-bridge")

_DEFAULTS: dict = {
    "setup_done": False,
    "secret_keys": [],  # names of keys saved to the Modal Secret, e.g. ["HF_TOKEN"]
    "spend": {"runs": 0, "gpu_seconds": 0.0, "usd": 0.0},
}
_lock = threading.RLock()


def _path() -> Path:
    return Path(folder_paths.get_user_directory(), "default", "modal_bridge", "config.json")


def load() -> dict:
    data = json.loads(json.dumps(_DEFAULTS))
    try:
        stored = json.loads(_path().read_text(encoding="utf-8"))
        data.update({k: v for k, v in stored.items() if k in data})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        log.warning("[modal-bridge] ignoring unreadable %s: %s", _path(), exc)
    return data


def update(**changes) -> dict:
    with _lock:
        data = load()
        data.update(changes)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return data


def add_spend(gpu_seconds: float, usd: float) -> dict:
    with _lock:
        spend = load()["spend"]
        spend = {
            "runs": spend["runs"] + 1,
            "gpu_seconds": round(spend["gpu_seconds"] + gpu_seconds, 2),
            "usd": round(spend["usd"] + usd, 6),
        }
        return update(spend=spend)["spend"]
