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

# --- toggle polish ---
tg = win.features["esp"].toggle
tg.setChecked(False); pump(300)
check("toggle progress is 0 when off", tg.progress == 0.0)
tg.setChecked(True); pump(90)
mid = tg.progress
pump(300)
check("track colour follows the knob through the slide", 0.0 < mid < 1.0 and tg.progress == 1.0, f"mid={mid:.2f}")
from PySide6.QtCore import QEvent
tg.enterEvent(QEvent(QEvent.Enter)); pump(300)
check("hover halo eases in", tg._hover > 0.95, f"{tg._hover:.2f}")
tg.leaveEvent(QEvent(QEvent.Leave)); pump(300)
check("hover halo eases out", tg._hover < 0.05, f"{tg._hover:.2f}")
tg.grab()
check("toggle paints mid-hover without error", True)
check("window fade-in finishes fully opaque", abs(win.windowOpacity() - 1.0) < 0.01, f"{win.windowOpacity():.2f}")
tg.setChecked(False)

# --- crosshair ---
from prosuite import constants as _C
ch = win.crosshair_overlay
for style in _C.CROSSHAIR_STYLES:
    win.crosshair_style_dropdown.setCurrentText(style)
    ch.grab()
check("every crosshair style paints", ch.style == _C.CROSSHAIR_STYLES[-1])
win.crosshair_size_slider.setValue(22); win.crosshair_thickness_slider.setValue(3); win.crosshair_gap_slider.setValue(7)
check("sliders drive the overlay", (ch.length, ch.thickness, ch.gap) == (22, 3, 7), f"{(ch.length, ch.thickness, ch.gap)}")
win.features["crosshair"].toggle.setChecked(True); pump(80)
check("toggle shows the crosshair", ch.isVisible())
c = win.screen_center
check("crosshair is centred on the screen", abs(ch.geometry().center().x() - c.x()) <= 1 and abs(ch.geometry().center().y() - c.y()) <= 1,
      f"{ch.geometry().center().x(), ch.geometry().center().y()} vs {c.x(), c.y()}")
check("crosshair is small, not fullscreen", ch.width() <= 200 and ch.height() <= 200)
win.features["crosshair"].toggle.setChecked(False); pump(80)
check("toggle hides the crosshair", not ch.isVisible())
from prosuite import settings as _S
win.set_crosshair_color(QColor("#00FF88"))
saved = _S.collect(win)
check("crosshair settings are saved", saved["crosshair_color"] == "#00ff88" and saved["crosshair_size"] == 22 and saved["crosshair_style"] == _C.CROSSHAIR_STYLES[-1])
win.set_crosshair_color(QColor("#FFFFFF")); win.crosshair_size_slider.setValue(5)
_S.apply(win, saved)
check("crosshair settings round-trip", ch.color.name() == "#00ff88" and ch.length == 22)
white = QColor("#FFFFFF")
import cv2, numpy as _np
from prosuite.core.detection import DetectionSettings, DetectionWorker
class _A:
    def reset_carry(self): pass
    def take_sent(self): return (0, 0)
frame = _np.full((20, 20, 3), 255, _np.uint8)
check("white crosshair is invisible to the colour filter",
      not DetectionWorker(_A())._colour_mask(frame, DetectionSettings(detection_rgb=(255, 0, 0))).any())

# --- toasts ---
t = win.toast
shown = []
real_notify = t.notify
t.notify = lambda name, on: (shown.append((name, on)), real_notify(name, on))
win.features["aim"].toggle.setChecked(True); pump(40)
check("switching a module on shows a toast", shown and shown[-1] == ("Smooth Aim", True), f"{shown}")
win.features["aim"].toggle.setChecked(False); pump(40)
check("switching it off shows a toast", shown[-1] == ("Smooth Aim", False))
check("toast becomes visible", t.isVisible() or t.visible_amount > 0)
pump(2200)
check("toast hides itself afterwards", not t.isVisible() and t.visible_amount <= 0.01, f"{t.visible_amount:.2f}")
n = len(shown)
win.features["notifications"].toggle.setChecked(False)
win.features["aim"].toggle.setChecked(True); pump(40)
check("notifications can be turned off", len(shown) == n)
win.features["notifications"].toggle.setChecked(True)
win.features["aim"].toggle.setChecked(False); pump(40)
n = len(shown)
_S.apply(win, {"toggles": {"aim": True, "esp": True, "radar": True, "crosshair": True}})
pump(40)
check("loading a config does not spam toasts", len(shown) == n, f"{len(shown) - n} extra")
check("...but the toggles did change", win.features["aim"].is_on() and win.features["radar"].is_on())
for name in ("aim", "esp", "radar", "crosshair"):
    win.features[name].toggle.setChecked(False)
pump(40)
win.toast.hide()
t.notify = real_notify

win.close()
print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
