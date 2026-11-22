import uuid

import pytest
from aiohttp.test_utils import make_mocked_request

from comfyless.bridge import routes


def test_canonical_uuid():
    value = str(uuid.uuid4())
    assert routes._is_canonical_uuid(value)
    assert not routes._is_canonical_uuid(value.upper())
    assert not routes._is_canonical_uuid(value.replace("-", ""))
    assert not routes._is_canonical_uuid("not-a-uuid")
    assert not routes._is_canonical_uuid(None)


def _request(remote: str, content_type: str | None = "application/json"):
    headers = {"Content-Type": content_type} if content_type else {}
    return make_mocked_request("POST", "/modal_bridge/setup/login", headers=headers).clone(remote=remote)


@pytest.mark.parametrize("remote", ["127.0.0.1", "::1", "localhost"])
def test_local_only_allows_this_computer(remote):
    assert routes._local_only(_request(remote)) is None


@pytest.mark.parametrize("remote", ["192.168.1.20", "10.0.0.5", "8.8.8.8", None])
def test_local_only_refuses_the_network(remote):
    response = routes._local_only(_request(remote))
    assert response is not None and response.status == 403


def test_local_only_needs_json_for_changes():
    # A cross-site form post can't send application/json.
    response = routes._local_only(_request("127.0.0.1", "application/x-www-form-urlencoded"))
    assert response is not None and response.status == 415
    assert routes._local_only(_request("127.0.0.1", None), mutating=False) is None


def test_partner_nodes_need_a_comfy_account(node_types):
    node_types({"OpenAIImage": type("OpenAIImage", (), {"API_NODE": True}), "KSampler": type("KSampler", (), {})})
    prompt = {"1": {"class_type": "OpenAIImage", "inputs": {}}, "2": {"class_type": "KSampler", "inputs": {}}}

    errors = routes._api_nodes_without_account(prompt, {})
    assert set(errors) == {"1"}
    assert "Comfy account" in errors["1"]["errors"][0]["message"]

    assert routes._api_nodes_without_account(prompt, {"auth_token_comfy_org": "t"}) == {}
    assert routes._api_nodes_without_account(prompt, {"api_key_comfy_org": "k"}) == {}


def test_a_node_class_that_raises_is_not_a_partner_node(node_types):
    class Weird:
        def __getattr__(self, name):
            raise RuntimeError("bad custom node")

    node_types({"Weird": Weird()})
    assert routes._api_nodes_without_account({"1": {"class_type": "Weird"}}, {}) == {}
