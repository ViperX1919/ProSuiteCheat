"""Named profiles: storage, safety of names, and loading never arms aim/trigger."""
import os, sys, tempfile, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from prosuite import settings as S
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


# --- name handling: nothing may leave the profiles folder ---
d = tempfile.mkdtemp()
for bad in ("../../etc/passwd", "..\\..\\windows\\system32", "C:\\evil", "a/b", "..", ".", "   ", "", "con:"):
    try:
        path = S.profile_path(bad, d)
        inside = os.path.dirname(os.path.abspath(path)) == os.path.abspath(d)
        check(f"unsafe name {bad!r} stays inside the folder", inside, os.path.basename(path))
    except ValueError:
        check(f"unsafe name {bad!r} is rejected", True)
check("clean name keeps ordinary characters", S.clean_profile_name("3D Aim Trainer-v2_final") == "3D Aim Trainer-v2_final")
check("clean name strips separators", "/" not in S.clean_profile_name("a/b\\c") and "\\" not in S.clean_profile_name("a/b\\c"))
for device in ("con", "NUL", "com1", "LPT9.txt", "Aux "):
    stem = S.clean_profile_name(device)
    check(f"device name {device!r} is not left as a device", stem.split(".")[0].strip().upper() not in S._RESERVED, stem)
check("clean name is length-limited", len(S.clean_profile_name("x" * 200)) <= 40)

# --- save / list / load / delete ---
win = MainWindow(enable_hotkeys=False)
win.worker.stop(); pump(100)
check("no profiles to begin with", S.list_profiles(d) == [])

win.tolerance_slider.setValue(121)
win.set_detection_color(QColor("#FF8800"))
win.features["radar"].toggle.setChecked(True)
win.features["aim"].toggle.setChecked(True)
win.features["triggerbot"].toggle.setChecked(True)
stored = S.save_profile(win, "Trainer A", d)
check("save returns the stored name", stored == "Trainer A", stored)
win.tolerance_slider.setValue(200)
S.save_profile(win, "second", d)
check("profiles are listed, sorted", S.list_profiles(d) == ["second", "Trainer A"], str(S.list_profiles(d)))
check("no temp files left behind", not [f for f in os.listdir(d) if f.endswith(".tmp")])

win.tolerance_slider.setValue(10)
win.set_detection_color(QColor("#0000FF"))
win.features["radar"].toggle.setChecked(False)
check("load succeeds", S.load_profile(win, "Trainer A", d))
check("settings restored from the profile", win.tolerance_slider.value() == 121 and win.detection_color.name().lower() == "#ff8800")
check("ordinary toggle restored", win.features["radar"].is_on())
check("loading never arms aim or triggerbot", not win.features["aim"].is_on() and not win.features["triggerbot"].is_on())
check("worker agrees", not win.worker.settings.aim_enabled and not win.worker.settings.trigger_enabled)

check("missing profile fails cleanly", S.load_profile(win, "nope", d) is False)
bad = os.path.join(d, "broken.json")
open(bad, "w").write("not json")
check("damaged profile fails cleanly", S.load_profile(win, "broken", d) is False)
open(os.path.join(d, "list.json"), "w").write("[1]")
check("non-object profile fails cleanly", S.load_profile(win, "list", d) is False)

check("delete removes it", S.delete_profile("second", d) and "second" not in S.list_profiles(d))
check("deleting a missing profile is harmless", S.delete_profile("second", d) is False)
check("deleting with an unusable name is harmless", S.delete_profile("..", d) is False)

# --- UI: dropdown, selecting loads ---
S.profiles_dir = lambda: d
win.refresh_profiles()
names = [win.profile_dropdown.itemText(i) for i in range(win.profile_dropdown.count())]
check("dropdown lists profiles", "Trainer A" in names, str(names))
win.tolerance_slider.setValue(33)
win._profile_chosen(names.index("Trainer A"))
pump(50)
check("choosing a profile loads it", win.tolerance_slider.value() == 121)
win.profile_dropdown.setCurrentText("Trainer A")
win._delete_profile()
check("delete button removes the selected profile", "Trainer A" not in S.list_profiles(d))
for leftover in S.list_profiles(d):
    S.delete_profile(leftover, d)
win.refresh_profiles()
check("dropdown shows a placeholder when empty", win.profile_dropdown.count() == 1 and win.profile_dropdown.itemText(0) == "(none)")

win.close()
print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
