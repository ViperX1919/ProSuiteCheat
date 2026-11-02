"""Status strip, mask preview, eyedropper and accent swatches."""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from prosuite.core.detection import DetectionResult, FrameStats
from prosuite.ui import status as S
from prosuite.ui.accent import PRESETS
from prosuite.ui.main_window import MainWindow
from prosuite.ui.picker import ScreenPicker

app = QApplication(sys.argv)
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


win = MainWindow(enable_hotkeys=False)
win.show()
pump(150)
win.worker.stop()   # the live worker would overwrite the data fed in below
pump(200)           # ...and flush results it had already queued

# --- status strip ---
check("status strip sits under the pages", win.status.isVisible() and win.status.height() == S.StatusStrip.HEIGHT)
check("health: idle is grey", S.health_colour(144, False) == S.IDLE)
check("health: fast is green", S.health_colour(140, True) == S.GOOD)
check("health: slow is amber", S.health_colour(60, True) == S.WARN)
check("health: crawling is red", S.health_colour(10, True) == S.BAD)

win.status.set_stats(FrameStats(fps=120, frame_ms=4.0, ego="img", lag=2, source="Some Window", scanning=True), True)
pump(900)
check("fps readout glides to the value", "1" in win.status.fps_label.text() and 110 <= float(win.status.fps_label.text().split()[0]) <= 121,
      win.status.fps_label.text())
check("shows capture source", "Some Window" in win.status.source_label.text())
check("shows ego mode and lag", "scene" in win.status.ego_label.text() and "lag 2f" in win.status.ego_label.text(),
      win.status.ego_label.text())
win.status.set_stats(FrameStats(fps=120, frame_ms=4.0, scanning=True), False)
pump(200)
check("ego hidden when prediction is off", win.status.ego_label.text() == "")
win.status.set_stats(FrameStats(fps=140, frame_ms=1.0, scanning=False), False)
pump(200)
check("idle state labelled", win.status.state_label.text() == "IDLE")

# --- mask preview ---
prev = win.mask_preview
mask = np.zeros((90, 90), np.uint8); mask[30:50, 30:50] = 255
prev.set_mask(mask)
pump(400)
check("preview lights up when pixels match", prev.lit > 0.9, f"{prev.lit:.2f}")
prev.set_mask(np.zeros((90, 90), np.uint8))
pump(400)
check("preview dims when nothing matches", prev.lit < 0.1, f"{prev.lit:.2f}")
prev.set_mask(None); prev.grab()  # paints the empty state without raising
check("preview paints the empty state", True)
prev.set_mask(mask); prev.grab()
check("preview paints a mask", True)

# preview toggle drives the worker setting and is saved as a feature toggle
win.features["preview"].toggle.setChecked(True)
pump(50)
check("preview toggle reaches the worker", win.worker.settings.preview_enabled)
win.features["preview"].toggle.setChecked(False)
pump(50)
check("preview toggle off reaches the worker", not win.worker.settings.preview_enabled)

win._on_result(DetectionResult(preview=mask, stats=FrameStats(fps=90, scanning=True)))  # must not raise
check("window accepts results with stats and preview", True)

# --- eyedropper ---
picker = ScreenPicker()
pix = QPixmap(100, 100)
pix.fill(QColor("#336699"))
picker.set_screenshot(pix)
got = picker.colour_at(QPoint(10, 10))
check("picker samples the screenshot", got is not None and got.name() == "#336699", got.name() if got else None)
check("picker ignores points off the screen", picker.colour_at(QPoint(500, 500)) is None)
seen = []
picker.picked.connect(seen.append)
picker._cursor = QPoint(20, 20)
picker._finish(picker.colour_at(QPoint(20, 20)))
check("picker emits the sampled colour", len(seen) == 1 and seen[0].name() == "#336699")
picker2 = ScreenPicker(); picker2.set_screenshot(pix)
cancelled = []
picker2.cancelled.connect(lambda: cancelled.append(1))
picker2._finish(None)
check("picker cancel emits cancelled", cancelled == [1])
check("eyedropper button exists next to the colour", win.eyedropper_button.text() == "Pick")
win.set_detection_color(QColor("#12AB34"))
check("sampled colour reaches the worker", win.worker.settings.detection_rgb == (0x12, 0xAB, 0x34))

# --- accent swatches ---
name, hexcolour = PRESETS[2]
win.accent_swatches.chosen.emit(QColor(hexcolour))
pump(50)
check("accent swatch changes the theme colour", win.theme_color.name().lower() == hexcolour.lower(), win.theme_color.name())
check("selected swatch is ringed", sum(s.selected for s in win.accent_swatches._swatches) == 1)

win.close()
print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
