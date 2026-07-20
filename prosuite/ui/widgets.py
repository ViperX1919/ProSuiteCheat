"""Reusable custom widgets and the labelled-control factories.

The factories return a :class:`Control` bundle so callers get the row widget
and its interactive parts without unpacking positional tuples.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from ..constants import color_button_style, keybind_button_style

#: Kinds of control, used by the theming pass to know what to restyle.
TOGGLE = "toggle"
SLIDER = "slider"
PICKER = "picker"
DROPDOWN = "dropdown"
KEYBIND = "keybind"


@dataclass
class Control:
    """A labelled row plus the widgets inside it."""

    widget: QWidget
    kind: str
    #: The interactive widget (toggle, slider, button, combo box).
    input: QWidget
    #: The caption on the left, if the row has one.
    label: QLabel | None = None
    #: The live value readout on the right, sliders only.
    value_label: QLabel | None = None


class AnimatedArrow(QWidget):
    """Small triangle that rotates 0 -> 90 degrees as a section expands."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(15, 15)
        self._rotation = 0.0
        self.color = QColor("#FFFFFF")

    @QtCore.Property(float)
    def rotation(self) -> float:
        return self._rotation

    @rotation.setter
    def rotation(self, value: float) -> None:
        self._rotation = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.color)

            center = QPointF(self.width() / 2, self.height() / 2)
            painter.translate(center)
            painter.rotate(self._rotation)
            painter.translate(-center)

            painter.drawPolygon(
                QPolygonF([QPointF(5, 5), QPointF(10, 7.5), QPointF(5, 10)])
            )
        finally:
            painter.end()


class ToggleSwitch(QWidget):
    """iOS-style sliding on/off switch."""

    toggled = QtCore.Signal(bool)

    def __init__(self, theme_color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFixedSize(50, 28)
        self.theme_color = theme_color

        self._checked = False
        self._knob_x = 0.0
        self._animation = QPropertyAnimation(self, b"knob_x", self)
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.InOutCubic)

    def mousePressEvent(self, event) -> None:
        self.setChecked(not self._checked)

    def setChecked(self, checked: bool) -> None:
        if self._checked == checked:
            return
        self._checked = checked
        self.toggled.emit(checked)
        self._animation.setStartValue(self._knob_x)
        self._animation.setEndValue(self.width() - self.height() if checked else 0)
        self._animation.start()

    def isChecked(self) -> bool:
        return self._checked

    @QtCore.Property(float)
    def knob_x(self) -> float:
        return self._knob_x

    @knob_x.setter
    def knob_x(self, value: float) -> None:
        self._knob_x = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            rect = self.rect()
            painter.setBrush(QColor(self.theme_color) if self._checked else QColor("#555"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

            knob = QtCore.QRectF(self._knob_x, 2, rect.height() - 4, rect.height() - 4)
            painter.setBrush(QColor("#FFF"))
            painter.drawEllipse(knob)
        finally:
            painter.end()


class AnimatedStackedWidget(QtWidgets.QStackedWidget):
    """Stacked widget whose pages slide in from the right."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._animation = QPropertyAnimation(self, b"pos", self)
        self._animation.setDuration(300)
        self._animation.setEasingCurve(QEasingCurve.InOutQuad)

    def setCurrentIndex(self, index: int) -> None:
        if (
            self.currentIndex() == index
            or self._animation.state() == QPropertyAnimation.Running
        ):
            return

        incoming = self.widget(index)
        if incoming is self.currentWidget():
            return

        incoming.move(self.width(), 0)
        super().setCurrentIndex(index)
        self._animation.setTargetObject(incoming)
        self._animation.setStartValue(incoming.pos())
        self._animation.setEndValue(QPoint(0, 0))
        self._animation.start()


# --- control factories ---
def _row() -> tuple[QWidget, QHBoxLayout]:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    return widget, layout


def make_toggle(text: str, theme_color: QColor) -> Control:
    widget, layout = _row()
    label = QLabel(text)
    switch = ToggleSwitch(theme_color)
    layout.addWidget(label, 1)
    layout.addWidget(switch)
    return Control(widget=widget, kind=TOGGLE, input=switch, label=label)


def make_slider(
    text: str,
    minimum: int,
    maximum: int,
    value: int,
    value_format: Callable[[int], str] = str,
) -> Control:
    """A caption, a slider, and a live readout formatted by ``value_format``."""
    widget, layout = _row()
    label = QLabel(text)
    slider = QSlider(Qt.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(value)

    readout = QLabel(value_format(value))
    readout.setObjectName("value_label")
    slider.valueChanged.connect(lambda v: readout.setText(value_format(v)))

    layout.addWidget(label)
    layout.addWidget(slider, 1)
    layout.addWidget(readout)
    return Control(
        widget=widget, kind=SLIDER, input=slider, label=label, value_label=readout
    )


def make_color_picker(text: str, initial: str = "#FF0000") -> Control:
    widget, layout = _row()
    label = QLabel(text)
    button = QPushButton()
    button.setFixedSize(30, 30)
    button.setStyleSheet(color_button_style(initial))
    layout.addWidget(label, 1)
    layout.addWidget(button)
    return Control(widget=widget, kind=PICKER, input=button, label=label)


def make_dropdown(text: str, items: Sequence[str]) -> Control:
    widget, layout = _row()
    label = QLabel(text)
    combo = QComboBox()
    combo.addItems(list(items))
    layout.addWidget(label, 1)
    layout.addWidget(combo)
    return Control(widget=widget, kind=DROPDOWN, input=combo, label=label)


def make_keybind_button(default_text: str) -> Control:
    widget = QWidget()
    widget.setContentsMargins(0, 0, 0, 0)
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(5, 0, 0, 0)

    button = QPushButton(default_text)
    button.setFixedWidth(110)
    button.setStyleSheet(keybind_button_style())
    layout.addWidget(button)
    return Control(widget=widget, kind=KEYBIND, input=button)


# --- value formatters used by the slider factory ---
def signed(value: int) -> str:
    """Render an offset with an explicit sign, e.g. ``+12``."""
    return f"{value:+}"


def tenths(value: int) -> str:
    """Render a slider stored as tenths, e.g. ``5`` -> ``0.5``."""
    return str(value / 10.0)
