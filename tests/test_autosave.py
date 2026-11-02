"""Autosave writes atomically, restores faithfully and survives bad files."""
import json, os, sys, tempfile, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from prosuite import settings
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


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "autosave.json")

win = MainWindow(enable_hotkeys=False)
win.worker.stop()
saver = settings.AutoSaver(win, path=path)
saver.start()
check("nothing written while nothing changed", not saver.save_if_changed() and not os.path.exists(path))

win.tolerance_slider.setValue(133)
win.sat_floor_slider.setValue(55)
win.set_detection_color(QColor("#12AB34"))
win.features["triggerbot"].toggle.setChecked(True)
win.features["aim"].toggle.setChecked(True)
win.features["esp"].toggle.setChecked(True)
check("change triggers a write", saver.save_if_changed() and os.path.exists(path))
check("no leftover temp file", not os.path.exists(path + ".tmp"))
check("unchanged state is not rewritten", not saver.save_if_changed())

data = json.load(open(path, encoding="utf-8"))
check("file holds the new values", data["tolerance"] == 133 and data["sat_floor"] == 55 and data["detection_color"] == "#12ab34")
win.close()

win2 = MainWindow(enable_hotkeys=False)
win2.worker.stop()
check("restore reports success", settings.restore(win2, path))
check("slider restored", win2.tolerance_slider.value() == 133 and win2.sat_floor_slider.value() == 55)
check("colour restored and pushed to the worker", win2.worker.settings.detection_rgb == (0x12, 0xAB, 0x34))
check("ordinary toggle restored", win2.features["esp"].is_on())
check("triggerbot and aim stay off at launch",
      not win2.features["triggerbot"].is_on() and not win2.features["aim"].is_on())
win2.close()

# Missing and damaged files are handled quietly.
win3 = MainWindow(enable_hotkeys=False)
win3.worker.stop()
check("missing file is a no-op", settings.restore(win3, os.path.join(tmp, "nope.json")) is False)
bad = os.path.join(tmp, "bad.json")
open(bad, "w").write("{ not json")
check("damaged file is rejected", settings.restore(win3, bad) is False)
check("damaged file is set aside, not retried", not os.path.exists(bad) and os.path.exists(bad + ".bad"))
arr = os.path.join(tmp, "arr.json")
open(arr, "w").write("[1, 2]")
check("non-object file is rejected", settings.restore(win3, arr) is False)
win3.close()

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
