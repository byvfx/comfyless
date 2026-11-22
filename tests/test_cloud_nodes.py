import pytest

from comfyless.bridge.cloud_nodes import CloudNodes


def _node_class(module: str):
    return type("Node", (), {"RELATIVE_PYTHON_MODULE": module})


@pytest.fixture
def registered(node_types):
    node_types({
        "KSampler": _node_class("nodes"),
        "Florence2": _node_class("custom_nodes.ComfyUI-Florence2"),
        "Rembg": _node_class("custom_nodes.rembg-pack"),
        "Secret": _node_class("custom_nodes.private-pack"),
        "Broken": _node_class("custom_nodes.broken-pack"),
    })


def _cloud(deployed=None, record=None) -> CloudNodes:
    c = CloudNodes()
    c.deployed, c.record = deployed, record
    return c


def _prompt(*class_types):
    return {str(i): {"class_type": t, "inputs": {}} for i, t in enumerate(class_types, 1)}


def test_nothing_known_means_no_errors(registered):
    assert _cloud().check(_prompt("Anything")) == {}


def test_exact_node_list_from_a_current_worker(registered):
    c = _cloud(
        deployed={"manifest_hash": "abc", "comfyui_version": "v0.44.0"},
        record={"manifest_hash": "abc", "classes": ["KSampler", "Florence2"], "comfyui_version": "v0.44.0"},
    )
    assert c.current
    errors = c.check(_prompt("KSampler", "Florence2", "Rembg", "BrandNewCoreNode"))
    assert set(errors) == {"3", "4"}
    assert "isn't in the cloud's ComfyUI v0.44.0" in errors["4"]["errors"][0]["details"]


def test_stale_worker_record_falls_back_to_pack_results(registered):
    c = _cloud(
        deployed={
            "manifest_hash": "new",
            "packs": [
                {"name": "ComfyUI-Florence2", "status": "ok"},
                {"name": "broken-pack", "status": "failed", "step": "pip install", "error": "no wheel for torch"},
            ],
            "skipped": [{"name": "private-pack", "reason": "Too big to upload."}],
        },
        record={"manifest_hash": "old", "classes": []},
    )
    assert not c.current
    errors = c.check(_prompt("KSampler", "Florence2", "Rembg", "Secret", "Broken"))
    assert set(errors) == {"3", "4", "5"}  # core nodes and built packs are assumed to load
    details = {k: v["errors"][0]["details"] for k, v in errors.items()}
    assert "isn't in your cloud yet" in details["3"]
    assert "isn't mirrored to the cloud: Too big to upload." in details["4"]
    assert "failed to install in the cloud (pip install): no wheel for torch" in details["5"]


def test_no_build_report_means_no_guessing(registered):
    c = _cloud(deployed={"manifest_hash": "abc"}, record=None)
    assert c.check(_prompt("Florence2")) == {}


def test_core_only_deploy_matches_a_core_worker(registered):
    c = _cloud(deployed={"manifest_hash": None}, record={"manifest_hash": "core", "classes": ["KSampler"]})
    assert c.current
    assert set(c.check(_prompt("KSampler", "Florence2"))) == {"2"}


def test_pack_built_but_node_missing(registered):
    c = _cloud(
        deployed={"manifest_hash": "abc", "packs": [{"name": "rembg-pack", "status": "ok"}]},
        record={"manifest_hash": "abc", "classes": ["KSampler"]},
    )
    details = c.check(_prompt("Rembg"))["1"]["errors"][0]["details"]
    assert "is installed in the cloud, but its Rembg node didn't load there" in details
