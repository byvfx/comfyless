import pytest

from comfyless.bridge import pricing


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(pricing, "_last_end", {})
    monkeypatch.setattr(pricing, "_live", None)


def test_cold_start_adds_overhead_and_boot():
    c = pricing.charge("L40S", 300, {"total_s": 10.0, "cold": True, "boot_s": 40.0}, started_at=1000, ended_at=1050)
    assert c.run_s == 10.0 + pricing.COLD_START_OVERHEAD_S + 40.0
    assert c.idle_s == 300
    assert c.added_s == c.run_s + 300


def test_warm_run_bills_only_the_wait_since_the_last_run():
    pricing.charge("L40S", 300, {"total_s": 10.0, "cold": True, "boot_s": 0.0}, started_at=0, ended_at=100)
    c = pricing.charge("L40S", 300, {"total_s": 5.0}, started_at=160, ended_at=165)
    assert c.run_s == 5.0 + 60  # 60 s of keep-warm were used up waiting for this run
    # The previous keep-warm window was counted in full; this run replaces it with the wait plus a new window.
    assert c.added_s == c.run_s


def test_wait_is_capped_at_the_keep_warm_window():
    pricing.charge("H100", 60, {"total_s": 1.0, "cold": True}, started_at=0, ended_at=10)
    c = pricing.charge("H100", 60, {"total_s": 1.0}, started_at=10_000, ended_at=10_001)
    assert c.run_s == 1.0 + 60


def test_each_gpu_and_window_is_its_own_container():
    pricing.charge("L40S", 300, {"total_s": 1.0, "cold": True}, started_at=0, ended_at=10)
    c = pricing.charge("L40S", 600, {"total_s": 1.0}, started_at=20, ended_at=21)
    assert c.run_s == 1.0  # no previous run on this (gpu, window) pair
    assert c.added_s == 1.0 + 600


def test_cost_uses_live_rates_then_fallback_table():
    assert pricing.cost("H100", 3600) == pytest.approx(3.95)
    assert pricing.cost("unknown-gpu", 3600) == pytest.approx(1.95)  # priced as L40S
    pricing.from_modal_rates({"gpu_hour_cost_h100": "4.50"})
    assert pricing.cost("H100", 3600) == pytest.approx(4.50)
    assert pricing.cost("L40S", 3600) == pytest.approx(1.95)  # missing from the rate card: fallback kept
    assert pricing.cost("H100", 3600, rates={"H100": 1 / 3600, "L40S": 0}) == pytest.approx(1.0)


@pytest.mark.parametrize("usd, text", [(0, "<$0.01"), (0.0099, "<$0.01"), (0.01, "$0.01"), (1.234, "$1.23"), (12.5, "$12.50")])
def test_fmt_usd(usd, text):
    assert pricing.fmt_usd(usd) == text


def test_idle_note_formats_the_window():
    assert "stays on for 5 min" in pricing.idle_note("L40S", 300)
    assert "stays on for 30 s" in pricing.idle_note("L40S", 30)
    assert "$0.16" in pricing.idle_note("L40S", 300)
