"""Collapsible settings sections and the page container they sit on."""

from __future__ import annotations

from PySide6 import QtCore
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..constants import (
    content_box_style,
    page_title_style,
    section_content_style,
)
from .widgets import AnimatedArrow, ToggleSwitch


class _SectionHeader(QWidget):
    """Clickable header strip.

    Replaces the original's reassignment of ``mouseReleaseEvent`` on a plain
    QWidget with a real subclass that emits a signal.
    """

    #: Emits the click position in header-local coordinates.
    clicked = QtCore.Signal(QtCore.QPoint)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(event.position().toPoint())
        super().mouseReleaseEvent(event)


class CollapsibleSection(QWidget):
    """A titled group of controls that expands and collapses on click."""

    def __init__(
        self,
        title_widget: QWidget,
        header_controls: list[QWidget] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        self.title_label = title_widget.findChild(QLabel)
        # The object name is the persistence key for the collapsed state, so it
        # must stay derived from the title text.
        self.setObjectName(self.title_label.text().replace(" ", ""))

        self.arrow = AnimatedArrow()
        self._arrow_animation = QPropertyAnimation(self.arrow, b"rotation")
        self._arrow_animation.setDuration(150)
        self._arrow_animation.setEasingCurve(QEasingCurve.InOutQuad)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.header_widget = _SectionHeader()
        self.header_widget.setCursor(QCursor(Qt.PointingHandCursor))
        self.header_widget.clicked.connect(self._on_header_clicked)

        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(self.arrow)
        header_layout.addWidget(title_widget, 1)
        for control in header_controls or ():
            header_layout.addWidget(control)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet(section_content_style("#2C2C2C"))
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(10, 5, 5, 10)
        self.content_layout.setSpacing(10)

        main_layout.addWidget(self.header_widget)
        main_layout.addWidget(self.content_widget)

        self.content_widget.hide()
        self._collapsed = True

    def add_widget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed

        self._arrow_animation.setEndValue(0 if collapsed else 90)
        self.content_widget.setVisible(not collapsed)
        self._arrow_animation.start()

        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            QtCore.QTimer.singleShot(0, parent.layout().activate)

    def _on_header_clicked(self, position: QtCore.QPoint) -> None:
        """Toggle, unless the click landed on the header's own toggle switch."""
        switch = self.header_widget.findChild(ToggleSwitch)
        if switch is not None:
            local = switch.mapFrom(self.header_widget, position)
            if switch.rect().contains(local):
                return
        self.set_collapsed(not self._collapsed)

    def apply_theme(self, text_color: str, border_color: str) -> None:
        self.content_widget.setStyleSheet(section_content_style(border_color))
        self.arrow.color = QColor(text_color)
        if self.title_label is not None:
            self.title_label.setStyleSheet(f"color:{text_color};")
        for label in self.content_widget.findChildren(QLabel):
            if label.objectName() != "value_label":
                label.setStyleSheet(f"color:{text_color};")


class CheatMenuPage(QWidget):
    """One page of the stacked menu: a title inside a rounded content box."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.sections: list[CollapsibleSection] = []
        self.themeable_labels: list[QLabel] = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        self.content_box = QWidget()
        self.box_layout = QVBoxLayout(self.content_box)
        self.box_layout.setContentsMargins(20, 30, 20, 20)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-size:24px; background: transparent;")
        self.box_layout.addWidget(self.title_label)
        self.box_layout.addSpacing(10)

        main_layout.addWidget(self.content_box)
        self.box_layout.addStretch()

    def add_widget(self, widget: QWidget, add_spacer: bool = True) -> None:
        # Insert before the trailing stretch so content stays top-aligned.
        self.box_layout.insertWidget(self.box_layout.count() - 1, widget)
        if add_spacer:
            self.box_layout.insertSpacing(self.box_layout.count() - 1, 10)

    def add_separator(self) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        self.add_widget(line)

    def add_section(self, section: CollapsibleSection) -> None:
        self.sections.append(section)
        self.add_widget(section)

    def add_heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold;")
        self.themeable_labels.append(label)
        self.add_widget(label, add_spacer=False)
        return label

    def apply_theme(self, content_color: str, text_color: str, border_color: str) -> None:
        self.content_box.setStyleSheet(content_box_style(content_color))
        self.title_label.setStyleSheet(page_title_style(text_color))

        for label in self.themeable_labels:
            label.setStyleSheet(f"color:{text_color}; font-weight: bold;")

        for section in self.sections:
            section.apply_theme(text_color, border_color)


def make_separator() -> QFrame:
    """A standalone horizontal rule, for use inside sections."""
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line
