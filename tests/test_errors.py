import modal.exception as mx
import pytest

from comfyless.bridge import errors


@pytest.mark.parametrize(
    "exc, expected",
    [
        (errors.BridgeError("Already written for you."), "Already written for you."),
        (mx.AuthError("bad"), "Not signed in to Modal"),
        (RuntimeError("Token missing for workspace"), "Not signed in to Modal"),
        (mx.NotFoundError("App not found"), "isn't deployed in your Modal account yet"),
        (RuntimeError("Workspace spend limit reached"), "billing or spending limit"),
        (mx.ResourceExhaustedError("no capacity"), "no free GPUs"),
        (RuntimeError("GPU quota exceeded"), "no free GPUs"),
        (ConnectionError("reset by peer"), "Can't reach Modal"),
        (mx.ConnectionError("grpc down"), "Can't reach Modal"),
    ],
)
def test_friendly_maps_known_errors(exc, expected):
    assert expected in errors.friendly(exc)


def test_friendly_timeout_keeps_its_own_message():
    # TimeoutError is an OSError subclass, so it has to be matched before ConnectionError-like cases.
    assert errors.friendly(TimeoutError("Waited 600 s for the GPU")) == "Waited 600 s for the GPU"
    assert errors.friendly(TimeoutError()) == "Timed out waiting for Modal. Try again."


def test_friendly_falls_back_to_type_and_message():
    assert errors.friendly(KeyError("x")) == "KeyError: 'x'"
    assert errors.friendly(RuntimeError()) == "RuntimeError"


def test_friendly_never_includes_a_traceback():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        assert "Traceback" not in errors.friendly(exc)


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"exception_type": "torch.OutOfMemoryError", "exception_message": ""}, "ran out of memory"),
        ({"exception_type": "RuntimeError", "exception_message": "CUDA out of memory. Tried to allocate"}, "ran out of memory"),
        ({"exception_type": "FileNotFoundError", "exception_message": "No such file or directory: 'x.safetensors'"}, "isn't in your Modal cloud"),
        ({"exception_type": "ValueError", "exception_message": "model foo not found"}, "isn't in your Modal cloud"),
    ],
)
def test_friendly_execution_error_hints(data, expected):
    assert expected in errors.friendly_execution_error(data)


def test_friendly_execution_error_leaves_other_errors_alone():
    assert errors.friendly_execution_error({"exception_type": "ValueError", "exception_message": "bad width"}) is None
    assert errors.friendly_execution_error({}) is None
