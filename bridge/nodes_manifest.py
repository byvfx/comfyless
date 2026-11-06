"""What the cloud image mirrors from this computer: ComfyUI's version and the custom node packs (spec §6.7, #14).

Each pack in custom_nodes/ is recorded from the most reproducible source available:
- "registry": installed from the Comfy Registry (ComfyUI-Manager leaves a .tracking file and the
  pack's pyproject.toml names its id and version). The cloud downloads that exact version.
- "git": a git checkout. The cloud clones the remote at the same commit.
- "local": anything else (copied by hand, private, or edited with uncommitted changes when git can
  tell). The cloud gets an upload of this computer's copy.

Skipped: this extension, ComfyUI-Manager (a local tool), disabled packs, and local copies too big
to upload (usually because they bundle model files).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("modal-bridge")

SCHEMA = 1
LOCAL_MAX_BYTES = 200 * 1024 * 1024
_IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}  # matches backend LOCAL_PACK_IGNORE
_SKIP_NAMES = {"comfyui-manager": "ComfyUI-Manager is a local tool; the cloud doesn't need it."}
SELF_DIR = Path(__file__).resolve().parent.parent  # this extension


def build() -> dict:
    """Scan the local install. Safe to call often: only "local" packs are hashed file by file."""
    import folder_paths

    packs: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for root in folder_paths.get_folder_paths("custom_nodes"):
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for entry in sorted(root_path.iterdir(), key=lambda p: p.name.lower()):
            name = entry.name
            if name.startswith(".") or name == "__pycache__" or name in seen:
                continue
            if entry.is_file():
                if entry.suffix != ".py":
                    continue
                name = entry.stem  # ComfyUI loads custom_nodes/foo.py as module custom_nodes.foo
            if name in seen:
                continue
            seen.add(name)
            reason = _skip_reason(entry)
            if reason:
                skipped.append({"name": name, "reason": reason})
                continue
            pack = _describe(entry)
            if pack.get("skip"):
                skipped.append({"name": name, "reason": pack["skip"]})
            else:
                packs.append(pack)
    manifest = {"schema": SCHEMA, "comfyui_version": local_comfyui_tag(), "packs": packs, "skipped": skipped}
    manifest["hash"] = manifest_hash(manifest)
    return manifest


def manifest_hash(manifest: dict) -> str:
    """Fingerprint of what the cloud image would contain; paths on this computer don't count."""
    key = {
        "comfyui_version": manifest.get("comfyui_version"),
        "packs": [{k: v for k, v in p.items() if k != "path"} for p in manifest.get("packs", [])],
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def write(manifest: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def local_comfyui_tag() -> str | None:
    """The git tag of this computer's ComfyUI release, e.g. "v0.44.0"."""
    try:
        import comfyui_version

        return "v" + comfyui_version.__version__.lstrip("v")
    except Exception:
        return None


def summary(manifest: dict) -> dict:
    """Short form for the UI."""
    return {
        "hash": manifest["hash"],
        "comfyui_version": manifest["comfyui_version"],
        "packs": [{"name": p["name"], "source": p["source"], "label": _label(p)} for p in manifest["packs"]],
        "skipped": manifest["skipped"],
    }


# -- one pack ---------------------------------------------------------------------------------


def _skip_reason(entry: Path) -> str | None:
    if entry.resolve() == SELF_DIR:
        return "This extension runs on your computer only."
    if entry.name.endswith(".disabled"):
        return "Disabled in ComfyUI-Manager."
    return _SKIP_NAMES.get(entry.name.lower())


def _describe(entry: Path) -> dict:
    name = entry.stem if entry.is_file() else entry.name  # the module name ComfyUI gives the pack
    if entry.is_dir():
        project = _pyproject(entry / "pyproject.toml")
        if (entry / ".tracking").is_file() and project.get("name") and project.get("version"):
            return {"name": name, "source": "registry", "id": project["name"], "version": _semver(project["version"])}
        git = _git_info(entry)
        if git and git.get("url") and git.get("commit") and not git.get("dirty"):
            return {"name": name, "source": "git", "url": git["url"], "commit": git["commit"]}
    size, digest = _content_hash(entry)
    if size > LOCAL_MAX_BYTES:
        return {"name": name, "skip": f"Too big to upload ({size // (1024 * 1024)} MB; it probably includes model files)."}
    pack = {"name": name, "source": "local", "path": str(entry), "is_file": entry.is_file(), "content_hash": digest}
    if entry.is_file():
        pack["filename"] = entry.name
    return pack


def _label(pack: dict) -> str:
    if pack["source"] == "registry":
        return f"{pack['id']} {pack['version']} (Comfy Registry)"
    if pack["source"] == "git":
        return f"{_short_url(pack['url'])} @ {pack['commit'][:8]}"
    return "uploaded from this computer"


def _semver(version: str) -> str:
    """The Registry stores versions as three-part semver: a pack's pyproject "1.16" is "1.16.0" there."""
    parts = version.strip().split(".")
    if 0 < len(parts) < 3 and all(p.isdigit() for p in parts):
        parts += ["0"] * (3 - len(parts))
    return ".".join(parts)


def _short_url(url: str) -> str:
    return re.sub(r"^(https?://|git@)(www\.)?github\.com[:/]", "", url).removesuffix(".git")


def _pyproject(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        import tomllib

        return tomllib.loads(text).get("project", {})
    except ImportError:  # Python 3.10
        pass
    except Exception:
        return {}
    section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not section:
        return {}
    fields = dict(re.findall(r'^(name|version)\s*=\s*["\']([^"\']+)["\']', section.group(1), re.M))
    return fields


def _git_info(pack: Path) -> dict | None:
    """Remote URL, commit and whether there are uncommitted changes, with or without a git binary."""
    if not (pack / ".git").exists():
        return None
    git = shutil.which("git")
    if git:
        def run(*args: str) -> str | None:
            try:
                out = subprocess.run([git, "-c", "safe.directory=*", "-C", str(pack), *args], capture_output=True, text=True, timeout=20,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.TimeoutExpired):
                return None
            return out.stdout.strip() if out.returncode == 0 else None

        commit = run("rev-parse", "HEAD")
        url = run("config", "--get", "remote.origin.url")
        status = run("status", "--porcelain")  # untracked (not ignored) files are edits the cloud would miss
        if commit:
            return {"url": _public_url(url), "commit": commit, "dirty": bool(status)}
    return _git_info_from_files(pack)


def _git_info_from_files(pack: Path) -> dict | None:
    git_dir = pack / ".git"
    if git_dir.is_file():  # a worktree or submodule: "gitdir: <path>"
        target = git_dir.read_text(encoding="utf-8", errors="replace").partition("gitdir:")[2].strip()
        git_dir = (pack / target).resolve()
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head[4:].strip()
            ref_file = git_dir / ref
            if ref_file.is_file():
                commit = ref_file.read_text(encoding="utf-8").strip()
            else:
                packed = (git_dir / "packed-refs").read_text(encoding="utf-8") if (git_dir / "packed-refs").is_file() else ""
                commit = next((line.split()[0] for line in packed.splitlines() if line.endswith(" " + ref)), "")
        else:
            commit = head
        config = (git_dir / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', config, re.S)
    return {"url": _public_url(m.group(1) if m else None), "commit": commit or None, "dirty": False}


def _public_url(url: str | None) -> str | None:
    """An https clone URL the cloud can use without credentials; never keeps a token from the URL."""
    if not url:
        return None
    m = re.match(r"^git@([^:]+):(.+)$", url)
    if m:
        url = f"https://{m.group(1)}/{m.group(2)}"
    url = re.sub(r"^(https?://)[^/@]+@", r"\1", url)  # drop user:token@
    return url if url.startswith(("https://", "http://")) else None


def _content_hash(entry: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    total = 0
    files = [entry] if entry.is_file() else sorted(
        p for p in entry.rglob("*")
        if p.is_file() and not (set(p.relative_to(entry).parts[:-1]) & _IGNORE_DIRS) and p.suffix != ".pyc"
    )
    for f in files:
        size = f.stat().st_size
        total += size
        if total > LOCAL_MAX_BYTES:
            break
        h.update(str(f.relative_to(entry.parent)).replace("\\", "/").encode())
        h.update(f.read_bytes())
    return total, h.hexdigest()[:16]
