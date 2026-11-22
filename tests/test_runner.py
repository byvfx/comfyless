from comfyless.bridge import runner


def test_free_name_keeps_a_free_name(tmp_path):
    assert runner._free_name(tmp_path, "ComfyUI_00001_.png") == tmp_path / "ComfyUI_00001_.png"


def test_free_name_continues_the_local_save_counter(tmp_path):
    # A cold cloud container restarts its counter at 00001; continue after the highest local one.
    for n in (1, 2, 7):
        (tmp_path / f"ComfyUI_{n:05}_.png").write_bytes(b"")
    (tmp_path / "ComfyUI_00099_.jpg").write_bytes(b"")  # another extension doesn't count
    assert runner._free_name(tmp_path, "ComfyUI_00001_.png") == tmp_path / "ComfyUI_00008_.png"


def test_free_name_numbers_other_names(tmp_path):
    (tmp_path / "result.png").write_bytes(b"")
    (tmp_path / "result_1.png").write_bytes(b"")
    assert runner._free_name(tmp_path, "result.png") == tmp_path / "result_2.png"


def test_rename_item():
    renames = {("output", "", "ComfyUI_00001_.png"): ("cloud", "ComfyUI_00008_.png")}
    item = {"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}
    assert runner._rename_item(item, renames) == {"filename": "ComfyUI_00008_.png", "subfolder": "cloud", "type": "output"}
    assert item["filename"] == "ComfyUI_00001_.png"  # not changed in place

    other = {"filename": "x.png", "subfolder": "", "type": "temp"}
    assert runner._rename_item(other, renames) is other
    assert runner._rename_item("text output", renames) == "text output"
    assert runner._rename_item({"no_filename": 1}, renames) == {"no_filename": 1}


def test_cloud_job_repr_hides_the_comfy_token():
    job = runner.CloudJob("id", {}, None, None, "L40S", 300, None, extra_data={"auth_token_comfy_org": "secret-token"})
    assert "secret-token" not in repr(job)
