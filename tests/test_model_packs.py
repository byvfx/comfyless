from urllib.parse import urlparse

import pytest

from comfyless.bridge import model_packs


def test_pack_ids_are_unique():
    ids = [p.id for p in model_packs.PACKS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", sorted(model_packs.FILES))
def test_file_links_are_direct_downloads(path):
    url, size = model_packs.FILES[path]
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "huggingface.co"
    assert "/resolve/" in parsed.path  # a file download, not the file's web page
    assert parsed.path.endswith("/" + path.partition("/")[2].rpartition("/")[2])
    assert size > 0
    assert path.count("/") >= 1


def test_every_file_is_used_by_a_pack():
    used = {f for p in model_packs.PACKS for f in p.files}
    assert set(model_packs.FILES) - used == set()


@pytest.mark.parametrize("pack", model_packs.PACKS, ids=lambda p: p.id)
def test_packs_are_complete(pack):
    assert pack.title and pack.blurb
    assert pack.files and pack.templates
    assert len(set(pack.files)) == len(pack.files)
    assert pack.gpu in ("L40S", "A100-80GB", "H100")


def test_get():
    first = model_packs.PACKS[0]
    assert model_packs.get(first.id) is first
    assert model_packs.get("no-such-pack") is None


def test_listing_marks_what_the_cloud_has(monkeypatch):
    monkeypatch.setattr(model_packs, "template_available", lambda _name: True)
    pack = model_packs.PACKS[0]
    have = pack.files[0]
    entry = next(e for e in model_packs.listing({have: 123}) if e["id"] == pack.id)

    files = {f["path"]: f for f in entry["files"]}
    assert files[have]["have"] is True
    assert all(not f["have"] for p, f in files.items() if p != have)
    assert entry["size"] == sum(model_packs.FILES[p][1] for p in pack.files)
    assert entry["missing_size"] == entry["size"] - model_packs.FILES[have][1]
    assert all(t["available"] for t in entry["templates"])
