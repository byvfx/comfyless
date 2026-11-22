import pytest

from comfyless.bridge import errors
from comfyless.bridge import models_manager as mm


@pytest.mark.parametrize(
    "pasted, direct",
    [
        ("https://huggingface.co/org/repo/blob/main/model.safetensors",
         "https://huggingface.co/org/repo/resolve/main/model.safetensors"),
        ("https://hf.co/org/repo/blob/main/sub/model.safetensors?download=true",
         "https://huggingface.co/org/repo/resolve/main/sub/model.safetensors"),
        ("https://huggingface.co/org/repo/resolve/main/model.safetensors",
         "https://huggingface.co/org/repo/resolve/main/model.safetensors"),
        ("  https://civitai.com/api/download/models/12345  ", "https://civitai.com/api/download/models/12345"),
        ("https://civitai.green/api/download/models/12345?type=Model", "https://civitai.com/api/download/models/12345?type=Model"),
        ("https://civitai.com/models/100/some-lora?modelVersionId=777", "https://civitai.com/api/download/models/777"),
        ("https://example.com/files/model.safetensors", "https://example.com/files/model.safetensors"),
    ],
)
def test_normalize_url(pasted, direct):
    assert mm.normalize_url(pasted) == direct


def test_civitai_page_without_a_version_looks_up_the_latest(monkeypatch):
    looked_up = []
    monkeypatch.setattr(mm, "_civitai_latest_version", lambda model_id: looked_up.append(model_id) or "999")
    assert mm.normalize_url("https://civitai.com/models/100/some-lora") == "https://civitai.com/api/download/models/999"
    assert looked_up == ["100"]


@pytest.mark.parametrize(
    "pasted, message",
    [
        ("http://huggingface.co/org/repo/resolve/main/x.safetensors", "https://"),
        ("ftp://example.com/x", "https://"),
        ("", "https://"),
        ("https://huggingface.co/org/repo", "repository, not a file"),
        ("https://civitai.com/user/someone", "Civitai model page"),
    ],
)
def test_normalize_url_rejects_unusable_links(pasted, message):
    with pytest.raises(errors.BridgeError, match=message):
        mm.normalize_url(pasted)


@pytest.mark.parametrize(
    "name, cleaned",
    [
        ("model.safetensors", "model.safetensors"),
        ("  ../evil.safetensors ", "_evil.safetensors"),
        ('a:b*c?"d<e>f|g.ckpt', "a_b_c__d_e_f_g.ckpt"),
        ("sub\\dir/x.pt", "sub_dir_x.pt"),
    ],
)
def test_clean_filename(name, cleaned):
    assert mm._clean_filename(name) == cleaned


def test_clean_filename_refuses_partial_downloads():
    with pytest.raises(errors.BridgeError):
        mm._clean_filename("model.safetensors.partial")


def test_download_error_messages():
    assert mm._download_error(TypeError("download_model() got an unexpected keyword argument 'progress_key'")) == mm._REDEPLOY
    assert mm._download_error(FileExistsError("x is already in your cloud.")) == "x is already in your cloud."
    assert "404" in mm._download_error(RuntimeError("HTTPError: 404 Client Error: Not Found"))
    assert "Can't reach Modal" in mm._download_error(ConnectionError("down"))
