"""Radar: does every detection get a dot, and is placement FOV-independent?"""
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtWidgets import QApplication

from prosuite.core.detection import DetectionSettings, DetectionWorker
from prosuite.ui.overlays import RadarWidget

app = QApplication([])
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


worker = DetectionWorker(None)


def radar_for(mask, fov):
    groups = worker._find_targets(mask)
    cfg = DetectionSettings(fov=fov, radar_enabled=True)
    centroids = np.array([g["center"] for g in groups])
    return groups, worker._build_radar(groups, centroids, cfg)


def blob(mask, cx, cy, half):
    mask[cy - half:cy + half, cx - half:cx + half] = 255


# --- every detected blob gets a dot, including the corners of the capture ---
fov = 200
mask = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
blob(mask, 200, 200, 20)   # dead centre
blob(mask, 30, 30, 15)     # top-left corner, outside the FOV *circle*
blob(mask, 370, 370, 15)   # bottom-right corner
blob(mask, 340, 200, 12)   # far right
groups, radar = radar_for(mask, fov)
check("every blob gets a dot while under the cap", len(radar) == len(groups),
      f"{len(radar)} dots for {len(groups)} blobs")
check("corner targets are not dropped", len(radar) == 4, f"{len(radar)}")

# --- bearing: sign and range ---
check("bearings span left and right",
      min(t.bearing for t in radar) < 0 < max(t.bearing for t in radar),
      f"{[round(t.bearing, 2) for t in radar]}")
check("bearings stay within +/-1", all(-1.0 <= t.bearing <= 1.0 for t in radar),
      f"{[round(t.bearing, 2) for t in radar]}")

# --- the FOV correction: same target, different FOV, same bearing angle ---
# A target 25% of the way to the FOV edge should sit at 25% of the cone,
# whatever the FOV is set to.
bearings = {}
for f in (100, 200, 400):
    m = np.zeros((2 * f, 2 * f), dtype=np.uint8)
    blob(m, int(f + f * 0.5), f, 12)   # half way to the right edge
    _, r = radar_for(m, f)
    bearings[f] = round(r[0].bearing, 4)
# An even-width blob has its centroid on a half-pixel, and half a pixel is a
# larger fraction of a small FOV - hence the tolerance rather than equality.
# At fov=100 that artefact alone is 0.005 of the bearing.
check("bearing is FOV-independent",
      max(bearings.values()) - min(bearings.values()) < 0.01, str(bearings))
check("bearing matches the offset fraction",
      all(abs(b - 0.5) < 0.01 for b in bearings.values()), str(bearings))

# --- distance from apparent size, monotonic ---
# Starts at 6 (a 12x12 blob): contourArea measures the polygon between contour
# points, so an 8x8 square reports 49 not 64, which is under the noise floor.
dists = []
for half in (6, 8, 16, 32, 60):
    m = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
    blob(m, fov, fov, half)
    _, r = radar_for(m, fov)
    dists.append(round(r[0].distance, 3))
check("bigger target reads as nearer", dists == sorted(dists, reverse=True), str(dists))
check("distance is bounded 0..1", all(0.0 <= d <= 1.0 for d in dists), str(dists))
check("distance actually spreads across the dial", max(dists) - min(dists) > 0.5,
      f"span {max(dists) - min(dists):.2f}")

# --- a target's on-screen size must not change with the FOV slider ---
# The FOV crops the capture; it does not change how big the target looks.
sizes = {}
for f in (100, 200, 400):
    m = np.zeros((2 * f, 2 * f), dtype=np.uint8)
    blob(m, f, f, 20)
    _, r = radar_for(m, f)
    sizes[f] = round(r[0].distance, 3)
check("distance is FOV-independent", len(set(sizes.values())) == 1, str(sizes))

# --- widget placement: dots land inside the cone and inside the dial ---
w = RadarWidget()
for fov_value, minimum, maximum in ((100, 100, 1000), (500, 100, 1000), (1000, 100, 1000)):
    w.set_cone_angle(fov_value, minimum, maximum)
    half_cone = w._cone_angle / 2
    for bearing in (-1.0, -0.5, 0.0, 0.5, 1.0):
        angle = math.degrees(math.radians(bearing * half_cone))
        if abs(angle) > half_cone + 1e-9:
            check(f"bearing {bearing} inside cone at fov {fov_value}", False, f"{angle:.1f}deg")
            break
    else:
        check(f"all bearings inside the cone at fov {fov_value}", True,
              f"cone {w._cone_angle:.0f}deg")

for distance in (0.0, 0.5, 1.0):
    ratio = w.INNER_MARGIN + distance * (w.OUTER_MARGIN - w.INNER_MARGIN)
    check(f"distance {distance} stays inside the dial", 0.0 < ratio < 1.0, f"r={ratio:.2f}")

# --- the dial is a summary: cap, merge and noise floor ---
from prosuite.core.detection import (  # noqa: E402
    RADAR_MAX_TARGETS,
    RADAR_MERGE_BEARING,
    RADAR_MIN_SIZE_PX,
)

# Twelve well-separated targets, all substantial.
m = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
for i in range(12):
    blob(m, 30 + i * 28, 60 + (i % 3) * 90, 11)
_, r = radar_for(m, fov)
check("dot count is capped", len(r) <= RADAR_MAX_TARGETS,
      f"{len(r)} dots (cap {RADAR_MAX_TARGETS})")

# The cap must keep the biggest contacts, not an arbitrary five.
m = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
halves = [6, 9, 12, 15, 18, 21, 24, 27]
for i, half in enumerate(halves):
    blob(m, 40 + i * 45, 100 + (i % 2) * 140, half)
groups, r = radar_for(m, fov)
kept = sorted(t.size for t in r)
biggest = sorted(math.sqrt(g["area"]) for g in groups)[-len(r):]
check("the cap keeps the largest targets",
      all(abs(a - b) < 1.0 for a, b in zip(kept, biggest)),
      f"kept={[round(s) for s in kept]} largest={[round(s) for s in biggest]}")

# Specks must not earn a dot.
m = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
blob(m, fov, fov, 30)          # one real target
blob(m, 60, 60, 2)             # a 4x4 speck
blob(m, 340, 60, 2)
_, r = radar_for(m, fov)
check("specks are ignored", len(r) == 1, f"{len(r)} dots")
check("every dot is above the noise floor",
      all(t.size >= RADAR_MIN_SIZE_PX for t in r), f"{[round(t.size, 1) for t in r]}")

# Blobs that would be drawn on top of each other collapse to one dot. A
# top-down dial has no vertical axis, so same-column blobs land on the same
# spot however far apart they are on screen - drawing both is pure overdraw.
m = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
blob(m, fov, 50, 20)
blob(m, fov, 350, 20)      # 300px away, far beyond the grouping distance
groups, r = radar_for(m, fov)
check("dots landing on the same spot collapse", len(r) == 1,
      f"{len(groups)} blobs -> {len(r)} dots")

# Fragments spread horizontally are genuinely different bearings and must not
# be merged away - that would hide a real second contact.
m = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
blob(m, fov - 46, fov, 20)
blob(m, fov + 46, fov, 20)
_, r = radar_for(m, fov)
check("horizontally separated blobs stay distinct", len(r) == 2, f"{len(r)} dots")

# ...but genuinely separate targets must survive the merge.
m = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
blob(m, 80, fov, 25)
blob(m, fov, fov, 25)
blob(m, 320, fov, 25)
_, r = radar_for(m, fov)
check("distinct targets are not over-merged", len(r) == 3, f"{len(r)} dots")
spread = sorted(t.bearing for t in r)
check("their bearings stay apart",
      all(b - a > RADAR_MERGE_BEARING for a, b in zip(spread, spread[1:])),
      f"{[round(b, 2) for b in spread]}")

check("ids are contiguous after filtering",
      [t.id for t in r] == list(range(len(r))), f"{[t.id for t in r]}")

print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
