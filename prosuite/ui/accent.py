"""A row of accent-colour presets."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QVariantAnimation, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QWidget

PRESETS = (
    ("Crimson", "#881122"),
    ("Violet", "#7B3FE4"),
    ("Azure", "#2F80ED"),
    ("Teal", "#14B8A6"),
    ("Lime", "#84CC16"),
    ("Amber", "#F59E0B"),
    ("Rose", "#EC4899"),
    ("Frost", "#CBD5E1"),
)


class _Swatch(QWidget):
    clicked = Signal(QColor)

    SIZE = 30

    def __init__(self, name: str, colour: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.colour = QColor(colour)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip(name)
        self.selected = False
        self._grow = 0.0  # 0 at rest, 1 hovered
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.valueChanged.connect(self._on_grow)

    def _on_grow(self, value) -> None:
        self._grow = float(value)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._grow)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event) -> None:
        self._animate(1.0)

    def leaveEvent(self, event) -> None:
        self._animate(0.0)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.colour)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            radius = 9.0 + 3.0 * self._grow
            centre = QRectF(self.rect()).center()
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.colour)
            painter.drawEllipse(centre, radius, radius)
            if self.selected:
                ring = QColor("#FFFFFF")
                painter.setPen(QPen(ring, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(centre, 13.0, 13.0)
        finally:
            painter.end()


class AccentSwatches(QWidget):
    chosen = Signal(QColor)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._swatches: list[_Swatch] = []
        for name, colour in PRESETS:
            swatch = _Swatch(name, colour)
            swatch.clicked.connect(self.chosen)
            layout.addWidget(swatch)
            self._swatches.append(swatch)
        layout.addStretch(1)

    def set_current(self, colour: QColor) -> None:
        """Ring the swatch matching ``colour``, if any preset does."""
        for swatch in self._swatches:
            swatch.set_selected(swatch.colour.rgb() == colour.rgb())
