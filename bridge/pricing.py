"""GPU prices for the cost display (spec §5.5).

The bridge asks Modal for the workspace's current rate card (Workspace.billing.rates) and falls
back to this table, from https://modal.com/pricing, when that fails. GPU time only; CPU and
memory add about 3%. These are estimates. Modal's billing report (shown in Check setup) is the
real number.
"""

from __future__ import annotations

from typing import NamedTuple

USD_PER_SECOND = {
    "L40S": 1.95 / 3600,
    "A100-80GB": 2.50 / 3600,
    "H100": 3.95 / 3600,
}

_live: dict[str, float] | None = None  # set once Modal's rate card has been fetched

# Bridge GPU name -> key in Modal's rate card.
_RATE_KEYS = {"L40S": "gpu_hour_cost_l40s", "A100-80GB": "gpu_hour_cost_a100_80gb", "H100": "gpu_hour_cost_h100"}


def from_modal_rates(rates) -> dict[str, float]:
    """Convert Modal's rate card to USD per second, and use it for later estimates."""
    global _live
    table = dict(USD_PER_SECOND)
    for gpu, key in _RATE_KEYS.items():
        hourly = rates.get(key)
        if hourly is not None:
            table[gpu] = float(hourly) / 3600
    _live = table
    return table


# Modal bills about this much GPU time per cold start before the worker's boot timer begins
# (scheduling, loading the image). Measured with scripts/cost_test.py; see spec §7.5.
COLD_START_OVERHEAD_S = 25.0

# (gpu, keep-warm seconds) -> when the last run on that GPU finished. Each pair is its own container.
_last_end: dict[tuple[str, int], float] = {}


class Charge(NamedTuple):
    run_s: float  # this run, plus the cold start or the idle wait since the previous run
    idle_s: float  # keep-warm after this run, billed if nothing else runs
    added_s: float  # change to the running total, which counts each keep-warm window in full until a run cuts it short


def charge(gpu: str, scaledown_s: int, timings: dict, started_at: float, ended_at: float) -> Charge:
    """Billed GPU time for one run, including the keep-warm idle time around it."""
    key = (gpu, scaledown_s)
    run_s = float(timings.get("total_s", 0.0))
    added_s = 0.0
    prev = _last_end.get(key)
    if timings.get("cold"):
        run_s += COLD_START_OVERHEAD_S + float(timings.get("boot_s", 0.0))
    elif prev is not None:
        # The previous run's keep-warm window was counted in full; only the wait until now was used.
        waited = min(max(0.0, started_at - prev), scaledown_s)
        run_s += waited
        added_s -= scaledown_s
    _last_end[key] = ended_at
    return Charge(run_s, float(scaledown_s), added_s + run_s + scaledown_s)


def cost(gpu: str, seconds: float, rates: dict[str, float] | None = None) -> float:
    table = rates or _live or USD_PER_SECOND
    return seconds * table.get(gpu, table["L40S"])


def idle_note(gpu: str, scaledown_s: int, rates: dict[str, float] | None = None) -> str:
    """Plain-language note on the keep-warm idle cost after a run."""
    window = f"{scaledown_s // 60} min" if scaledown_s >= 60 else f"{scaledown_s} s"
    return (f"The GPU stays on for {window} in case you run again; "
            f"that idle time costs about {fmt_usd(cost(gpu, scaledown_s, rates))} if you don't.")


def fmt_usd(usd: float) -> str:
    if usd < 0.01:
        return "<$0.01"
    return f"${usd:.2f}"
