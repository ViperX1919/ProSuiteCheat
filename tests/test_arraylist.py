"""Headless checks for the rewritten arraylist."""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication

from prosuite.ui.arraylist import ANIMATION_MS, ArrayListWidget

app = QApplication([])
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def skip(name, why):
    print(f"SKIP  {name}  ({why})")


def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


w = ArrayListWidget()
w.show()
pump(50)

# --- ordering: widest caption at the top ---
for n, d in [("aim", "Aimbot"), ("esp", "ESP"), ("pred", "Prediction"),
             ("radar", "Radar"), ("tr", "Tracers"), ("fov", "FOV")]:
    w.add_feature(n, d)
pump(ANIMATION_MS + 150)

order = [e.label.text() for e in w._ordered()]
widths = [e.sort_width for e in w._ordered()]
check("sorted by rendered width desc", widths == sorted(widths, reverse=True), widths)
check("widest first", order[0] == "Prediction", order[0])

# The nitpick: equal character count, different pixel width. Only meaningful
# under a proportional font - the offscreen platform has no font directory and
# falls back to a monospace face where every glyph has the same advance.
from PySide6.QtGui import QFontMetrics

_fm = QFontMetrics(w._entries["fov"].label.font())
PROPORTIONAL = _fm.horizontalAdvance("W") != _fm.horizontalAdvance("i")

if PROPORTIONAL:
    fov_w, esp_w = w._entries["fov"].sort_width, w._entries["esp"].sort_width
    check("FOV measures wider than ESP", fov_w > esp_w, f"FOV={fov_w}px ESP={esp_w}px")
    check("FOV sits above ESP", order.index("FOV") < order.index("ESP"), order)
else:
    skip("FOV wider than ESP", "monospace fallback font, no proportional metrics")

ys = [e.label.y() for e in w._ordered()]
check("stacked top to bottom, no overlap", ys == sorted(ys) and len(set(ys)) == len(ys), ys)

# --- no duplicates on rapid re-toggle ---
w.remove_feature("esp")
w.add_feature("esp", "ESP")
w.remove_feature("esp")
w.add_feature("esp", "ESP")
pump(ANIMATION_MS + 200)
esp_labels = [e for e in w._entries.values() if e.label.text() == "ESP"]
check("rapid re-toggle leaves one entry", len(esp_labels) == 1, f"{len(esp_labels)} entries")
check("no orphan child labels", len(w.findChildren(type(esp_labels[0].label))) == len(w._entries),
      f"{len(w.findChildren(type(esp_labels[0].label)))} labels vs {len(w._entries)} entries")

# --- removal actually deletes ---
before = len(w._entries)
w.remove_feature("tr")
pump(ANIMATION_MS + 250)
check("removed entry is destroyed", len(w._entries) == before - 1, f"{len(w._entries)}")
check("removed name gone", "tr" not in w._entries)

# --- animation really runs ---
w.add_feature("glow", "Glow")
app.processEvents()
mid = w._entries["glow"].label.pos()
running = w._entries["glow"].is_animating()
pump(ANIMATION_MS + 150)
end = w._entries["glow"].label.pos()
check("entry animates rather than snapping", running and mid != end, f"{mid} -> {end}")
check("entry ends fully opaque", abs(w._entries["glow"].effect.opacity() - 1.0) < 0.01)

# --- right anchor: labels flush to the right edge ---
w._anchor_right = True
w._relayout()
pump(ANIMATION_MS + 150)
rights = {e.label.x() + e.label.width() for e in w._ordered()}
check("right-anchored entries share a right edge", len(rights) == 1, rights)

# --- caption updates must not reshuffle ---
order_before = [e.name for e in w._ordered()]
w.update_feature("pred", "Prediction [0.42]")
pump(ANIMATION_MS + 150)
check("live caption change keeps order", [e.name for e in w._ordered()] == order_before)
check("caption applied", w._entries["pred"].label.text() == "Prediction [0.42]")

# --- update_feature on a repeat value is a no-op (per-frame churn guard) ---
pos_before = w._entries["pred"].label.pos()
w.update_feature("pred", "Prediction [0.42]")
check("repeat caption does not restart animation", not w._entries["pred"].is_animating())
check("repeat caption does not move", w._entries["pred"].label.pos() == pos_before)

# --- move mode ---
seen = []
w.move_mode_changed.connect(seen.append)
w.toggle_move_mode()
pump(80)
from PySide6.QtCore import Qt
check("move mode drops click-through", not w.windowFlags() & Qt.WindowTransparentForInput)
check("move mode signalled", seen == [True], seen)
w.toggle_move_mode()
pump(80)
check("click-through restored", bool(w.windowFlags() & Qt.WindowTransparentForInput))
check("exit signalled", seen == [True, False], seen)

# --- sizing tracks the widest entry ---
w.add_feature("long", "Extremely Long Module Name")
pump(ANIMATION_MS + 150)
widest = max(e.label.width() for e in w._ordered())
check("widget widened to fit", w.width() >= widest, f"w={w.width()} widest={widest}")
check("widest is top", w._ordered()[0].name == "long", w._ordered()[0].name)

# --- widths must be re-measured when the font changes ---
small = w._entries["fov"].sort_width
w.set_font_size(28)
pump(ANIMATION_MS + 150)
big = w._entries["fov"].sort_width
check("sort width tracks font size", big > small, f"{small}px -> {big}px")
widths = [e.sort_width for e in w._ordered()]
check("order still valid after resize", widths == sorted(widths, reverse=True), widths)
if PROPORTIONAL:
    names = [e.name for e in w._ordered()]
    check("FOV still above ESP at 28pt", names.index("fov") < names.index("esp"), names)
else:
    skip("FOV above ESP at 28pt", "monospace fallback font")

print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
