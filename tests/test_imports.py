"""Every bridge module imports without ComfyUI or a Modal sign-in."""

import importlib

import pytest

MODULES = [
    "comfyless.backend.app",
    "comfyless.backend.deploy",
    "comfyless.bridge.cloud_nodes",
    "comfyless.bridge.config",
    "comfyless.bridge.errors",
    "comfyless.bridge.inputs",
    "comfyless.bridge.modal_client",
    "comfyless.bridge.model_packs",
    "comfyless.bridge.models_manager",
    "comfyless.bridge.models_sync",
    "comfyless.bridge.nodes_manifest",
    "comfyless.bridge.pricing",
    "comfyless.bridge.routes",
    "comfyless.bridge.runner",
    "comfyless.bridge.setup",
]


@pytest.mark.parametrize("name", MODULES)
def test_imports(name):
    importlib.import_module(name)
