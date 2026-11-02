"""Verify the triggerbot delay actually rate-limits, without sending clicks."""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from prosuite.core.detection import DetectionSettings, DetectionWorker

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


class FakeAimer:
    """Stands in for SmoothAimer so no real mouse input is ever sent."""

    def __init__(self):
        self.clicks = []

    def click(self):
        self.clicks.append(time.perf_counter())


def run(delay_ms, duration=0.6, fps=144):
    aimer = FakeAimer()
    worker = DetectionWorker(aimer)
    cfg = DetectionSettings(fov=20, trigger_enabled=True, trigger_delay=delay_ms)
    # Solid mask: the colour is under the crosshair for the whole run.
    mask = np.full((40, 40), 255, dtype=np.uint8)

    end = time.perf_counter() + duration
    while time.perf_counter() < end:
        worker._maybe_trigger(mask, cfg)
        time.sleep(1 / fps)
    return aimer.clicks, duration


# --- 0 ms: unlimited, reproduces the machine-gunning ---
clicks, dur = run(0)
rate_free = len(clicks) / dur
check("delay 0 fires every frame", rate_free > 100, f"{rate_free:.0f} clicks/sec")

# --- 100 ms: should settle near 10/sec ---
clicks, dur = run(100)
rate = len(clicks) / dur
gaps = [b - a for a, b in zip(clicks, clicks[1:])]
check("delay 100 limits the rate", 8 <= rate <= 12, f"{rate:.1f} clicks/sec")
check("no gap shorter than the delay", all(g >= 0.099 for g in gaps),
      f"min gap {min(gaps) * 1000:.1f}ms")
check("delay is fixed, not randomised",
      max(gaps) - min(gaps) < 0.02, f"spread {(max(gaps) - min(gaps)) * 1000:.1f}ms")

# --- 500 ms, the slider maximum ---
clicks, dur = run(500, duration=1.6)
check("delay 500 fires about twice a second", len(clicks) == 4, f"{len(clicks)} clicks")

# --- an empty mask must never fire, and must not consume the cooldown ---
aimer = FakeAimer()
worker = DetectionWorker(aimer)
cfg = DetectionSettings(fov=20, trigger_enabled=True, trigger_delay=50)
empty = np.zeros((40, 40), dtype=np.uint8)
for _ in range(50):
    worker._maybe_trigger(empty, cfg)
check("no colour under crosshair means no click", not aimer.clicks, f"{len(aimer.clicks)}")

solid = np.full((40, 40), 255, dtype=np.uint8)
worker._maybe_trigger(solid, cfg)
check("fires immediately once colour appears", len(aimer.clicks) == 1, f"{len(aimer.clicks)}")

# --- reaction delay: a fixed debounce before the first shot ---
def first_shot_latency(reaction_ms, repeat_ms=0, fps=240):
    aimer = FakeAimer()
    worker = DetectionWorker(aimer)
    cfg = DetectionSettings(fov=20, trigger_enabled=True, trigger_delay=repeat_ms,
                            trigger_reaction=reaction_ms)
    solid = np.full((40, 40), 255, dtype=np.uint8)
    start = time.perf_counter()
    while not aimer.clicks and time.perf_counter() - start < 1.0:
        worker._maybe_trigger(solid, cfg)
        time.sleep(1 / fps)
    return (aimer.clicks[0] - start) if aimer.clicks else None

lat = [first_shot_latency(120) for _ in range(3)]
check("first shot waits for the reaction delay", all(0.115 <= l <= 0.145 for l in lat),
      " ".join(f"{l * 1000:.0f}ms" for l in lat))
check("reaction delay is fixed, not randomised", max(lat) - min(lat) < 0.02,
      f"spread {(max(lat) - min(lat)) * 1000:.1f}ms")

aimer = FakeAimer()
worker = DetectionWorker(aimer)
cfg = DetectionSettings(fov=20, trigger_enabled=True, trigger_reaction=100)
solid = np.full((40, 40), 255, dtype=np.uint8)
gone = np.zeros((40, 40), dtype=np.uint8)
t0 = time.perf_counter()
while time.perf_counter() - t0 < 0.08:      # colour present, not long enough
    worker._maybe_trigger(solid, cfg); time.sleep(0.004)
worker._maybe_trigger(gone, cfg)            # colour leaves the crosshair
t1 = time.perf_counter()
while time.perf_counter() - t1 < 0.08:      # back again - timer must restart
    worker._maybe_trigger(solid, cfg); time.sleep(0.004)
check("colour leaving the crosshair restarts the reaction timer", not aimer.clicks,
      f"{len(aimer.clicks)} clicks")

# Repeat interval still applies after the first shot.
aimer = FakeAimer()
worker = DetectionWorker(aimer)
cfg = DetectionSettings(fov=20, trigger_enabled=True, trigger_reaction=50, trigger_delay=200)
end = time.perf_counter() + 0.9
while time.perf_counter() < end:
    worker._maybe_trigger(solid, cfg); time.sleep(0.004)
gaps = [b - a for a, b in zip(aimer.clicks, aimer.clicks[1:])]
check("fast first shot then spaced repeats", len(aimer.clicks) in (4, 5) and all(g >= 0.199 for g in gaps),
      f"{len(aimer.clicks)} clicks, min gap {min(gaps) * 1000:.0f}ms")

print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
