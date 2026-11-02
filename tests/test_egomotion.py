"""Prediction must work when the aim holds the target still on screen.

A textured world scrolls past a camera that follows a moving target, so the
target sits at the crosshair while its true velocity is high. The tracker has
to recover that velocity from the scene shift. Synthetic frames only.
"""
import os, sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from prosuite.core import detection as D
from prosuite.core.egomotion import EgoMotion

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


class FakeAimer:
    smoothness, speed = 1, 10

    def reset_carry(self):
        pass

    def take_sent(self):
        return (0, 0)


REGION = {"left": 0, "top": 0}
SIZE, HALF = 400, 200
clock = [1000.0]
D.time.perf_counter = lambda: clock[0]

rng = np.random.default_rng(3)
noise = rng.integers(0, 255, (3000, 3000), dtype=np.uint8)
TEXTURED = cv2.cvtColor(cv2.GaussianBlur(noise, (0, 0), 6), cv2.COLOR_GRAY2BGR)
BLANK = np.full((3000, 3000, 3), 90, np.uint8)


def render(world, cam, target_world):
    x, y = int(round(cam[0])), int(round(cam[1]))
    frame = world[y - HALF : y + HALF, x - HALF : x + HALF].copy()
    tx, ty = target_world - cam + HALF
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.circle(frame, (int(round(tx)), int(round(ty))), 14, (0, 128, 255), -1)
    cv2.circle(mask, (int(round(tx)), int(round(ty))), 14, 255, -1)
    return frame, mask, np.array([tx, ty])


def follow(world, fps=60, seconds=2.0, v=300.0, gain=0.8, ego=None, learn_for=0.0):
    """Camera tracks the target exactly. Returns the worker and final state."""
    ego = ego or EgoMotion()
    worker = D.DetectionWorker(FakeAimer())
    worker._ego = ego
    cfg = D.DetectionSettings(fov=HALF, prediction_enabled=True, prediction_factor=5)
    dt = 1 / fps
    target = np.array([1500.0, 1500.0])
    cam = target.copy()
    last = None
    for i in range(int(fps * seconds)):
        clock[0] += dt
        prev_cam = cam.copy()
        target = target + np.array([v, 0.0]) * dt
        cam = target.copy()  # perfect tracking: target stays centred
        use = TEXTURED if (i * dt < learn_for or learn_for == 0.0) else BLANK
        frame, mask, centre = render(use, cam, target)
        delta = cam - prev_cam
        counts = tuple(delta / gain)           # what our aimer "sent"
        shift, src = ego.estimate(frame, mask, counts)
        worker._camera += shift
        worker._ego_source = src
        ax, ay, lines, label = worker._apply_prediction(
            centre.copy(), centre.copy(), (int(centre[0]), int(centre[1])), REGION, cfg
        )
        last = (worker, centre, (ax, ay), src, lines)
    return last


worker, centre, aim, src, lines = follow(TEXTURED)
vx = worker._kalman.get_velocity()[0]
check("target pinned at crosshair on screen", abs(centre[0] - HALF) < 2, f"x={centre[0]:.1f}")
check("image ego-motion used", src == "img", src)
check("world velocity recovered while pinned", abs(vx - 300) < 40, f"{vx:.0f} px/s (true 300; image shift reads ~7% low)")
check("aim leads the pinned target", 8 < aim[0] - centre[0] < 20, f"{aim[0] - centre[0]:.1f}px (50ms * 300 = 15, scaled by confidence)")
check("arrow drawn although target is static on screen", lines[0][2] > 4, f"speed {lines[0][2]:.1f}")

# Stage 2: learn px-per-count on texture, then lose the texture entirely.
ego = EgoMotion()
worker, centre, aim, src, lines = follow(TEXTURED, seconds=3.0, ego=ego, learn_for=1.2)
vx = worker._kalman.get_velocity()[0]
check("gain learned from image frames", ego.gain is not None and abs(ego.gain - 0.8) < 0.1, f"{ego.gain}")
check("falls back to sent counts on a blank scene", src == "cnt", src)
check("world velocity still recovered from counts", abs(vx - 300) < 40, f"{vx:.0f} px/s")

# Stage 3: stationary camera, moving target - no ego, plain screen velocity.
worker = D.DetectionWorker(FakeAimer())
ego = EgoMotion()
cfg = D.DetectionSettings(fov=HALF, prediction_enabled=True, prediction_factor=5)
target = np.array([1400.0, 1500.0])
for i in range(90):
    clock[0] += 1 / 60
    target = target + np.array([300 / 60, 0])
    frame, mask, centre = render(TEXTURED, np.array([1500.0, 1500.0]), target)
    shift, src = ego.estimate(frame, mask, (0, 0))
    worker._camera += shift
    worker._apply_prediction(centre.copy(), centre.copy(), (0, 0), REGION, cfg)
check("static camera reports ~zero ego", np.linalg.norm(worker._camera) < 2.0, f"{worker._camera}")
check("moving target on static camera still tracked", abs(worker._kalman.get_velocity()[0] - 300) < 25,
      f"{worker._kalman.get_velocity()[0]:.0f}")

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
