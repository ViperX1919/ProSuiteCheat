"""Verify prediction leads a moving target instead of trailing it.

Time is simulated by patching the clock, so results don't depend on the
machine and no real input is sent.
"""
import os, sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from prosuite.core import detection as D
from prosuite.core.kalman import KalmanFilter

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


class FakeAimer:
    smoothness, speed = 1, 10

    def reset_carry(self):
        pass


REGION = {"left": 0, "top": 0}
clock = [1000.0]
D.time.perf_counter = lambda: clock[0]


def track(fps, lead_units, seconds=1.5, jitter=1.5, turn_at=None, seed=1):
    """Mean aim error against the target's future position, last 10 frames."""
    rng = np.random.default_rng(seed)
    worker = D.DetectionWorker(FakeAimer())
    cfg = D.DetectionSettings(fov=3000, prediction_enabled=True, prediction_factor=lead_units)
    dt = 1 / fps
    v = np.array([300.0, 0.0])
    p = np.array([100.0, 300.0])
    errs = []
    for i in range(int(fps * seconds)):
        clock[0] += dt
        p = p + v * dt
        if turn_at is not None and i == int(fps * turn_at):
            v = np.array([-200.0, 150.0])
        m = p + rng.normal(0, jitter, 2)
        ax, ay, _, _ = worker._apply_prediction(m.copy(), m.copy(), (0, 0), REGION, cfg)
        errs.append(np.array([ax, ay]) - (p + v * lead_units * 0.01))
    return float(np.abs(np.array(errs[-10:])).mean()), worker


err, _ = track(60, 5)
check("aim leads target at 60fps (no lag)", err < 6, f"{err:.2f}px")

errs = [track(fps, 5)[0] for fps in (30, 60, 144)]
check("error independent of frame rate", max(errs) < 6, " ".join(f"{e:.1f}" for e in errs))

err, _ = track(60, 5, seconds=2.0, turn_at=0.75)
check("recovers after a sudden turn", err < 6, f"{err:.2f}px")

# Gate: a different blob far away must not become a huge velocity spike.
err, worker = track(60, 5)
before = np.linalg.norm(worker._kalman.get_velocity())
clock[0] += 1 / 60
cfg = D.DetectionSettings(fov=3000, prediction_enabled=True, prediction_factor=5)
far = np.array([2500.0, 2500.0])
worker._apply_prediction(far, far, (0, 0), REGION, cfg)
check("target switch resets the track", np.linalg.norm(worker._kalman.get_velocity()) < 1.0,
      f"velocity {before:.0f} -> {np.linalg.norm(worker._kalman.get_velocity()):.0f}px/s")

# Filter sanity.
kf = KalmanFilter()
conf = []
for i in range(60):
    kf.update(np.array([i * 5.0, 0.0]), 1 / 60)
    conf.append(kf.get_prediction_confidence())
check("confidence zero until warmed up", conf[0] == 0.0 and conf[2] == 0.0)
check("confidence rises with a clean track", 0.5 < conf[-1] <= 1.0, f"{conf[-1]:.3f}")
check("velocity learned in px/s", abs(kf.get_velocity()[0] - 300) < 15, f"{kf.get_velocity()[0]:.0f}")
check("filter learns velocity itself (cross-covariance)", abs(kf.P[0, 2]) > 0)
kf.reset()
check("kalman resets", not kf.initialized)

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
