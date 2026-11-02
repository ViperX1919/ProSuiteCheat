"""Live view of what the colour filter is matching.

Tuning tolerance, saturation and brightness blind is guesswork. This shows the
mask itself - every matching pixel lit in the accent colour on a dark panel,
with the crosshair marked - so the sliders have visible, immediate effects.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class MaskPreview(QWidget):
    HEIGHT = 190

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.accent = QColor("#881122")
        self._panel = QColor("#0B0B0B")
        self._text = QColor("#FFFFFF")

        self._rgba: np.ndarray | None = None  # kept alive for QImage
        self._image: QImage | None = None
        self._matched = 0.0  # fraction of pixels matching, for the readout

        #: 0..1, eased toward 1 while something matches. Drives the border.
        self._lit = 0.0
        self._lit_target = 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(220)
        self._fade.valueChanged.connect(self._on_fade)

    @property
    def lit(self) -> float:
        return self._lit

    def _on_fade(self, value) -> None:
        self._lit = float(value)
        self.update()

    def set_mask(self, mask: np.ndarray | None) -> None:
        """Show a uint8 mask (255 = match), or the empty state for ``None``."""
        if mask is None or mask.size == 0:
            self._rgba = self._image = None
            self._matched = 0.0
            self._animate_to(0.0)
            self.update()
            return

        height, width = mask.shape[:2]
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[..., 0] = self.accent.red()
        rgba[..., 1] = self.accent.green()
        rgba[..., 2] = self.accent.blue()
        rgba[..., 3] = mask
        self._rgba = rgba
        self._image = QImage(rgba.data, width, height, width * 4, QImage.Format_RGBA8888)
        self._matched = float(np.count_nonzero(mask)) / mask.size
        self._animate_to(1.0 if self._matched > 0 else 0.0)
        self.update()

    def _animate_to(self, target: float) -> None:
        if target == self._lit_target:
            return
        self._lit_target = target
        self._fade.stop()
        self._fade.setStartValue(self._lit)
        self._fade.setEndValue(target)
        self._fade.start()

    def apply_theme(self, accent: QColor, panel: str, text: str) -> None:
        self.accent = QColor(accent)
        self._panel = QColor(panel)
        self._text = QColor(text)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)

            border = QColor(self.accent)
            border.setAlphaF(0.25 + 0.75 * self._lit)
            painter.setPen(QPen(border, 1.5))
            painter.setBrush(self._panel)
            painter.drawRoundedRect(outer, 10, 10)

            inner = outer.adjusted(8, 8, -8, -8)
            if self._image is None:
                muted = QColor(self._text)
                muted.setAlphaF(0.45)
                painter.setPen(muted)
                painter.drawText(inner, Qt.AlignCenter, "No signal\nturn on a module to scan")
                return

            # Fit the square-ish mask inside the panel, preserving aspect.
            image = self._image
            scale = min(inner.width() / image.width(), inner.height() / image.height())
            w, h = image.width() * scale, image.height() * scale
            target = QRectF(
                inner.center().x() - w / 2, inner.center().y() - h / 2, w, h
            )
            grid = QColor(self._text)
            grid.setAlphaF(0.07)
            painter.fillRect(target, grid)
            painter.drawImage(target, image)

            # Crosshair at the capture centre.
            cross = QColor(self._text)
            cross.setAlphaF(0.5)
            painter.setPen(QPen(cross, 1))
            c = target.center()
            painter.drawLine(QPointF(c.x() - 7, c.y()), QPointF(c.x() + 7, c.y()))
            painter.drawLine(QPointF(c.x(), c.y() - 7), QPointF(c.x(), c.y() + 7))

            label = QColor(self._text)
            label.setAlphaF(0.7)
            painter.setPen(label)
            painter.drawText(
                outer.adjusted(14, 0, -14, -8),
                Qt.AlignBottom | Qt.AlignRight,
                f"{self._matched * 100:.2f}% match",
            )
        finally:
            painter.end()
