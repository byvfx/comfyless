"""Helpers in the cloud worker that run without a container."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from comfyless.backend import app


@pytest.mark.parametrize("tag, expected", [("v0.44.0", (0, 44, 0)), ("0.45.1", (0, 45, 1)), ("v1.0.0-rc1", None), (None, None), ("", None)])
def test_version_tuple(tag, expected):
    assert app._version_tuple(tag) == expected


@pytest.mark.parametrize(
    "local, cloud",
    [
        ("v0.45.0", "v0.45.0"),
        ("v0.44.0", "v0.44.0"),
        ("v0.43.9", app.DEFAULT_COMFYUI_VERSION),  # older than the backend is tested with
        ("v1.0.0-rc1", app.DEFAULT_COMFYUI_VERSION),
        (None, app.DEFAULT_COMFYUI_VERSION),
    ],
)
def test_comfyui_version(local, cloud):
    assert app._comfyui_version({"comfyui_version": local}) == cloud


def test_safe_join_refuses_traversal(tmp_path):
    assert app._safe_join(tmp_path, "checkpoints/a.safetensors") == (tmp_path / "checkpoints" / "a.safetensors").resolve()
    for bad in ("../outside", "checkpoints/../../outside", "/etc/passwd"):
        with pytest.raises(ValueError):
            app._safe_join(tmp_path, bad)


def _response(disposition: str = "", url: str = "https://example.com/files/model.safetensors"):
    return SimpleNamespace(headers={"content-disposition": disposition} if disposition else {}, url=url)


@pytest.mark.parametrize(
    "resp, name",
    [
        (_response('attachment; filename="lora v2.safetensors"'), "lora v2.safetensors"),
        (_response("attachment; filename=plain.ckpt"), "plain.ckpt"),
        (_response("attachment; filename*=UTF-8''caf%C3%A9.safetensors; filename=\"cafe.safetensors\""), "café.safetensors"),
        (_response("", "https://cdn.example.com/a/b/my%20model.safetensors?sig=abc"), "my model.safetensors"),
        (_response('attachment; filename="../../etc/passwd"'), "_.._etc_passwd"),
    ],
)
def test_download_filename(resp, name):
    assert app._download_filename(resp) == name


def test_download_filename_needs_a_name():
    with pytest.raises(ValueError):
        app._download_filename(_response("", "https://example.com/"))


def _frame(event: int, payload: bytes = b"jpeg") -> bytes:
    return event.to_bytes(4, "big") + payload


def test_relay_binary_forwards_previews_and_throttles(monkeypatch):
    # The plain class, as the container runs it. Modal's wrapper copies arguments, so the
    # throttle's last_sent dict would never be updated through it.
    relay = app.ComfyWorker._get_user_cls()._relay_binary
    sent, last = [], {}
    emit = lambda kind, data: sent.append(data)  # noqa: E731
    now = [100.0]
    monkeypatch.setattr(app.time, "time", lambda: now[0])

    relay(_frame(1), emit, last)
    relay(_frame(1), emit, last)  # within PROGRESS_MIN_INTERVAL_S: dropped
    relay(_frame(3, b"progress text"), emit, last)  # text is never throttled
    now[0] += app.PROGRESS_MIN_INTERVAL_S + 0.01
    relay(_frame(4), emit, last)

    assert [d["event"] for d in sent] == [1, 3, 4]
    assert sent[1]["payload"] == b"progress text"


@pytest.mark.parametrize(
    "raw",
    [
        _frame(2),  # not a forwarded event type
        b"\x00\x01",  # too short to hold an event type
        _frame(1, b"x" * app.PREVIEW_MAX_BYTES),  # too big for a Queue item
    ],
    ids=["event-type", "too-short", "too-big"],
)
def test_relay_binary_rejects_unusable_frames(raw):
    # Fresh throttle state, so only the frame check itself can drop the frame.
    sent = []
    app.ComfyWorker._get_user_cls()._relay_binary(raw, lambda kind, data: sent.append(data), {})
    assert sent == []


def test_source_hash_ignores_line_endings(monkeypatch, tmp_path):
    lf, crlf = tmp_path / "lf.py", tmp_path / "crlf.py"
    lf.write_bytes(b"a = 1\nb = 2\n")
    crlf.write_bytes(b"a = 1\r\nb = 2\r\n")
    monkeypatch.setattr(app, "__file__", str(lf))
    first = app.source_hash()
    monkeypatch.setattr(app, "__file__", str(crlf))
    assert app.source_hash() == first


def test_control_partition_is_separate_from_events():
    assert app.control_partition("job") != "job"


def test_local_pack_ignore_matches_the_bridge():
    from comfyless.bridge import nodes_manifest

    dirs = {Path(p).name for p in app.LOCAL_PACK_IGNORE if not p.endswith(".pyc")}
    assert dirs == nodes_manifest._IGNORE_DIRS
