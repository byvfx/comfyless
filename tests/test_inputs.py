import asyncio
import hashlib
from pathlib import Path

import folder_paths

from comfyless.bridge import inputs


def _write(directory: str, rel: str, data: bytes = b"pixels") -> Path:
    path = Path(directory, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_collect_finds_input_files_and_rewrites_paths():
    _write(folder_paths.get_input_directory(), "photos/cat.PNG")
    prompt = {"1": {"class_type": "LoadImage", "inputs": {"image": "photos/cat.PNG"}}}
    found = inputs.collect(prompt, skip=set())
    assert len(found) == 1
    assert found[0].ext == "png"
    assert found[0].staged_as == "photos/cat.PNG"
    assert prompt["1"]["inputs"]["image"] == "photos/cat.PNG"  # forward slashes for the Linux worker


def test_collect_namespaces_output_and_temp_files():
    _write(folder_paths.get_output_directory(), "render_00001_.png")
    _write(folder_paths.get_temp_directory(), "preview.png")
    prompt = {
        "1": {"inputs": {"image": "render_00001_.png [output]"}},
        "2": {"inputs": {"image": "preview.png [temp]"}},
    }
    found = {f.staged_as for f in inputs.collect(prompt, skip=set())}
    assert found == {"_output/render_00001_.png", "_temp/preview.png"}
    assert prompt["1"]["inputs"]["image"] == "_output/render_00001_.png"


def test_collect_ignores_models_missing_files_and_non_strings():
    _write(folder_paths.get_input_directory(), "mask.png")
    prompt = {
        "1": {"inputs": {"image": "mask.png", "seed": 5, "text": "a cat"}},
        "2": {"inputs": {"ckpt_name": "mask.png"}},
    }
    found = inputs.collect(prompt, skip={("2", "ckpt_name")})
    assert [f.staged_as for f in found] == ["mask.png"]
    assert prompt["1"]["inputs"]["text"] == "a cat"


def test_collect_refuses_paths_outside_the_input_directory(comfy_dirs):
    _write(str(comfy_dirs), "secret.txt")
    prompt = {"1": {"inputs": {"image": "../secret.txt"}}}
    assert inputs.collect(prompt, skip=set()) == []
    assert prompt["1"]["inputs"]["image"] == "../secret.txt"


def test_collect_uploads_a_file_used_twice_once():
    _write(folder_paths.get_input_directory(), "ref.png")
    prompt = {"1": {"inputs": {"image": "ref.png"}}, "2": {"inputs": {"image": "ref.png"}}}
    assert len(inputs.collect(prompt, skip=set())) == 1


class FakeClient:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.uploaded: list[tuple[str, str]] = []

    async def existing_inputs(self) -> set[str]:
        return self.existing

    def upload_inputs_blocking(self, pairs):
        # The snapshot must still exist while the upload runs.
        assert all(Path(local).is_file() for local, _ in pairs)
        self.uploaded += pairs


def test_upload_missing_skips_files_already_in_the_cloud():
    a = _write(folder_paths.get_input_directory(), "a.png", b"aaa")
    b = _write(folder_paths.get_input_directory(), "b.png", b"bbb")
    sha_a = hashlib.sha256(b"aaa").hexdigest()
    sha_b = hashlib.sha256(b"bbb").hexdigest()
    files = [inputs.InputFile(a, "png", "a.png"), inputs.InputFile(b, "png", "b.png")]
    client = FakeClient(existing={f"{sha_a}.png"})

    assert asyncio.run(inputs.upload_missing(client, files)) == 1
    assert [remote for _, remote in client.uploaded] == [f"inputs/{sha_b}.png"]
    assert files[0].as_job_entry() == {"sha256": sha_a, "ext": "png", "as": "a.png"}


def test_upload_missing_with_nothing_to_send():
    assert asyncio.run(inputs.upload_missing(FakeClient(set()), [])) == 0
