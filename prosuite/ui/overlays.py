"""Click-through fullscreen overlays: FOV circle, ESP, prediction, radar."""

from __future__ import annotations

import math

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from ..constants import ACCENT_RED
from ..core.detection import RadarTarget

#: Flags shared by every overlay: borderless, always on top, no focus stealing.
_OVERLAY_FLAGS = (
    Qt.FramelessWindowHint
    | Qt.WindowStaysOnTopHint
    | Qt.Tool
    | Qt.WindowDoesNotAcceptFocus
)


class _OverlayBase(QtWidgets.QWidget):
    """Common window setup for a transparent, click-through overlay."""

    #: Set False for overlays the user can drag (radar, arraylist).
    click_through = True

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        flags = _OVERLAY_FLAGS
        if self.click_through:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    @staticmethod
    def screen_geometry() -> QRect:
        return QtGui.QGuiApplication.primaryScreen().geometry()


class FOVOverlay(_OverlayBase):
    """Circle showing the region the detector samples."""

    def __init__(self, color: QColor | None = None):
        super().__init__()
        self.radius = 100
        self.color = color or QColor(ACCENT_RED)

    def set_fov_radius(self, radius: int) -> None:
        self.radius = radius
        self.update()

    def set_color(self, color: QColor) -> None:
        self.color = color
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(self.color, 2))
            painter.drawEllipse(self.screen_geometry().center(), self.radius, self.radius)
        finally:
            painter.end()


class RenderOverlay(_OverlayBase):
    """Draws the ESP box/corners/head-circle/pixel mask over the target."""

    GLOW_LAYERS = 5

    def __init__(self):
        super().__init__()
        self.color = QColor(ACCENT_RED)
        self.opacity = 100
        self.glow = False
        self.tracers = False
        self.esp_mode = "Box"

        self._bbox: tuple[int, int, int, int] | None = None
        self._offset = QPoint(0, 0)
        self._screen_pos: QPoint | None = None
        self._mask: np.ndarray | None = None

    def show_target(
        self,
        bbox: tuple[int, int, int, int] | None,
        offset: tuple[int, int],
        screen_pos: tuple[int, int] | None,
        mask: np.ndarray | None,
    ) -> None:
        self._bbox = bbox
        self._offset = QPoint(*offset)
        self._screen_pos = QPoint(*screen_pos) if screen_pos else None
        self._mask = mask
        self.update()

    def clear(self) -> None:
        if self._bbox is None and self._mask is None:
            return
        self._bbox = None
        self._mask = None
        self._screen_pos = None
        self.update()

    def paintEvent(self, event) -> None:
        if self._bbox is None and self._mask is None:
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            if self.tracers and self._screen_pos is not None:
                geometry = self.screen_geometry()
                origin = QPoint(geometry.width() // 2, geometry.height())
                painter.setPen(QPen(self.color, 1))
                painter.drawLine(origin, self._screen_pos)

            painter.save()
            painter.translate(self._offset)

            if self.esp_mode == "Pixel" and self._mask is not None:
                self._draw_pixel_mask(painter)
            elif self._bbox is not None:
                rect = QtCore.QRectF(*self._bbox)
                if self.glow:
                    self._draw_glow(painter, rect)
                painter.setPen(QPen(self.color, 2))
                painter.setBrush(Qt.NoBrush)
                self._draw_shape(painter, rect)

            painter.restore()
        finally:
            painter.end()

    def _draw_glow(self, painter: QPainter, rect: QtCore.QRectF) -> None:
        """Stack progressively wider, fainter outlines around the box."""
        painter.setBrush(Qt.NoBrush)
        for layer in range(self.GLOW_LAYERS):
            color = QColor(self.color)
            color.setAlpha(int(self.opacity / ((layer + 1) * 2.5)))
            painter.setPen(
                QPen(color, 2 + layer * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            )
            spread = layer * 2
            painter.drawRoundedRect(
                rect.adjusted(-spread, -spread, spread, spread), 5, 5
            )

    def _draw_shape(self, painter: QPainter, rect: QtCore.QRectF) -> None:
        if self.esp_mode == "Box":
            painter.drawRect(rect)
        elif self.esp_mode == "Corner Box":
            self._draw_corners(painter, rect)
        elif self.esp_mode == "Head Circle":
            radius = rect.width() / 4
            head = QPointF(rect.center().x(), rect.top())
            painter.drawEllipse(head, radius, radius)

    @staticmethod
    def _draw_corners(painter: QPainter, rect: QtCore.QRectF) -> None:
        size = min(rect.width(), rect.height()) / 4
        for corner, dx, dy in (
            (rect.topLeft(), 1, 1),
            (rect.topRight(), -1, 1),
            (rect.bottomLeft(), 1, -1),
            (rect.bottomRight(), -1, -1),
        ):
            painter.drawLine(corner, corner + QPointF(size * dx, 0))
            painter.drawLine(corner, corner + QPointF(0, size * dy))

    def _draw_pixel_mask(self, painter: QPainter) -> None:
        """Tint every matching pixel of the mask in the ESP colour."""
        mask = self._mask
        height, width = mask.shape

        image = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
        image.fill(Qt.transparent)

        color = QColor(self.color)
        color.setAlpha(self.opacity)

        # Write straight into the QImage buffer. bytesPerLine is honoured
        # because Qt pads scanlines to a 32-bit boundary.
        stride = image.bytesPerLine() // 4
        buffer = np.frombuffer(image.bits(), dtype=np.uint32, count=stride * height)
        buffer = buffer.reshape(height, stride)
        buffer[:, :width][mask > 0] = color.rgba()

        painter.drawImage(QPoint(0, 0), image)


class PredictionAimOverlay(_OverlayBase):
    """Arrows from a target's current position to its predicted position."""

    def __init__(self, color: QColor | None = None):
        super().__init__()
        self.color = color or QColor(ACCENT_RED)
        self._lines: tuple = ()

    def set_color(self, color: QColor) -> None:
        self.color = color
        self.update()

    def show_lines(self, lines) -> None:
        self._lines = tuple(lines)
        self.update()

    def clear(self) -> None:
        if not self._lines:
            return
        self._lines = ()
        self.update()

    def paintEvent(self, event) -> None:
        if not self._lines:
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            for start, end, speed in self._lines:
                self._draw_arrow(painter, QPoint(*start), QPoint(*end), speed)
        finally:
            painter.end()

    def _draw_arrow(
        self, painter: QPainter, start: QPoint, end: QPoint, speed: float
    ) -> None:
        # Faster targets get brighter, thicker, larger-headed arrows.
        color = QColor(self.color)
        color.setAlpha(min(255, int(speed * 15)))
        thickness = max(1, min(5, int(speed / 2)))

        painter.setPen(QPen(color, thickness, Qt.SolidLine))
        painter.drawLine(start, end)

        dx = end.x() - start.x()
        dy = end.y() - start.y()
        if dx == 0 and dy == 0:
            return

        painter.setBrush(color)
        angle = math.atan2(dy, dx)
        length = max(8, min(15, int(speed / 2)))
        spread = math.pi / 6

        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(end),
                    QPointF(
                        end.x() - length * math.cos(angle - spread),
                        end.y() - length * math.sin(angle - spread),
                    ),
                    QPointF(
                        end.x() - length * math.cos(angle + spread),
                        end.y() - length * math.sin(angle + spread),
                    ),
                ]
            )
        )


class RadarWidget(_OverlayBase):
    """Top-down radar showing target bearings within the FOV cone."""

    click_through = False

    #: Radar cone angle at the smallest and largest FOV settings.
    MIN_CONE_ANGLE = 15.0
    MAX_CONE_ANGLE = 170.0
    PLAYER_ARROW_SIZE = 15
    TARGET_DOT_RADIUS = 5

    def __init__(self, radius: int = 150, esp_color: QColor | None = None):
        super().__init__()
        self.radius = radius
        self.esp_color = esp_color or QColor(ACCENT_RED)
        self.setFixedSize(radius * 2 + 20, radius * 2 + 20)
        self.center = QPoint(self.width() // 2, self.height() // 2)

        self.targets: tuple[RadarTarget, ...] = ()
        self.player_angle = 0.0
        self._cone_angle = 90.0

        self.move_mode = False
        self._drag_offset = QPoint()
        self.setMouseTracking(True)
        self.setWindowFlag(Qt.WindowTransparentForInput, True)

    def set_cone_angle(self, fov: int, minimum: int, maximum: int) -> None:
        """Map the FOV slider's position onto the visual cone width."""
        span = maximum - minimum
        fraction = (fov - minimum) / span if span > 0 else 0.0
        self._cone_angle = self.MIN_CONE_ANGLE + fraction * (
            self.MAX_CONE_ANGLE - self.MIN_CONE_ANGLE
        )
        self.update()

    def set_targets(self, targets) -> None:
        self.targets = tuple(targets)
        self.update()

    def toggle_move_mode(self) -> bool:
        self.move_mode = not self.move_mode
        self.setWindowFlag(Qt.WindowTransparentForInput, not self.move_mode)
        self.show()
        return self.move_mode

    def mousePressEvent(self, event) -> None:
        if self.move_mode and event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.move_mode and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            if self.move_mode:
                painter.setPen(QPen(self.esp_color, 2, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

            painter.setPen(QPen(QColor(200, 200, 200, 180), 2))
            painter.setBrush(QtGui.QBrush(QColor(50, 50, 50, 150)))
            painter.drawEllipse(self.center, self.radius, self.radius)

            painter.save()
            painter.translate(self.center)
            painter.rotate(-self.player_angle)
            self._draw_cone(painter)
            self._draw_targets(painter)
            painter.restore()

            self._draw_player(painter)
        finally:
            painter.end()

    def _draw_cone(self, painter: QPainter) -> None:
        color = QColor(self.esp_color)
        color.setAlpha(30)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        # Qt pie angles are in 1/16th degrees, measured counter-clockwise.
        painter.drawPie(
            QRect(-self.radius, -self.radius, self.radius * 2, self.radius * 2),
            int((90 - self._cone_angle / 2) * 16),
            int(self._cone_angle * 16),
        )

    def _draw_targets(self, painter: QPainter) -> None:
        painter.setBrush(self.esp_color)
        painter.setPen(Qt.NoPen)
        half_cone = self._cone_angle / 2

        for target in self.targets:
            if abs(target.angle) > half_cone:
                continue
            # Bigger blobs read as closer, so they sit nearer the centre.
            ratio = max(0.1, min(1.0 - (target.size / 100.0), 0.9))
            angle = math.radians(target.angle)
            painter.drawEllipse(
                QPointF(
                    math.sin(angle) * self.radius * ratio,
                    -math.cos(angle) * self.radius * ratio,
                ),
                self.TARGET_DOT_RADIUS,
                self.TARGET_DOT_RADIUS,
            )

    def _draw_player(self, painter: QPainter) -> None:
        painter.setBrush(self.esp_color)
        painter.setPen(Qt.NoPen)
        size = self.PLAYER_ARROW_SIZE
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(self.center.x(), self.center.y() - size),
                    QPointF(self.center.x() + size // 2, self.center.y() + size // 2),
                    QPointF(self.center.x() - size // 2, self.center.y() + size // 2),
                ]
            )
        )
