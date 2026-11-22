import os
from pathlib import Path

import folder_paths
import pytest

from comfyless.bridge import models_sync


@pytest.fixture
def cloud():
    return models_sync.CloudModels()


def _local_model(kind: str, rel: str) -> Path:
    path = Path(folder_paths.get_folder_paths(kind)[0], rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"weights")
    return path


def _placeholder(rel: str) -> Path:
    return models_sync.PLACEHOLDER_ROOT / rel


def test_apply_creates_placeholders_and_registers_folders(cloud):
    created, removed = cloud.apply(["checkpoints/sdxl.safetensors", "loras/style/ink.safetensors"])
    assert (created, removed) == (2, 0)
    assert _placeholder("checkpoints/sdxl.safetensors").stat().st_size == 0
    assert _placeholder("loras/style/ink.safetensors").is_file()
    assert "sdxl.safetensors" in folder_paths.get_filename_list("checkpoints")
    assert cloud.by_kind == {"checkpoints": {"sdxl.safetensors"}, "loras": {"style/ink.safetensors"}}


def test_apply_maps_legacy_folder_names(cloud):
    cloud.apply(["unet/flux.safetensors"])
    assert cloud.by_kind == {"diffusion_models": {"flux.safetensors"}}


def test_apply_removes_placeholders_no_longer_in_the_cloud(cloud):
    cloud.apply(["checkpoints/a.safetensors", "checkpoints/b.safetensors"])
    created, removed = cloud.apply(["checkpoints/a.safetensors"])
    assert (created, removed) == (0, 1)
    assert not _placeholder("checkpoints/b.safetensors").exists()


def test_apply_skips_files_that_are_really_on_this_computer(cloud):
    _local_model("checkpoints", "real.safetensors")
    cloud.apply(["checkpoints/real.safetensors"])
    assert not _placeholder("checkpoints/real.safetensors").exists()
    assert cloud.by_kind == {"checkpoints": {"real.safetensors"}}


def test_apply_ignores_files_at_the_volume_root(cloud):
    assert cloud.apply(["README.md"]) == (0, 0)


def test_check_rewrites_model_paths_to_forward_slashes(cloud):
    _local_model("loras", "style/ink.safetensors")
    local_name = os.path.join("style", "ink.safetensors")
    prompt = {"1": {"class_type": "LoraLoader", "inputs": {"lora_name": local_name, "strength": 1.0}}}
    errors, refs = cloud.check_and_rewrite(prompt)
    assert errors == {}  # no sync yet, so nothing is reported missing
    assert refs == {("1", "lora_name")}
    assert prompt["1"]["inputs"]["lora_name"] == "style/ink.safetensors"


def test_check_reports_models_only_on_this_computer(cloud):
    _local_model("checkpoints", "local_only.safetensors")
    cloud.apply(["checkpoints/in_cloud.safetensors"])
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "local_only.safetensors"}},
        "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "in_cloud.safetensors"}},
    }
    errors, refs = cloud.check_and_rewrite(prompt)
    assert set(errors) == {"1"}
    error = errors["1"]["errors"][0]
    assert error["type"] == "custom_validation_failed"
    assert error["extra_info"]["modal_bridge_model"] == {"folder": "checkpoints", "name": "local_only.safetensors"}
    assert refs == {("1", "ckpt_name"), ("2", "ckpt_name")}


def test_check_uses_the_dropdown_to_tell_apart_same_named_models(cloud, node_types):
    # model.safetensors is a local checkpoint and also a LoRA in the cloud. The checkpoint loader's
    # dropdown only lists checkpoints, so the cloud LoRA must not count as the checkpoint.
    _local_model("checkpoints", "model.safetensors")
    cloud.apply(["loras/model.safetensors", "loras/other.safetensors"])

    class CheckpointLoaderSimple:
        @staticmethod
        def INPUT_TYPES():
            return {"required": {"ckpt_name": (folder_paths.get_filename_list("checkpoints"),)}}

    node_types({"CheckpointLoaderSimple": CheckpointLoaderSimple})
    prompt = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}}}
    errors, _ = cloud.check_and_rewrite(prompt)
    assert errors["1"]["errors"][0]["extra_info"]["modal_bridge_model"]["folder"] == "checkpoints"


def test_combo_options_reads_both_node_apis(node_types):
    class Old:
        @staticmethod
        def INPUT_TYPES():
            return {"required": {"a": (["x", "y"],), "seed": ("INT", {})}}

    class New:
        @staticmethod
        def INPUT_TYPES():
            return {"optional": {"b": ("COMBO", {"options": ["p", "q"]})}}

    class Broken:
        @staticmethod
        def INPUT_TYPES():
            raise RuntimeError("custom node bug")

    node_types({"Old": Old, "New": New, "Broken": Broken})
    cache: dict = {}
    assert models_sync._combo_options("Old", cache) == {"a": {"x", "y"}}
    assert models_sync._combo_options("New", cache) == {"b": {"p", "q"}}
    assert models_sync._combo_options("Broken", cache) == {}
    assert models_sync._combo_options("Missing", cache) == {}
