"""On-screen HUD: what the detector sees, and what it has locked.

A small always-on-top window that sits beside the trainer instead of living
inside the settings window. It shows the colour mask with the locked target
boxed, a lock-state header, and a few readouts. Like the radar it is
click-through until "Reposition HUD" is pressed.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout

from .overlays import _OverlayBase
from .preview import MaskPreview

LOCKED, SCANNING, IDLE = "LOCKED", "SCANNING", "IDLE"


def _lead_text(label: str | None) -> str:
    """'Prediction [45ms img]' -> '45ms img'; bare 'Prediction' -> 'on'."""
    if not label:
        return "off"
    return label.replace("Prediction", "").strip(" []") or "on"


class HudWindow(_OverlayBase):
    WIDTH = 270
    PADDING = 12

    #: Draggable overlays opt out of click-through; it is restored in __init__
    #: so the HUD only takes the mouse while it is being repositioned.
    click_through = False

    def __init__(self, accent: QColor | None = None):
        super().__init__()
        self.accent = QColor(accent or "#881122")
        self._panel = QColor("#0B0B0B")
        self._text = QColor("#FFFFFF")
        self.state = IDLE

        self.setFixedWidth(self.WIDTH)
        self.move_mode = False
        self._drag_offset = QPoint()
        self.setWindowFlag(Qt.WindowTransparentForInput, True)

        pad = self.PADDING
        layout = QVBoxLayout(self)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(8)

        self.state_label = QLabel(IDLE)
        self.perf_label = QLabel("")
        header = QGridLayout()
        header.addWidget(self.state_label, 0, 0, Qt.AlignLeft)
        header.addWidget(self.perf_label, 0, 1, Qt.AlignRight)
        layout.addLayout(header)

        self.view = MaskPreview()
        self.view.setMinimumWidth(0)
        layout.addWidget(self.view)

        self.readouts: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        for index, name in enumerate(("Offset", "Match", "Lead", "Motion", "Shots")):
            key = QLabel(name)
            value = QLabel("-")
            key.setProperty("hudKey", True)
            row, col = divmod(index, 2)
            grid.addWidget(key, row * 2, col)
            grid.addWidget(value, row * 2 + 1, col)
            self.readouts[name] = value
        layout.addLayout(grid)
        self._restyle()

    # --- feeding ---
    def update_from(self, result, fov: int, crosshair: tuple[int, int]) -> None:
        """Show one detection frame. ``fov`` is the capture half-size."""
        stats = result.stats
        scanning = bool(stats and stats.scanning)
        self.state = LOCKED if result.has_target else SCANNING if scanning else IDLE

        self.view.set_mask(result.preview)
        box = None
        if result.has_target and result.bbox and fov > 0:
            x, y, w, h = result.bbox
            span = 2.0 * fov
            box = (x / span, y / span, (x + w) / span, (y + h) / span)
        self.view.set_target(box)

        if stats is not None:
            self.perf_label.setText(f"{stats.fps:.0f} FPS  {stats.frame_ms:.1f} ms")
            self.readouts["Motion"].setText(
                f"{stats.ego}" + (f"  lag {stats.lag}" if stats.lag else "")
            )
            self.readouts["Shots"].setText(str(stats.shots))
        if result.has_target and result.screen_pos is not None:
            dx = result.screen_pos[0] - crosshair[0]
            dy = result.screen_pos[1] - crosshair[1]
            self.readouts["Offset"].setText(f"{dx:+d}, {dy:+d} px")
        else:
            self.readouts["Offset"].setText("-")
        self.readouts["Match"].setText(f"{self.view.matched * 100:.2f}%")
        label = result.prediction_label
        self.readouts["Lead"].setText(_lead_text(result.prediction_label))

        self.state_label.setText(self.state)
        self._restyle()
        self.update()

    def clear(self) -> None:
        self.state = IDLE
        self.view.set_mask(None)
        self.view.set_target(None)
        self.state_label.setText(IDLE)
        self.perf_label.setText("")
        for value in self.readouts.values():
            value.setText("-")
        self._restyle()
        self.update()

    # --- look ---
    def apply_theme(self, accent: QColor, panel: str, text: str) -> None:
        self.accent = QColor(accent)
        self._panel = QColor(panel)
        self._text = QColor(text)
        self.view.apply_theme(accent, panel, text)
        self._restyle()
        self.update()

    def _restyle(self) -> None:
        muted = QColor(self._text)
        muted.setAlphaF(0.55)
        colour = self.accent if self.state == LOCKED else muted
        self.state_label.setStyleSheet(
            f"color:{colour.name(QColor.HexArgb)}; font-weight:700; letter-spacing:1px;"
        )
        self.perf_label.setStyleSheet(f"color:{muted.name(QColor.HexArgb)};")
        value_style = f"color:{self._text.name()};"
        key_style = f"color:{muted.name(QColor.HexArgb)}; font-size:10px;"
        for label in self.findChildren(QLabel):
            if label.property("hudKey"):
                label.setStyleSheet(key_style)
        for value in self.readouts.values():
            value.setStyleSheet(value_style)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            body = QColor(self._panel)
            body.setAlphaF(0.88)
            edge = QColor(self.accent)
            edge.setAlphaF(1.0 if self.state == LOCKED else 0.35)
            if self.move_mode:
                edge = QColor(self._text)
            painter.setPen(QPen(edge, 1.5))
            painter.setBrush(body)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        finally:
            painter.end()

    # --- repositioning (same behaviour as the radar) ---
    def toggle_move_mode(self) -> bool:
        self.move_mode = not self.move_mode
        self.setWindowFlag(Qt.WindowTransparentForInput, not self.move_mode)
        self.show()
        self.update()
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
