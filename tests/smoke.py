"""Offscreen smoke test for the refactored ProSuite package."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np
from PySide6.QtWidgets import QApplication

from prosuite import logging_setup, settings
from prosuite.constants import ESP_MODES, AIM_POSITIONS
from prosuite.core.detection import DetectionSettings, DetectionWorker
from prosuite.core.aimer import SmoothAimer
from prosuite.core.kalman import KalmanFilter
from prosuite.ui.main_window import MainWindow

failures = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


logging_setup.configure()
app = QApplication(sys.argv)
win = MainWindow()

# --- 1. the saved schema matches the original save_settings() exactly ---
ORIGINAL_KEYS = {
    "fov", "detection_color", "prediction_color", "tolerance", "smoothness",
    "speed", "priority", "aim_pos", "y_offset", "prediction_factor",
    "esp_color", "opacity", "esp_mode", "arraylist_enabled", "arraylist_color",
    "arraylist_size", "arraylist_pos", "arraylist_style", "radar_pos",
    "radar_color", "ui_theme_color", "ui_theme", "window_pos", "window_size",
    "toggles", "keybinds", "sections_collapsed",
}
# Keys added deliberately since the original. Old configs must still load
# (the loader falls back to a default), so these may only ever be additions.
ADDED_KEYS = {
    "trigger_delay", "trigger_reaction", "capture_window", "sat_floor", "val_floor",
    "shade_tolerance", "noise_filter", "max_size", "confirm_frames",
    "crosshair_color", "crosshair_style", "crosshair_size", "crosshair_thickness", "crosshair_gap",
}
data = settings.collect(win)
check("no original key was dropped", not ORIGINAL_KEYS - set(data),
      f"missing={ORIGINAL_KEYS - set(data)}")
check("only intended keys were added", set(data) - ORIGINAL_KEYS == ADDED_KEYS,
      f"unexpected={set(data) - ORIGINAL_KEYS - ADDED_KEYS}")
check("config with no trigger_delay defaults to 0",
      (settings.apply(win, {k: v for k, v in data.items() if k != "trigger_delay"})
       or win.trigger_delay_slider.value()) == 0,
      f"got {win.trigger_delay_slider.value()}")
check("toggles cover every feature switch",
      set(data["toggles"]) == {n for n, f in win.features.items() if f.toggle},
      str(sorted(data["toggles"])))

# --- 2. round-trip through the on-disk schema ---
blob = json.dumps(data)
settings.apply(win, json.loads(blob))
again = settings.collect(win)
diff = {k for k in data if data[k] != again[k]}
check("config round-trips unchanged", not diff, f"differs: {diff or 'none'}")

# --- 3. the user's existing config still loads ---
with open(REPO / "config" / "current_config.json") as f:
    legacy = json.load(f)
settings.apply(win, legacy)
check("legacy current_config.json loads", win.tolerance_slider.value() == 80,
      f"tolerance={win.tolerance_slider.value()}")
check("legacy arraylist int colour decoded",
      win.arraylist_color.name() == "#881122", win.arraylist_color.name())

with open(REPO / "config" / "FPS.json") as f:
    settings.apply(win, json.load(f))
check("legacy FPS.json loads", True)

# --- 4. every feature resolves, and the aim_toggle hotkey now works ---
check("aim_toggle targets the aim switch",
      win.features["aim_toggle"].controls == "aim")
before = win.features["aim"].toggle.isChecked()
win._on_hotkey("aim_toggle", True)
check("aim_toggle hotkey flips the aimbot",
      win.features["aim"].toggle.isChecked() != before)

win._on_hotkey("aim", True)
check("hold key reaches the worker", win.worker.settings.aim_key_held)
win._on_hotkey("aim", False)
check("hold key release reaches the worker", not win.worker.settings.aim_key_held)

# --- 5. theming touches every registered control without error ---
for theme in ("Light", "Dark"):
    win.apply_theme(theme)
check("both themes apply", True, f"{len(win.controls)} controls restyled")

# --- 6. all dropdown options are accepted ---
for mode in ESP_MODES:
    win.esp_mode_dropdown.setCurrentText(mode)
    check(f"esp mode {mode!r}", win.render_overlay.esp_mode == mode)
for pos in AIM_POSITIONS:
    win.aim_position_dropdown.setCurrentText(pos)
check("Y-offset row shows only for Custom", win.y_offset_widget.isVisibleTo(win))

# --- 7. detection pipeline against a synthetic frame ---
worker = DetectionWorker(SmoothAimer())
fov = 100
mask = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
mask[90:130, 80:120] = 255          # one blob near centre
mask[95:125, 130:170] = 255         # a second, within grouping distance
mask[10:30, 10:30] = 255            # a distant third
groups = worker._find_targets(mask)
check("nearby blobs merge, distant one does not", len(groups) == 2,
      f"{len(groups)} groups")

# Two differently-sized targets, both inside the FOV circle.
radar_mask = np.zeros((2 * fov, 2 * fov), dtype=np.uint8)
radar_mask[90:110, 40:60] = 255      # small, left of centre
radar_mask[80:130, 140:190] = 255    # large, right of centre
radar_groups = worker._find_targets(radar_mask)
cfg = DetectionSettings(fov=fov, radar_enabled=True)
radar = worker._build_radar(
    radar_groups, np.array([g["center"] for g in radar_groups]), cfg
)
sizes = [round(t.size, 1) for t in radar]
check("radar reports two targets", len(radar) == 2, f"{len(radar)}")
check("radar sizes are populated and distinct",
      len(radar) == 2 and all(s > 0 for s in sizes) and sizes[0] != sizes[1],
      f"sizes={sizes}")
check("radar bearings straddle the centre",
      len(radar) == 2 and min(t.bearing for t in radar) < 0 < max(t.bearing for t in radar),
      f"bearings={[round(t.bearing, 2) for t in radar]}")
# The bigger blob reads as the nearer contact, so it sits closer to the centre.
small, large = sorted(radar, key=lambda t: t.size)
check("bigger target maps nearer the centre", large.distance < small.distance,
      f"large={large.distance:.2f} small={small.distance:.2f}")
check("distance stays within the dial", all(0.0 <= t.distance <= 1.0 for t in radar),
      f"{[round(t.distance, 2) for t in radar]}")

# --- 8. Pixel ESP writes into the QImage buffer without raising ---
win.esp_mode_dropdown.setCurrentText("Pixel")
win.render_overlay.show_target(None, (0, 0), (10, 10), mask)
img_ok = True
try:
    from PySide6.QtGui import QPixmap, QPainter
    pm = QPixmap(400, 400)
    pm.fill()
    p = QPainter(pm)
    win.render_overlay._mask = mask
    win.render_overlay._draw_pixel_mask(p)
    p.end()
except Exception as e:
    img_ok = False
    print("   pixel error:", e)
check("Pixel ESP mask blits", img_ok)

# --- 9. Kalman filter behaves ---
kf = KalmanFilter()
for i in range(10):
    kf.update(np.array([float(i * 3), 0.0]))
vel = kf.get_velocity()
check("kalman tracks constant velocity", vel[0] > 1.0, f"vx={vel[0]:.2f}")
check("kalman confidence in range", 0.0 < kf.get_prediction_confidence() <= 1.0)
kf.reset()
check("kalman resets", not kf.initialized)

# --- 10. shutdown is clean ---
win.worker.start()
app.processEvents()
win.close()
check("worker stops on close", not win.worker.isRunning())

print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
