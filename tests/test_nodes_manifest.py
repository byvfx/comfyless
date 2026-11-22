from pathlib import Path

import folder_paths
import pytest

from comfyless.bridge import nodes_manifest as nm


@pytest.mark.parametrize("version, expected", [("1.16", "1.16.0"), ("2", "2.0.0"), ("1.2.3", "1.2.3"), ("1.0b1", "1.0b1"), (" 3.1 ", "3.1.0")])
def test_semver(version, expected):
    assert nm._semver(version) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/kijai/ComfyUI-Florence2.git", "kijai/ComfyUI-Florence2"),
        ("git@github.com:kijai/ComfyUI-Florence2.git", "kijai/ComfyUI-Florence2"),
        ("https://www.github.com/a/b", "a/b"),
        ("https://gitlab.com/a/b.git", "https://gitlab.com/a/b"),
    ],
)
def test_short_url(url, expected):
    assert nm._short_url(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("git@github.com:owner/repo.git", "https://github.com/owner/repo.git"),
        ("https://user:ghp_secret@github.com/owner/repo.git", "https://github.com/owner/repo.git"),
        ("https://ghp_secret@github.com/owner/repo", "https://github.com/owner/repo"),
        ("http://example.com/repo.git", "http://example.com/repo.git"),
        ("ssh://git@github.com/owner/repo.git", None),
        ("/local/path/repo", None),
        (None, None),
        ("", None),
    ],
)
def test_public_url_never_keeps_credentials(url, expected):
    assert nm._public_url(url) == expected


def test_manifest_hash_ignores_local_paths():
    a = {"comfyui_version": "v0.44.0", "packs": [{"name": "x", "source": "local", "content_hash": "1", "path": "C:/one"}]}
    b = {"comfyui_version": "v0.44.0", "packs": [{"name": "x", "source": "local", "content_hash": "1", "path": "D:/two"}]}
    c = {"comfyui_version": "v0.45.0", "packs": a["packs"]}
    assert nm.manifest_hash(a) == nm.manifest_hash(b)
    assert nm.manifest_hash(a) != nm.manifest_hash(c)
    assert len(nm.manifest_hash(a)) == 16


def _fake_git(pack: Path, url: str, commit: str, packed: bool = False) -> None:
    git = pack / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    if packed:
        (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{commit} refs/heads/main\n")
    else:
        (git / "refs" / "heads" / "main").write_text(commit + "\n")
    (git / "config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*\n')


@pytest.mark.parametrize("packed", [False, True])
def test_git_info_from_files(tmp_path, packed):
    pack = tmp_path / "pack"
    _fake_git(pack, "https://tok@github.com/o/r.git", "a" * 40, packed=packed)
    assert nm._git_info_from_files(pack) == {"url": "https://github.com/o/r.git", "commit": "a" * 40, "dirty": False}


def test_git_info_from_files_detached_head(tmp_path):
    pack = tmp_path / "pack"
    _fake_git(pack, "git@github.com:o/r.git", "b" * 40)
    (pack / ".git" / "HEAD").write_text("c" * 40 + "\n")
    assert nm._git_info_from_files(pack)["commit"] == "c" * 40


def test_pyproject_reads_name_and_version(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "comfyui-florence2"\nversion = "1.0"\n\n[tool.comfy]\nPublisherId = "kijai"\n')
    assert nm._pyproject(path)["name"] == "comfyui-florence2"
    assert nm._pyproject(path)["version"] == "1.0"
    assert nm._pyproject(tmp_path / "missing.toml") == {}


def test_build_describes_each_kind_of_pack(monkeypatch):
    monkeypatch.setattr(nm.shutil, "which", lambda _name: None)  # read .git files, don't need a git binary
    monkeypatch.setattr(nm, "local_comfyui_tag", lambda: "v0.44.0")
    root = Path(folder_paths.get_folder_paths("custom_nodes")[0])

    registry = root / "ComfyUI-Florence2"
    registry.mkdir()
    (registry / ".tracking").write_text("")
    (registry / "pyproject.toml").write_text('[project]\nname = "comfyui-florence2"\nversion = "1.0"\n')

    _fake_git(root / "rgthree-comfy", "git@github.com:rgthree/rgthree-comfy.git", "d" * 40)

    local = root / "my-nodes"
    (local / "__pycache__").mkdir(parents=True)
    (local / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
    (local / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"ignored")

    (root / "single_file.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
    (root / "notes.txt").write_text("not a pack")
    (root / "old-pack.disabled").mkdir()
    (root / "ComfyUI-Manager").mkdir()

    manifest = nm.build()
    packs = {p["name"]: p for p in manifest["packs"]}
    assert set(packs) == {"ComfyUI-Florence2", "rgthree-comfy", "my-nodes", "single_file"}
    assert packs["ComfyUI-Florence2"] == {"name": "ComfyUI-Florence2", "source": "registry", "id": "comfyui-florence2", "version": "1.0.0"}
    assert packs["rgthree-comfy"] == {"name": "rgthree-comfy", "source": "git", "url": "https://github.com/rgthree/rgthree-comfy.git", "commit": "d" * 40}
    assert packs["my-nodes"]["source"] == "local" and not packs["my-nodes"]["is_file"]
    assert packs["single_file"]["filename"] == "single_file.py"

    skipped = {s["name"]: s["reason"] for s in manifest["skipped"]}
    assert set(skipped) == {"old-pack.disabled", "ComfyUI-Manager"}
    assert manifest["comfyui_version"] == "v0.44.0"
    assert manifest["hash"] == nm.manifest_hash(manifest)

    labels = {p["name"]: p["label"] for p in nm.summary(manifest)["packs"]}
    assert labels["ComfyUI-Florence2"] == "comfyui-florence2 1.0.0 (Comfy Registry)"
    assert labels["rgthree-comfy"] == f"rgthree/rgthree-comfy @ {'d' * 8}"
    assert labels["my-nodes"] == "uploaded from this computer"


def test_local_hash_ignores_caches_but_sees_edits(tmp_path):
    pack = tmp_path / "pack"
    (pack / "node_modules").mkdir(parents=True)
    (pack / "a.py").write_text("x = 1\n")
    _, before = nm._content_hash(pack)
    (pack / "node_modules" / "big.js").write_text("junk")
    (pack / "b.pyc").write_bytes(b"junk")
    assert nm._content_hash(pack)[1] == before
    (pack / "a.py").write_text("x = 2\n")
    assert nm._content_hash(pack)[1] != before


def test_too_big_local_pack_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(nm, "LOCAL_MAX_BYTES", 10)
    pack = tmp_path / "huge"
    pack.mkdir()
    (pack / "model.bin").write_bytes(b"x" * 100)
    assert "Too big to upload" in nm._describe(pack)["skip"]
