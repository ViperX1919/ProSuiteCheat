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
    """Stacked widget whose pages slide in the direction of travel.

    Both pages move: the outgoing one leaves as the incoming one arrives, so
    the transition reads as a strip sliding sideways rather than a new page
    appearing over a gap.
    """

    DURATION = 260

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        #: The page sliding out, kept visible for the duration of the slide.
        self._outgoing: QWidget | None = None
        self._animations: list[QPropertyAnimation] = []
        #: Bumped per transition. A settle carrying a stale token is ignored,
        #: which is why this does not need an animation group - stopping a
        #: group and clearing it mid-flight is what produced
        #: "QAnimationGroup::animationAt: index is out of bounds".
        self._token = 0

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < self.count() or index == self.currentIndex():
            return

        # A click landing mid-transition finishes the previous one instantly
        # rather than being swallowed, so the nav always feels responsive.
        if self._animations:
            self._settle(self._token)

        outgoing = self.currentWidget()
        incoming = self.widget(index)
        if incoming is None or incoming is outgoing:
            return

        # Travelling down the nav slides left, travelling up slides right.
        offset = self.width() if index > self.currentIndex() else -self.width()

        # This hides the outgoing page, so it has to be shown again below.
        super().setCurrentIndex(index)

        # QStackedLayout re-applies the full rect to whichever page is current,
        # which pins the incoming page at x=0 and eats the slide entirely.
        # Suspending the layout for the duration is what makes it move at all.
        layout = self.layout()
        if layout is not None:
            layout.setEnabled(False)

        size = self.size()
        incoming.setGeometry(offset, 0, size.width(), size.height())
        if outgoing is not None:
            outgoing.setGeometry(0, 0, size.width(), size.height())
            outgoing.show()
            outgoing.raise_()
            incoming.raise_()

        self._outgoing = outgoing
        self._animations = [self._slide(incoming, QPoint(0, 0))]
        if outgoing is not None:
            self._animations.append(self._slide(outgoing, QPoint(-offset, 0)))
        for animation in self._animations:
            animation.start()

        self._token += 1
        token = self._token
        QtCore.QTimer.singleShot(self.DURATION + 20, lambda: self._settle(token))

    def _slide(self, page: QWidget, end: QPoint) -> QPropertyAnimation:
        animation = QPropertyAnimation(page, b"pos", self)
        animation.setDuration(self.DURATION)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.setStartValue(page.pos())
        animation.setEndValue(end)
        return animation

    def _settle(self, token: int | None = None) -> None:
        """Park the pages where the stacked layout expects to find them."""
        if token is not None and token != self._token:
            return

        for animation in self._animations:
            animation.stop()
            animation.deleteLater()
        self._animations.clear()

        if self._outgoing is not None:
            self._outgoing.hide()
            self._outgoing.move(0, 0)
            self._outgoing = None
        current = self.currentWidget()
        if current is not None:
            current.move(0, 0)

        layout = self.layout()
        if layout is not None and not layout.isEnabled():
            layout.setEnabled(True)
            layout.activate()

    def resizeEvent(self, event) -> None:
        # Mid-flight geometry is stale the moment the window resizes; snapping
        # to the end state is better than animating to the wrong place.
        if self._animations:
            self._settle(self._token)
        super().resizeEvent(event)


class AnimatedComboBox(QComboBox):
    """Combo box whose popup fades out as well as in.

    Windows animates the popup open but snaps it shut, so the two directions
    do not match. Fading the close makes the dropdown feel like it belongs to
    the same UI as the sections and page transitions.
    """

    DURATION = 110

    #: Emitted just before the popup opens, so a list that changes over time
    #: (like the open windows) can be refreshed while the user is looking.
    popupAboutToShow = QtCore.Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._fade: QPropertyAnimation | None = None
        #: Guards against Qt re-entering hidePopup while the fade is running.
        self._closing = False
        #: stop() emits finished(), so a fade cancelled by a re-open would
        #: otherwise run its completion handler and hide the popup we just
        #: opened. Stale tokens are ignored instead.
        self._token = 0

    def _popup_window(self) -> QWidget | None:
        view = self.view()
        return view.window() if view is not None else None

    def showPopup(self) -> None:
        self._closing = False
        self.popupAboutToShow.emit()
        super().showPopup()
        popup = self._popup_window()
        if popup is None:
            return
        popup.setWindowOpacity(0.0)
        self._animate(popup, 1.0, None)

    def hidePopup(self) -> None:
        popup = self._popup_window()
        # Fall straight through if there is nothing to animate, so the popup
        # can never be left stranded on screen.
        if self._closing or popup is None or not popup.isVisible():
            self._closing = False
            super().hidePopup()
            return
        self._closing = True
        self._animate(popup, 0.0, self._finish_hide)

    def _animate(self, popup: QWidget, end: float, on_done) -> None:
        self._token += 1
        token = self._token

        if self._fade is not None:
            self._fade.stop()
            self._fade.deleteLater()

        self._fade = QPropertyAnimation(popup, b"windowOpacity", self)
        self._fade.setDuration(self.DURATION)
        self._fade.setEasingCurve(QEasingCurve.OutQuad)
        self._fade.setStartValue(popup.windowOpacity())
        self._fade.setEndValue(end)
        if on_done is not None:
            self._fade.finished.connect(
                lambda: on_done() if token == self._token else None
            )
        self._fade.start()

    def _finish_hide(self) -> None:
        popup = self._popup_window()
        self._closing = False
        super().hidePopup()
        if popup is not None:
            # Reset, or the next open starts invisible.
            popup.setWindowOpacity(1.0)


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
    combo = AnimatedComboBox()
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


def tens_of_ms(value: int) -> str:
    """Render a slider stored in units of 10 ms, e.g. ``5`` -> ``50 ms``."""
    return f"{value * 10} ms"


def tenths(value: int) -> str:
    """Render a slider stored as tenths, e.g. ``5`` -> ``0.5``."""
    return str(value / 10.0)
