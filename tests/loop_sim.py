"""Closed-loop simulation: aim -> camera -> frames -> ego/tracker -> aim.

Not a pass/fail test; prints how well the aim holds a manoeuvring target, so
changes to prediction can be compared (python tests/loop_sim.py).
"""
import os, sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from prosuite.core import detection as D
from prosuite.core.egomotion import EgoMotion

SIZE, HALF = 400, 200
clock = [1000.0]
D.time.perf_counter = lambda: clock[0]

rng = np.random.default_rng(3)
noise = rng.integers(0, 255, (4000, 4000), dtype=np.uint8)
WORLD = cv2.cvtColor(cv2.GaussianBlur(noise, (0, 0), 6), cv2.COLOR_GRAY2BGR)


class FakeAimer:
    smoothness, speed = 1, 10

    def __init__(self):
        self.sent = [0, 0]

    def reset_carry(self):
        pass

    def take_sent(self):
        s, self.sent = tuple(self.sent), [0, 0]
        return s


def run(prediction, compensate=True, fps=60, seconds=8.0, smooth=6.0, latency=2, gain=0.9,
        jitter=0.7, speed=320.0, overlays=None):
    """overlays: None (window-style frames), 'unmasked' or 'masked' (screen-style)."""
    aimer = FakeAimer()
    worker = D.DetectionWorker(aimer)
    cfg = D.DetectionSettings(
        fov=HALF, prediction_enabled=prediction, prediction_factor=8,
        screen_center=(HALF, HALF), esp_enabled=bool(overlays), fov_ring=bool(overlays))
    worker_box = None
    dt = 1 / fps
    cam = np.array([2000.0, 2000.0])
    pending = [np.zeros(2)] * latency
    errs, aim_steps = [], []
    last_aim = None
    prev_rng = np.random.default_rng(5)
    for i in range(int(fps * seconds)):
        clock[0] += dt
        t = i * dt
        # Target weaves: speed and direction change every ~1.2 s.
        heading = 0.9 * np.sin(2 * np.pi * t / 2.4) + 0.4 * np.sin(2 * np.pi * t / 1.1)
        target = np.array([2000.0, 2000.0]) + np.array([
            speed * 0.6 * np.sin(2 * np.pi * t / 2.4) * 1.2,
            speed * 0.3 * np.sin(2 * np.pi * t / 1.7)]) * 1.3 + np.array([speed * t * 0.15, 0])

        # camera moves by counts sent `latency` frames ago
        moved = pending.pop(0)
        cam = cam + moved
        x, y = int(round(cam[0])), int(round(cam[1]))
        frame = WORLD[y - HALF : y + HALF, x - HALF : x + HALF].copy()
        centre_img = target - (cam - np.array([x, y])) - np.array([x, y]) + HALF
        centre_img = target - np.array([x, y]) + HALF + (np.array([x, y]) - cam) * 0
        centre_img = centre_img + prev_rng.normal(0, jitter, 2)
        mask = np.zeros(frame.shape[:2], np.uint8)
        px, py = int(round(centre_img[0])), int(round(centre_img[1]))
        if 0 <= px < SIZE and 0 <= py < SIZE:
            cv2.circle(frame, (px, py), 14, (0, 128, 255), -1)
            cv2.circle(mask, (px, py), 14, 255, -1)
        else:
            pending.append(np.zeros(2)); errs.append(HALF * 1.5); continue

        ignore = None
        if overlays:
            # Draw what the real overlays draw: the FOV ring and last frame's box.
            cv2.circle(frame, (HALF, HALF), HALF, (60, 20, 200), 2)
            if worker_box is not None:
                l, t, r, b = worker_box
                cv2.rectangle(frame, (l, t), (r, b), (60, 20, 200), 2)
            worker._last_box = worker_box
            if overlays == "masked":
                ignore = worker._ignore_mask({"left": 0, "top": 0}, frame.shape[:2], cfg)
            worker_box = (px - 16, py - 16, px + 16, py + 16)

        sent = aimer.take_sent()
        ax, ay = centre_img
        if prediction or compensate:
            blanked = mask if ignore is None else cv2.bitwise_or(mask, ignore)
            shift, worker._ego_source = worker._ego.estimate(frame, blanked, sent)
            worker._camera += shift
        if prediction:
            ax, ay, _, _ = worker._apply_prediction(
                centre_img.copy(), centre_img.copy(), (0, 0), {"left": 0, "top": 0}, cfg)
        aim = np.array([ax, ay])
        if last_aim is not None:
            aim_steps.append(np.linalg.norm(aim - last_aim))
        last_aim = aim

        off = aim - HALF
        if compensate:
            off = off + worker._ego.pending_shift()
        counts = np.round(off / smooth)
        aimer.sent = [aimer.sent[0] + counts[0], aimer.sent[1] + counts[1]]
        pending.append(counts * gain)
        errs.append(np.linalg.norm(centre_img - HALF))

    e = np.array(errs[60:])
    s = np.array(aim_steps[60:])
    if not len(s):
        s = np.array([999.0])
    return e.mean(), np.percentile(e, 95), s.mean(), np.percentile(s, 99)


if __name__ == "__main__":
    cases = (
        ("plain aim", dict(prediction=False, compensate=False)),
        ("+ in-flight comp", dict(prediction=False, compensate=True)),
        ("+ prediction", dict(prediction=True, compensate=False)),
        ("prediction + comp", dict(prediction=True, compensate=True)),
        ("  (screen, masked)", dict(prediction=True, compensate=True, overlays="masked")),
    )
    for smooth, latency in ((6.0, 2), (3.0, 2), (2.0, 4), (1.5, 5)):
        for name, kw in cases:
            mean, p95, step, step99 = run(smooth=smooth, latency=latency, seconds=10, **kw)
            print(f"smooth {smooth:3.1f} lag {latency} {name:19s} error mean {mean:6.1f}px  p95 {p95:6.1f}px | "
                  f"aim jump/frame mean {step:5.1f}px  p99 {step99:6.1f}px")
        print()
