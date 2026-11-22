import json

from comfyless.bridge import config


def test_load_returns_defaults_without_a_file():
    data = config.load()
    assert data["setup_done"] is False
    assert data["spend"] == {"runs": 0, "gpu_seconds": 0.0, "usd": 0.0}


def test_load_returns_a_copy_of_the_defaults():
    config.load()["spend"]["runs"] = 99
    assert config.load()["spend"]["runs"] == 0


def test_update_persists_and_ignores_unknown_stored_keys():
    config.update(setup_done=True, secret_keys=["HF_TOKEN"])
    path = config._path()
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["something_else"] = 1
    path.write_text(json.dumps(stored), encoding="utf-8")

    data = config.load()
    assert data["setup_done"] is True
    assert data["secret_keys"] == ["HF_TOKEN"]
    assert "something_else" not in data
    assert not path.with_suffix(".tmp").exists()


def test_unreadable_file_falls_back_to_defaults():
    path = config._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert config.load()["setup_done"] is False


def test_add_spend_accumulates():
    config.add_spend(10.5, 0.01)
    spend = config.add_spend(4.5, 0.002)
    assert spend == {"runs": 2, "gpu_seconds": 15.0, "usd": 0.012}
    assert config.load()["spend"] == spend
