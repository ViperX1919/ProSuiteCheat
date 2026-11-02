"""The on-screen HUD: state, target box, toggle, repositioning, persistence."""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtWidgets import QApplication

from prosuite import settings as S
from prosuite.core.detection import DetectionResult, DetectionSettings, FrameStats
from prosuite.ui.hud import IDLE, LOCKED, SCANNING, _lead_text
from prosuite.ui.main_window import MainWindow

app = QApplication(sys.argv)
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(); time.sleep(0.005)


win = MainWindow(enable_hotkeys=False)
win.worker.stop(); pump(150)
hud = win.hud
mask = np.zeros((90, 90), np.uint8); mask[30:50, 30:50] = 255

check("HUD is no longer inside the Aimbot page", not hasattr(win, "mask_preview"))
check("HUD starts hidden", not hud.isVisible())
win.features["preview"].toggle.setChecked(True); pump(50)
check("enabling the HUD shows it", hud.isVisible())
check("and tells the worker to produce the preview", win.worker.settings.preview_enabled)

# --- state ---
hud.update_from(DetectionResult(stats=FrameStats(scanning=False)), 200, (960, 540))
check("idle when nothing is scanning", hud.state == IDLE)
hud.update_from(DetectionResult(preview=mask, stats=FrameStats(scanning=True, fps=90, frame_ms=4.2)), 200, (960, 540))
check("scanning when scanning without a target", hud.state == SCANNING)
check("fps readout", "90 FPS" in hud.perf_label.text(), hud.perf_label.text())
res = DetectionResult(
    has_target=True, bbox=(120, 160, 40, 40), offset=(760, 340), screen_pos=(900, 560),
    preview=mask, prediction_label="Prediction [45ms img]",
    stats=FrameStats(scanning=True, fps=90, ego="img", lag=2, shots=3),
)
hud.update_from(res, 200, (960, 540))
check("locked when a target is found", hud.state == LOCKED)
check("offset from the crosshair", hud.readouts["Offset"].text() == "-60, +20 px", hud.readouts["Offset"].text())
check("lead readout", hud.readouts["Lead"].text() == "45ms img", hud.readouts["Lead"].text())
check("motion + shots readouts", "img" in hud.readouts["Motion"].text() and hud.readouts["Shots"].text() == "3")
check("target box is a fraction of the view", hud.view._box == (0.3, 0.4, 0.4, 0.5), str(hud.view._box))
hud.grab()
check("HUD paints locked", True)
check("lead text for no prediction", _lead_text(None) == "off" and _lead_text("Prediction") == "on")

hud.update_from(DetectionResult(stats=FrameStats(scanning=True)), 200, (960, 540))
check("box clears when the lock is lost", hud.view._box is None)

# --- results flow through the window ---
win._on_result(res); pump(20)
check("main window feeds the HUD", hud.state == LOCKED)
win.features["preview"].toggle.setChecked(False); pump(50)
check("disabling hides it", not hud.isVisible())
check("and resets it", hud.state == IDLE and hud.view._box is None)
check("worker preview off again", not win.worker.settings.preview_enabled)

# --- repositioning ---
win.features["preview"].toggle.setChecked(True); pump(50)
check("not repositioning by default", not hud.move_mode)
win._toggle_hud_move()
check("reposition button toggles move mode", hud.move_mode and win.hud_reposition_button.text() == "Stop Repositioning")
win._toggle_hud_move()
check("and back", not hud.move_mode and win.hud_reposition_button.text() == "Reposition HUD")
win._toggle_hud_move()
win.features["preview"].toggle.setChecked(False); pump(50)
check("hiding the HUD leaves move mode", not hud.move_mode)

# --- the screen capture must not see the HUD ---
win.features["preview"].toggle.setChecked(True); pump(50)
rects = win._own_rects()
g = hud.frameGeometry()
check("HUD rect is excluded from screen capture", (g.left(), g.top(), g.right(), g.bottom()) in rects)

# --- position is saved and restored ---
hud.move(333, 222)
data = S.collect(win)
check("position is saved", data["hud_pos"] == [333, 222], str(data["hud_pos"]))
hud.move(10, 10)
S.apply_safely(win, data) if hasattr(S, "apply_safely") else S.apply(win, data)
pump(50)
check("position is restored", (hud.x(), hud.y()) == (333, 222), f"{hud.x()},{hud.y()}")
data.pop("hud_pos")
check("a config without hud_pos still loads", S.apply(win, data) in (None, True))

# --- the detector resolves a target for the HUD alone ---
from prosuite.core.detection import DetectionWorker
class FakeAimer:
    def reset_carry(self): pass
    def take_sent(self): return (0, 0)
    def move(self, dx, dy): pass
w = DetectionWorker(FakeAimer())
out = []
w._emit = out.append
import cv2
img = np.zeros((400, 400, 3), np.uint8); cv2.circle(img, (200, 120), 14, (0, 0, 255), -1)
class Src:
    sees_overlays = False
    def grab(self, cx, cy, fov): return ({"left": 0, "top": 0}, img, time.perf_counter())
w._ensure_source = lambda cfg: Src()
cfg = DetectionSettings(preview_enabled=True, confirm_frames=1, fov=200)
w._process_frame(cfg)
check("preview-only still reports the locked target", out and out[-1].has_target and out[-1].bbox is not None)
cfg2 = DetectionSettings(radar_enabled=True, confirm_frames=1, fov=200)
w._process_frame(cfg2)
check("without HUD/ESP/aim no target box is produced", not out[-1].has_target)

win.close()
print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
