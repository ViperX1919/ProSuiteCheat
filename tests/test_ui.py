"""Page transitions and section expand/collapse.

These animate, so the risk is not "does it look nice" but "does it always end
in a consistent state" - the wrong page left visible, or a section stuck at
zero height, would be a broken UI rather than a cosmetic flaw.
"""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PySide6.QtWidgets import QApplication, QLabel

from prosuite.ui.main_window import MainWindow
from prosuite.ui.sections import _UNBOUNDED, CollapsibleSection
from prosuite.ui.widgets import AnimatedStackedWidget, make_toggle

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
pump(120)
stack = win.stack
D = AnimatedStackedWidget.DURATION


def visible_pages():
    return [i for i in range(stack.count()) if stack.widget(i).isVisible()]


check("stack has the three pages", stack.count() == 3, f"{stack.count()}")

# --- a transition ends with exactly one visible page, parked at the origin ---
for target in (1, 2, 0, 2):
    stack.setCurrentIndex(target)
    pump(D + 150)
    vis = visible_pages()
    ok = stack.currentIndex() == target and vis == [target]
    check(f"switch to page {target} settles cleanly", ok,
          f"current={stack.currentIndex()} visible={vis}")
    check(f"page {target} parked at origin",
          stack.widget(target).pos().x() == 0, f"x={stack.widget(target).pos().x()}")

# --- direction of travel ---
stack.setCurrentIndex(0)
pump(D + 150)
stack.setCurrentIndex(2)
app.processEvents()
check("moving down the nav slides in from the right",
      stack.widget(2).pos().x() > 0, f"x={stack.widget(2).pos().x()}")
pump(D + 150)
stack.setCurrentIndex(0)
app.processEvents()
check("moving up the nav slides in from the left",
      stack.widget(0).pos().x() < 0, f"x={stack.widget(0).pos().x()}")
pump(D + 150)

# --- both pages move, so there is no gap ---
stack.setCurrentIndex(1)
pump(60)
moving = [i for i in range(stack.count()) if stack.widget(i).isVisible()]
check("both pages are visible mid-transition", len(moving) == 2, f"{moving}")
pump(D + 150)

# --- a click landing mid-transition is honoured, not swallowed ---
stack.setCurrentIndex(0)
pump(D + 150)
stack.setCurrentIndex(1)
pump(40)                       # interrupt deliberately
stack.setCurrentIndex(2)
pump(D + 200)
check("interrupting a transition still lands on the last page",
      stack.currentIndex() == 2, f"current={stack.currentIndex()}")
check("interruption leaves one visible page", visible_pages() == [2],
      f"{visible_pages()}")

# --- rapid fire ---
for i in (0, 1, 2, 0, 2, 1, 0):
    stack.setCurrentIndex(i)
    pump(25)
pump(D + 250)
check("rapid switching settles on one page", visible_pages() == [stack.currentIndex()],
      f"visible={visible_pages()} current={stack.currentIndex()}")

# --- sections ---
section = CollapsibleSection(make_toggle("Enable Test", win.theme_color).widget)
for i in range(4):
    section.add_widget(QLabel(f"row {i}"))
section.show()
pump(60)

check("section starts collapsed", section.is_collapsed())

section.set_collapsed(False)
pump(40)
mid = section.content_widget.maximumHeight()
pump(300)
check("expanding animates rather than snapping",
      0 < mid < section.content_widget.maximumHeight(), f"mid height {mid}")
check("expanded content is visible", section.content_widget.isVisible())
check("expanded height is uncapped",
      section.content_widget.maximumHeight() == _UNBOUNDED,
      f"{section.content_widget.maximumHeight()}")
check("arrow points down when open", section.arrow.rotation == 90,
      f"{section.arrow.rotation}")

section.set_collapsed(True)
pump(300)
check("collapsed content is hidden", not section.content_widget.isVisible())
check("arrow points right when closed", section.arrow.rotation == 0,
      f"{section.arrow.rotation}")

# --- the non-animated path used when restoring a config ---
section.set_collapsed(False, animate=False)
check("snap-expand is immediate", section.content_widget.isVisible()
      and section.content_widget.maximumHeight() == _UNBOUNDED,
      f"visible={section.content_widget.isVisible()} "
      f"max={section.content_widget.maximumHeight()}")
check("snap-expand sets the arrow", section.arrow.rotation == 90)
section.set_collapsed(True, animate=False)
check("snap-collapse is immediate", not section.content_widget.isVisible())

# --- toggling faster than the animation must not strand it ---
for _ in range(3):
    section.set_collapsed(False)
    pump(30)
    section.set_collapsed(True)
    pump(30)
section.set_collapsed(False)
pump(400)
check("interrupted section toggling ends open",
      section.content_widget.isVisible()
      and section.content_widget.maximumHeight() == _UNBOUNDED,
      f"visible={section.content_widget.isVisible()} "
      f"max={section.content_widget.maximumHeight()}")

# --- real sections in the window still work ---
target = win.sections[0]
was = target.is_collapsed()
target.set_collapsed(not was)
pump(300)
check("a real section toggles", target.is_collapsed() != was)
target.set_collapsed(was)
pump(300)

win.shutdown()
print()
print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
