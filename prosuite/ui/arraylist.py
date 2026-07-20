"""Draggable HUD listing the currently active features."""

from __future__ import annotations

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QVBoxLayout

from ..constants import ACCENT_RED, arraylist_label_style

ANIMATION_MS = 250
EDGE_MARGIN = 40
#: Horizontal padding between the widest label and the widget edge.
LABEL_PADDING = 32
MIN_WIDTH = 100


class ArrayListWidget(QtWidgets.QWidget):
    """Vertical stack of feature captions that slides entries in and out."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowTransparentForInput, True)
        self.setStyleSheet("background: transparent;")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)
        self._layout.setAlignment(Qt.AlignTop)

        self.features: dict[str, QLabel] = {}
        self._font_size = 14
        self._color = QColor(ACCENT_RED)
        self._style = "Default"

        self.is_move_mode = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._positioned = False

        # PySide does not keep a reference to a running animation group, so
        # they are held here until they finish.
        self._animations: list[QAbstractAnimation] = []

    # --- geometry ---
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._positioned:
            screen = QtGui.QGuiApplication.primaryScreen().geometry()
            self.move(
                screen.width() - self.sizeHint().width() - EDGE_MARGIN, EDGE_MARGIN
            )
            self._positioned = True

    def sizeHint(self) -> QSize:
        font = self.font()
        font.setPointSize(self._font_size)
        metrics = QtGui.QFontMetrics(font)
        widest = max(
            (metrics.horizontalAdvance(label.text()) for label in self.features.values()),
            default=0,
        )
        return QSize(
            max(widest + LABEL_PADDING, MIN_WIDTH), self._layout.sizeHint().height()
        )

    def _label_width(self) -> int:
        return max(self.sizeHint().width() - 16, 80)

    # --- feature entries ---
    def add_feature(self, name: str, display_name: str) -> None:
        if name in self.features:
            self.update_feature(name, display_name)
            return

        label = QLabel(display_name, self)
        label.setAttribute(Qt.WA_TranslucentBackground, True)
        self._style_label(label)
        label.setFixedHeight(label.sizeHint().height())
        label.setFixedWidth(self._label_width())

        self._layout.insertWidget(len(self.features), label)
        self.features[name] = label
        self.adjustSize()

        self._slide_in(label)
        self._reflow()

    def remove_feature(self, name: str) -> None:
        label = self.features.pop(name, None)
        if label is None:
            return
        self._slide_out(label, lambda: self._destroy_label(label))
        self._reflow()

    def update_feature(self, name: str, display_name: str) -> None:
        label = self.features.get(name)
        if label is None:
            return
        label.setText(display_name)
        self._style_label(label)
        label.adjustSize()
        label.setFixedWidth(self._label_width())
        self.adjustSize()
        self._reflow()

    def _destroy_label(self, label: QLabel) -> None:
        self._layout.removeWidget(label)
        label.deleteLater()
        self.adjustSize()
        self._reflow()

    # --- animation ---
    def _track(self, animation: QAbstractAnimation) -> None:
        """Keep a running animation alive and drop it once it finishes."""
        self._animations.append(animation)
        animation.finished.connect(lambda: self._animations.remove(animation))
        animation.start(QAbstractAnimation.DeleteWhenStopped)

    @staticmethod
    def _fade(label: QLabel, start: float, end: float) -> QPropertyAnimation:
        """Fade a child widget.

        Child widgets ignore setWindowOpacity - only top-level windows honour
        it - so a graphics effect is used instead.
        """
        effect = label.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(label)
            label.setGraphicsEffect(effect)
        effect.setOpacity(start)

        animation = QPropertyAnimation(effect, b"opacity")
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(ANIMATION_MS)
        return animation

    def _slide_in(self, label: QLabel) -> None:
        end_pos = label.pos()
        start_pos = QPoint(max(self.width(), 0), end_pos.y())
        label.move(start_pos)

        slide = QPropertyAnimation(label, b"pos")
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setDuration(ANIMATION_MS)
        slide.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup()
        group.addAnimation(slide)
        group.addAnimation(self._fade(label, 0.0, 1.0))
        self._track(group)

    def _slide_out(self, label: QLabel, on_finished) -> None:
        start_pos = label.pos()
        end_pos = QPoint(max(self.width(), 0), start_pos.y())

        slide = QPropertyAnimation(label, b"pos")
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setDuration(ANIMATION_MS)
        slide.setEasingCurve(QEasingCurve.InCubic)

        group = QParallelAnimationGroup()
        group.addAnimation(slide)
        group.addAnimation(self._fade(label, 1.0, 0.0))
        group.finished.connect(on_finished)
        self._track(group)

    def _reflow(self) -> None:
        """Animate every entry to its new stacked position."""
        margins = self._layout.contentsMargins()
        y = margins.top()

        group = QParallelAnimationGroup()
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item else None
            if widget is None:
                continue

            animation = QPropertyAnimation(widget, b"pos")
            animation.setDuration(ANIMATION_MS)
            animation.setEndValue(QPoint(margins.left(), y))
            animation.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(animation)
            y += widget.height() + self._layout.spacing()

        if group.animationCount():
            self._track(group)

    # --- appearance ---
    def _style_label(self, label: QLabel) -> None:
        label.setStyleSheet(
            arraylist_label_style(self._color.name(), self._font_size, self._style)
        )

    def _restyle_all(self) -> None:
        width = self._label_width()
        for label in self.features.values():
            self._style_label(label)
            label.adjustSize()
            label.setFixedWidth(width)
        self.adjustSize()
        self._reflow()

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        self._restyle_all()

    def set_style(self, style_name: str) -> None:
        self._style = style_name
        self._restyle_all()

    def set_text_color(self, color: QColor) -> None:
        self._color = color
        self._restyle_all()

    # --- repositioning ---
    def toggle_move_mode(self) -> bool:
        self.is_move_mode = not self.is_move_mode
        self.setWindowFlag(Qt.WindowTransparentForInput, not self.is_move_mode)
        if self.is_move_mode:
            self.setStyleSheet(
                f"background: rgba(30,30,30,0.5); border: 2px dashed {self._color.name()};"
            )
        else:
            self.setStyleSheet("background: transparent; border: none;")
        self.show()
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
        if event.button() == Qt.LeftButton:
            self._dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.is_move_mode:
            self.toggle_move_mode()
            event.accept()
        else:
            super().keyPressEvent(event)
