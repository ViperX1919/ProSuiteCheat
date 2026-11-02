"""Noise rejection: walls, stripes, speckle, flicker, and target stickiness."""
import os, sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
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
    def move(self, dx, dy): pass


w = DetectionWorker(FakeAimer())
SIZE = 400  # a 2*fov square, fov 200
RED = (0, 0, 255)          # BGR, the picked colour (255,0,0)
DARK_RED = (0, 0, 150)     # same hue, much darker: a shaded wall
PALE_RED = (150, 150, 255)  # same hue, washed out


def frame():
    return np.zeros((SIZE, SIZE, 3), np.uint8)


def detect(img, **kw):
    cfg = DetectionSettings(
        detection_rgb=(255, 0, 0), tolerance=80, sat_floor=60, val_floor=60, **kw
    )
    mask = w._clean_mask(w._colour_mask(img, cfg), cfg)
    return w._find_targets(mask, cfg), cfg


def disc(img, x, y, r, colour=RED):
    cv2.circle(img, (x, y), r, colour, -1)


# --- the target itself must survive every filter ---
img = frame(); disc(img, 200, 200, 14)
groups, _ = detect(img)
check("a plain disc target is found", len(groups) == 1, str(len(groups)))

# --- shade: same hue, different brightness/saturation ---
img = frame(); cv2.rectangle(img, (0, 250), (399, 399), DARK_RED, -1)
check("dark same-hue wall is accepted with shade match off",
      len(detect(img, shade_tolerance=255)[0]) == 1)
check("dark same-hue wall is rejected by shade match",
      len(detect(img, shade_tolerance=60)[0]) == 0)
img = frame(); cv2.rectangle(img, (0, 250), (399, 399), PALE_RED, -1)
check("washed-out same-hue wall is rejected by shade match",
      len(detect(img, shade_tolerance=60)[0]) == 0)
img = frame(); disc(img, 200, 200, 14)
check("target colour still passes a tight shade window",
      len(detect(img, shade_tolerance=20)[0]) == 1)

# --- a wall in exactly the target colour: only size can tell ---
img = frame(); cv2.rectangle(img, (0, 150), (399, 399), RED, -1)
check("exact-colour wall is rejected as too big",
      len(detect(img, shade_tolerance=255, max_size=50)[0]) == 0)
check("the same wall is kept when the size limit is 100%",
      len(detect(img, shade_tolerance=255, max_size=100)[0]) == 1)
img = frame(); cv2.rectangle(img, (0, 150), (399, 399), RED, -1); disc(img, 200, 60, 14)
groups, _ = detect(img, shade_tolerance=255)
check("a target next to a wall is still found, wall ignored",
      len(groups) == 1 and abs(groups[0]["center"][1] - 60) < 3, str([g["center"] for g in groups]))

# --- thin edges and stripes ---
img = frame(); cv2.rectangle(img, (20, 300), (380, 305), RED, -1)
check("a thin stripe is not a target", len(detect(img, noise_filter=0)[0]) == 0)
img = frame(); cv2.line(img, (10, 10), (390, 60), RED, 3)
check("a hairline edge is not a target", len(detect(img)[0]) == 0)

# --- thin outline targets: turning the filters down must keep them ---
img = frame(); cv2.rectangle(img, (150, 150), (250, 250), RED, 1)
check("1px outline box is lost with default filters", len(detect(img)[0]) == 0)
check("1px outline box survives with noise and shape filters off",
      len(detect(img, noise_filter=0, shape_filter=0)[0]) == 1)
img = frame(); cv2.line(img, (20, 300), (380, 304), RED, 1)
check("a 1px line is kept when the shape filter is off",
      len(detect(img, noise_filter=0, shape_filter=0)[0]) == 1)
check("lower shape strength is more permissive",
      len(detect(img, noise_filter=0, shape_filter=3)[0]) >= len(detect(img, noise_filter=0, shape_filter=10)[0]))

# --- speckle ---
rng = np.random.default_rng(1)
img = frame()
for _ in range(250):
    x, y = rng.integers(0, SIZE, 2)
    img[y, x] = RED
check("raw speckle would be found without the filter", len(detect(img, noise_filter=0)[0]) >= 0)
check("speckle is erased by the noise filter", len(detect(img, noise_filter=1)[0]) == 0)
img2 = frame(); disc(img2, 100, 100, 10)
for _ in range(250):
    x, y = rng.integers(0, SIZE, 2)
    img2[y, x] = RED
groups, _ = detect(img2, noise_filter=1)
check("target survives while the speckle around it is erased",
      len(groups) == 1 and abs(groups[0]["center"][0] - 100) < 3, str(len(groups)))

# --- scattered pieces grouped into one mostly-empty hull ---
img = frame()
for x, y in ((50, 50), (90, 50), (50, 90), (90, 90)):
    cv2.rectangle(img, (x, y), (x + 5, y + 5), RED, -1)
check("sparse scattered blobs are not one solid target", len(detect(img)[0]) == 0)

# --- confirmation across frames ---
w._seen = []
cfg = DetectionSettings(confirm_frames=3)
g = [{"center": (100.0, 100.0), "area": 200.0, "contour": None}]
out = [len(w._confirm(g, cfg)) for _ in range(4)]
check("candidate confirms on the 3rd consecutive frame", out == [0, 0, 1, 1], str(out))
w._confirm([], cfg)
w._confirm(g, cfg)
check("a gap resets the count", len(w._confirm(g, cfg)) == 0)
w._seen = []
w._confirm(g, cfg)
jump = [{"center": (300.0, 300.0), "area": 200.0, "contour": None}]
check("a candidate that jumps far is a new one", len(w._confirm(jump, cfg)) == 0)
check("confirm_frames=1 passes straight through",
      len(w._confirm(g, DetectionSettings(confirm_frames=1))) == 1)

# --- stickiness: an equally-near rival must not steal the lock ---
sent = []
w._emit = sent.append
def square(cx, cy):
    c = np.array([[[cx-5, cy-5]], [[cx+5, cy-5]], [[cx+5, cy+5]], [[cx-5, cy+5]]], np.int32)
    return {"contour": c, "center": (float(cx), float(cy)), "area": 100.0}
cfg = DetectionSettings(fov=200, aim_enabled=True, aim_key_held=True, esp_enabled=True)
region = {"left": 0, "top": 0}
mask = np.zeros((400, 400), np.uint8)
pair = [square(155, 200), square(245, 200)]  # 45 px either side of the crosshair
cents = np.array([g["center"] for g in pair])
w._lock = np.array([245.0, 200.0])
w._emit_target(pair, cents, region, mask, (), cfg)
check("locked target keeps the lock against an equally-near rival",
      sent[-1].screen_pos == (245, 200), str(sent[-1].screen_pos))
w._lock = np.array([155.0, 200.0])
w._emit_target(pair, cents, region, mask, (), cfg)
check("and the lock follows the other target when that is the locked one",
      sent[-1].screen_pos == (155, 200), str(sent[-1].screen_pos))
w._lock = None

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
