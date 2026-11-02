"""Click-to-sample colour picker over a frozen screenshot of the desktop.

The screenshot is taken first and shown full-screen, so the pick is of exactly
what was on screen, not of whatever the picker itself is drawing. A loupe
follows the cursor so single pixels are easy to hit.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

#: Source pixels shown across the loupe, and its on-screen diameter.
LOUPE_PIXELS = 15
LOUPE_SIZE = 150


class ScreenPicker(QWidget):
    picked = Signal(QColor)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pixmap: QPixmap | None = None
        self._image: QImage | None = None
        self._cursor = QPoint(0, 0)
        self._done = False

    # --- lifecycle ---
    def start(self) -> bool:
        """Grab the screen and show the picker. False if nothing to grab."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return False
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            return False
        self.set_screenshot(pixmap)
        self.setGeometry(screen.geometry())
        self._cursor = self.mapFromGlobal(QCursor.pos())
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()
        return True

    def set_screenshot(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._image = pixmap.toImage()

    # --- sampling ---
    def _device_point(self, point: QPoint) -> QPoint:
        """Widget (logical) coordinates -> screenshot (device) pixels."""
        ratio = self._pixmap.devicePixelRatio() if self._pixmap else 1.0
        return QPoint(int(point.x() * ratio), int(point.y() * ratio))

    def colour_at(self, point: QPoint) -> QColor | None:
        if self._image is None:
            return None
        p = self._device_point(point)
        if not (0 <= p.x() < self._image.width() and 0 <= p.y() < self._image.height()):
            return None
        return self._image.pixelColor(p)

    # --- events ---
    def mouseMoveEvent(self, event) -> None:
        self._cursor = event.position().toPoint()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            self._finish(None)
            return
        self._finish(self.colour_at(event.position().toPoint()))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._finish(None)

    def _finish(self, colour: QColor | None) -> None:
        if self._done:
            return
        self._done = True
        self.close()
        if colour is not None and colour.isValid():
            self.picked.emit(colour)
        else:
            self.cancelled.emit()

    # --- drawing ---
    def paintEvent(self, event) -> None:
        if self._pixmap is None:
            return
        painter = QPainter(self)
        try:
            painter.drawPixmap(self.rect(), self._pixmap)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 40))
            self._draw_loupe(painter)
        finally:
            painter.end()

    def _draw_loupe(self, painter: QPainter) -> None:
        if self._image is None:
            return
        colour = self.colour_at(self._cursor)
        if colour is None:
            return

        # Sit to the lower right of the cursor, flipping near screen edges.
        x, y = self._cursor.x() + 24, self._cursor.y() + 24
        if x + LOUPE_SIZE > self.width():
            x = self._cursor.x() - 24 - LOUPE_SIZE
        if y + LOUPE_SIZE + 30 > self.height():
            y = self._cursor.y() - 24 - LOUPE_SIZE - 30
        circle = QRectF(x, y, LOUPE_SIZE, LOUPE_SIZE)

        centre = self._device_point(self._cursor)
        half = LOUPE_PIXELS // 2
        source = QRectF(centre.x() - half, centre.y() - half, LOUPE_PIXELS, LOUPE_PIXELS)

        painter.save()
        clip = QPainterPath()
        clip.addEllipse(circle)
        painter.setClipPath(clip)
        painter.fillRect(circle, QColor(10, 10, 10))
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawImage(circle, self._image, source)
        painter.restore()

        # Highlight the centre pixel.
        cell = LOUPE_SIZE / LOUPE_PIXELS
        mid = circle.center()
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(mid.x() - cell / 2, mid.y() - cell / 2, cell, cell))
        painter.setPen(QPen(QColor(0, 0, 0, 200), 2))
        painter.drawEllipse(circle)

        # Hex readout with a swatch.
        tag = QRectF(x, y + LOUPE_SIZE + 6, LOUPE_SIZE, 24)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 200))
        painter.drawRoundedRect(tag, 8, 8)
        painter.setBrush(colour)
        painter.drawEllipse(QRectF(tag.x() + 8, tag.y() + 5, 14, 14))
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            tag.adjusted(30, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, colour.name().upper()
        )
