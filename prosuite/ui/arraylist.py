"""Draggable HUD listing the currently active features.

Entries are positioned manually rather than by a QLayout. A layout fights
property animations - it re-snaps every child to its computed slot on the next
resize or polish - so anything animating ``pos`` inside one either jitters or
never moves at all. Owning the geometry outright is what makes the slide work.
"""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel

from ..constants import ACCENT_RED, arraylist_label_style

ANIMATION_MS = 220
#: Gap left between the list and the screen edge on first show.
EDGE_MARGIN = 40
#: Padding between the entries and the widget edge.
MARGIN = 8
#: Vertical gap between entries.
SPACING = 4
MIN_WIDTH = 90
#: The drag surface needs to stay grabbable even with no entries in it.
MOVE_MODE_MIN = QSize(190, 72)


class _Entry:
    """One row: its label, its animations, and its sort key."""

    def __init__(self, name: str, text: str, parent: QtWidgets.QWidget):
        self.name = name
        #: Ordering key. Deliberately *not* the live caption - entries such as
        #: "Prediction [0.42]" rewrite themselves every frame, and sorting on
        #: that would make the whole list reshuffle continuously.
        self.sort_text = text
        #: Rendered width of ``sort_text``, in pixels. Recomputed by measure()
        #: whenever the caption or the font changes.
        self.sort_width = 0
        self.leaving = False
        self.entering = True
        #: Bumped whenever the entry's fate changes, which invalidates any
        #: deletion an earlier removal already scheduled.
        self.token = 0

        self.label = QLabel(text, parent)
        self.label.setAttribute(Qt.WA_TranslucentBackground, True)

        # Child widgets ignore setWindowOpacity - only top-level windows honour
        # it - so fading needs a graphics effect.
        self.effect = QGraphicsOpacityEffect(self.label)
        self.effect.setOpacity(0.0)
        self.label.setGraphicsEffect(self.effect)

        self.move_anim = QPropertyAnimation(self.label, b"pos", self.label)
        self.move_anim.setDuration(ANIMATION_MS)
        self.fade_anim = QPropertyAnimation(self.effect, b"opacity", self.label)
        self.fade_anim.setDuration(ANIMATION_MS)

    def measure(self) -> None:
        """Cache the caption's rendered width for the current font."""
        self.sort_width = QtGui.QFontMetrics(self.label.font()).horizontalAdvance(
            self.sort_text
        )

    @property
    def sort_key(self) -> tuple[int, str]:
        """Widest caption first, alphabetical between equal widths.

        Measured in pixels rather than characters: the font is proportional,
        so "FOV" and "ESP" are the same length but visibly different widths.
        """
        return (-self.sort_width, self.sort_text.lower())

    def is_animating(self) -> bool:
        return (
            self.move_anim.state() == QAbstractAnimation.Running
            or self.fade_anim.state() == QAbstractAnimation.Running
        )

    def is_settled_at(self, pos: QPoint, opacity: float) -> bool:
        return (
            not self.is_animating()
            and self.label.pos() == pos
            and abs(self.effect.opacity() - opacity) < 1e-3
        )

    def animate_to(self, pos: QPoint, opacity: float, curve: QEasingCurve) -> None:
        """Retarget both animations from wherever the entry currently is."""
        self.move_anim.stop()
        self.move_anim.setEasingCurve(curve)
        self.move_anim.setStartValue(self.label.pos())
        self.move_anim.setEndValue(pos)

        self.fade_anim.stop()
        self.fade_anim.setEasingCurve(curve)
        self.fade_anim.setStartValue(self.effect.opacity())
        self.fade_anim.setEndValue(opacity)

        self.move_anim.start()
        self.fade_anim.start()

    def place(self, pos: QPoint, opacity: float) -> None:
        """Jump straight to a state, cancelling any animation in flight."""
        self.move_anim.stop()
        self.fade_anim.stop()
        self.label.move(pos)
        self.effect.setOpacity(opacity)


class ArrayListWidget(QtWidgets.QWidget):
    """Vertical stack of feature captions that slides entries in and out."""

    #: Emitted when move mode is entered or left, including via Escape.
    move_mode_changed = Signal(bool)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowTransparentForInput, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._entries: dict[str, _Entry] = {}
        self._font_size = 14
        self._color = QColor(ACCENT_RED)
        self._style = "Default"

        self.is_move_mode = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._positioned = False
        #: Which edge stays put as the list grows. Entries slide out towards it.
        self._anchor_right = True

        self.resize(MIN_WIDTH, MARGIN * 2)

    @property
    def features(self) -> dict[str, _Entry]:
        """Live entries by feature name. Supports ``name in list.features``."""
        return self._entries

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------
    def add_feature(self, name: str, display_name: str) -> None:
        entry = self._entries.get(name)
        if entry is not None:
            # Re-enabled before its exit finished: revive the existing row
            # instead of stacking a second one on top of it.
            entry.leaving = False
            entry.token += 1
            if entry.sort_text != display_name:
                entry.sort_text = display_name
                entry.label.setText(display_name)
                self._restyle(entry)
            self._relayout()
            return

        entry = _Entry(name, display_name, self)
        self._restyle(entry)
        entry.label.show()
        self._entries[name] = entry
        self._relayout()

    def remove_feature(self, name: str) -> None:
        entry = self._entries.get(name)
        if entry is None or entry.leaving:
            return

        entry.leaving = True
        entry.token += 1
        token = entry.token
        # Deletion is scheduled rather than driven off finished() because
        # stop() also emits finished(), and these animations get retargeted
        # constantly - a finished() handler would delete live entries.
        QTimer.singleShot(
            ANIMATION_MS + 40, lambda: self._destroy_entry(name, token)
        )
        self._relayout()

    def update_feature(self, name: str, display_name: str) -> None:
        """Rewrite an entry's caption without disturbing its position."""
        entry = self._entries.get(name)
        if entry is None or entry.label.text() == display_name:
            return
        entry.label.setText(display_name)
        entry.label.adjustSize()
        self._relayout()

    def _destroy_entry(self, name: str, token: int) -> None:
        entry = self._entries.get(name)
        if entry is None or not entry.leaving or entry.token != token:
            return
        del self._entries[name]
        entry.label.deleteLater()
        self._relayout()

    def _ordered(self) -> list[_Entry]:
        live = [e for e in self._entries.values() if not e.leaving]
        return sorted(live, key=lambda e: e.sort_key)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def sizeHint(self) -> QSize:
        entries = self._ordered()
        width = max((e.label.width() for e in entries), default=0)
        height = sum(e.label.height() for e in entries)
        if len(entries) > 1:
            height += SPACING * (len(entries) - 1)

        size = QSize(max(width + MARGIN * 2, MIN_WIDTH), height + MARGIN * 2)
        if self.is_move_mode:
            size = size.expandedTo(MOVE_MODE_MIN)
        return size

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._positioned:
            screen = QtGui.QGuiApplication.primaryScreen().geometry()
            self.move(screen.width() - self.width() - EDGE_MARGIN, EDGE_MARGIN)
            self._positioned = True

    def _offscreen_x(self, entry: _Entry) -> int:
        """Where an entry sits while hidden - just past the anchored edge."""
        if self._anchor_right:
            return self.width() + 4
        return -entry.label.width() - 4

    def _resize_anchored(self, size: QSize) -> None:
        """Resize while keeping the anchored edge pinned in screen space."""
        if size == self.size():
            return
        right = self.x() + self.width()
        self.resize(size)
        if self._anchor_right and self._positioned and not self._dragging:
            self.move(right - size.width(), self.y())

    def _update_anchor(self) -> None:
        if not self._positioned or self._dragging:
            return
        screen = QtGui.QGuiApplication.primaryScreen().geometry()
        self._anchor_right = (self.x() + self.width() / 2) >= screen.center().x()

    def _relayout(self) -> None:
        """Re-sort, resize, and animate every entry to where it belongs."""
        self._update_anchor()
        self._resize_anchored(self.sizeHint())

        y = MARGIN
        for entry in self._ordered():
            x = (
                self.width() - MARGIN - entry.label.width()
                if self._anchor_right
                else MARGIN
            )
            target = QPoint(x, y)

            if entry.entering:
                entry.entering = False
                entry.place(QPoint(self._offscreen_x(entry), y), 0.0)

            if not entry.is_settled_at(target, 1.0):
                entry.animate_to(target, 1.0, QEasingCurve.OutCubic)
            y += entry.label.height() + SPACING

        for entry in self._entries.values():
            if not entry.leaving:
                continue
            exit_pos = QPoint(self._offscreen_x(entry), entry.label.y())
            if not entry.is_settled_at(exit_pos, 0.0):
                entry.animate_to(exit_pos, 0.0, QEasingCurve.InCubic)

        self.update()

    # ------------------------------------------------------------------
    # Appearance
    # ------------------------------------------------------------------
    def _restyle(self, entry: _Entry) -> None:
        label = entry.label
        font = label.font()
        font.setPointSize(self._font_size)
        label.setFont(font)
        label.setStyleSheet(
            arraylist_label_style(self._color.name(), self._font_size, self._style)
        )
        label.adjustSize()
        # The font just changed, so the cached sort width is stale.
        entry.measure()

    def _restyle_all(self) -> None:
        for entry in self._entries.values():
            self._restyle(entry)
        self._relayout()

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        self._restyle_all()

    def set_style(self, style_name: str) -> None:
        self._style = style_name
        self._restyle_all()

    def set_text_color(self, color: QColor) -> None:
        self._color = color
        self._restyle_all()

    def paintEvent(self, event) -> None:
        # Nothing to draw normally - the entries are child widgets. The move
        # mode chrome is not just a hint: a fully transparent translucent
        # window has no hit-testable pixels, so without these the widget could
        # never be grabbed no matter what the input flags said.
        if not self.is_move_mode:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(30, 30, 30, 140))

        border = self.rect().adjusted(1, 1, -2, -2)
        painter.setPen(QPen(self._color, 2, Qt.DashLine))
        painter.drawRect(border)

        if not self._ordered():
            painter.setPen(QPen(self._color))
            painter.drawText(border, Qt.AlignCenter, "Drag to reposition")

    # ------------------------------------------------------------------
    # Repositioning
    # ------------------------------------------------------------------
    def toggle_move_mode(self) -> bool:
        self.is_move_mode = not self.is_move_mode

        # Click-through has to come off, and the window has to become
        # activatable for the Escape shortcut to ever reach keyPressEvent.
        self.setWindowFlag(Qt.WindowTransparentForInput, not self.is_move_mode)
        self.setAttribute(Qt.WA_ShowWithoutActivating, not self.is_move_mode)
        self.setFocusPolicy(Qt.StrongFocus if self.is_move_mode else Qt.NoFocus)

        self._relayout()
        # Changing window flags hides a visible window; it must be re-shown.
        self.show()
        self.raise_()
        if self.is_move_mode:
            self.activateWindow()
            self.setFocus(Qt.OtherFocusReason)

        self.move_mode_changed.emit(self.is_move_mode)
        return self.is_move_mode

    def mousePressEvent(self, event) -> None:
        if self.is_move_mode and event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            # Dropping it on the other half of the screen flips which edge the
            # entries align to.
            self._relayout()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.is_move_mode:
            self.toggle_move_mode()
            event.accept()
        else:
            super().keyPressEvent(event)
