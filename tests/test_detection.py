"""Colour filter, frame stats and the mask preview."""
import os, sys
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
    def reset_carry(self): pass
    def take_sent(self): return (0, 0)


w = DetectionWorker(FakeAimer())


def hit(bgr_colour, **kw):
    # Shade match off: these cases are about the floors alone (test_noise.py
    # covers the shade window, which otherwise overrides a lowered floor).
    kw.setdefault("shade_tolerance", 255)
    cfg = DetectionSettings(detection_rgb=(255, 0, 0), tolerance=80, **kw)
    frame = np.full((20, 20, 3), bgr_colour, np.uint8)
    return bool(w._colour_mask(frame, cfg).any())


# A dark red (value ~70) and a washed-out red (saturation ~60).
dark, washed = (0, 0, 70), (200, 200, 255)
check("default floors reject dark red", not hit(dark))
check("lower brightness floor accepts dark red", hit(dark, val_floor=40))
check("default floors reject washed-out red", not hit(washed))
check("lower saturation floor accepts washed-out red", hit(washed, sat_floor=40))
check("pure red always accepted", hit((0, 0, 255)))
check("floor change invalidates the cache", hit(dark, val_floor=40) and not hit(dark, val_floor=100))

# Preview is opt-in and small.
big = np.zeros((600, 600), np.uint8); big[100:200, 100:200] = 255
check("no preview unless asked", w._make_preview(big, DetectionSettings()) is None)
small = w._make_preview(big, DetectionSettings(preview_enabled=True))
check("preview is downscaled", small is not None and max(small.shape) <= 180, f"{small.shape}")
check("preview keeps the blob", small is not None and small.any())
check("preview forces scanning", DetectionSettings(preview_enabled=True).should_scan)

# Stats smooth towards the true rate.
w2 = DetectionWorker(FakeAimer())
t = 100.0
for _ in range(200):
    w2._record_timing(t, 0.004)
    t += 1 / 100
check("fps estimate converges", 95 < w2._fps < 105, f"{w2._fps:.1f}")
check("frame time converges", 3.5 < w2._frame_ms < 4.5, f"{w2._frame_ms:.2f}ms")

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
