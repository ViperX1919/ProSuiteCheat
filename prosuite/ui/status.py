"""The status strip along the bottom of the window.

A client-style readout: a pulsing health dot, frame rate and frame time, what
is being captured and how camera motion is being measured. Numbers glide to
their new value instead of snapping, and updates are throttled so a 144 Hz
result stream doesn't turn into 144 label repaints a second.
"""

from __future__ import annotations

from PySide6 import QtCore
from PySide6.QtCore import QPointF, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..constants import Palette
from ..core.detection import FrameStats

GOOD, WARN, BAD, IDLE = "#3DDC84", "#F5B73D", "#F0524F", "#6B6B6B"

#: How often the readout refreshes, in milliseconds.
REFRESH_MS = 80
#: Fraction of the remaining distance a displayed number covers per refresh.
EASE = 0.35

EGO_TEXT = {"img": "scene", "cnt": "counts", "none": "none"}


def health_colour(fps: float, scanning: bool, target: float = 144.0) -> str:
    """Traffic-light colour for the loop's health."""
    if not scanning:
        return IDLE
    ratio = fps / target
    if ratio >= 0.6:
        return GOOD
    if ratio >= 0.3:
        return WARN
    return BAD


class _Dot(QWidget):
    """A small status light that breathes while the worker is scanning."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._colour = QColor(IDLE)
        self._glow = 1.0
        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(0.35)
        self._pulse.setKeyValueAt(0.5, 1.0)
        self._pulse.setEndValue(0.35)
        self._pulse.setDuration(1400)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)

    def _on_pulse(self, value) -> None:
        self._glow = float(value)
        self.update()

    def set_state(self, colour: str, pulsing: bool) -> None:
        self._colour = QColor(colour)
        if pulsing and self._pulse.state() != QVariantAnimation.Running:
            self._pulse.start()
        elif not pulsing:
            self._pulse.stop()
            self._glow = 1.0
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            centre = QPointF(self.width() / 2, self.height() / 2)
            halo = QColor(self._colour)
            halo.setAlphaF(0.35 * self._glow)
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(centre, 6.5, 6.5)
            painter.setBrush(self._colour)
            painter.drawEllipse(centre, 3.5, 3.5)
        finally:
            painter.end()


class StatusStrip(QWidget):
    HEIGHT = 36

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        # A plain QWidget subclass ignores its own stylesheet background
        # unless asked to honour it.
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(14)

        self.dot = _Dot()
        self.state_label = QLabel("IDLE")
        self.fps_label = QLabel("-- FPS")
        self.ms_label = QLabel("-- ms")
        self.source_label = QLabel("Screen")
        self.ego_label = QLabel("")
        self._labels = [
            self.state_label,
            self.fps_label,
            self.ms_label,
            self.source_label,
            self.ego_label,
        ]

        layout.addWidget(self.dot)
        layout.addWidget(self.state_label)
        layout.addWidget(self._divider())
        layout.addWidget(self.fps_label)
        layout.addWidget(self.ms_label)
        layout.addWidget(self._divider())
        layout.addWidget(self.source_label, 1)
        layout.addWidget(self.ego_label)

        self._stats: FrameStats | None = None
        self._show_ego = False
        self._fps = 0.0
        self._ms = 0.0

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self.apply_theme("#111111", "#FFFFFF", QColor("#881122"))

    def _divider(self) -> QWidget:
        line = QWidget()
        line.setFixedSize(1, 14)
        if not hasattr(self, "_dividers"):
            self._dividers = []
        self._dividers.append(line)
        return line

    # --- data in ---
    def set_stats(self, stats: FrameStats | None, show_ego: bool) -> None:
        """Cheap: just remember the latest. The timer does the drawing."""
        self._stats = stats
        self._show_ego = show_ego

    def _refresh(self) -> None:
        stats = self._stats
        if stats is None:
            return

        self._fps += (stats.fps - self._fps) * EASE
        self._ms += (stats.frame_ms - self._ms) * EASE

        scanning = stats.scanning
        self.dot.set_state(health_colour(self._fps, scanning), scanning)
        self.state_label.setText("SCANNING" if scanning else "IDLE")
        self.fps_label.setText(f"{self._fps:.0f} FPS")
        self.ms_label.setText(f"{self._ms:.1f} ms")

        source = stats.source
        self.source_label.setText(
            "Capture: screen" if source == "Screen" else f"Capture: {source}"
        )
        self.source_label.setToolTip(source)

        if self._show_ego and scanning:
            ego = f"Ego: {EGO_TEXT.get(stats.ego, stats.ego)}"
            if stats.lag:
                ego += f"  |  lag {stats.lag}f"
            self.ego_label.setText(ego)
        else:
            self.ego_label.setText("")

    # --- look ---
    def apply_theme(self, background: str, text: str, accent: QColor) -> None:
        self.setStyleSheet(
            f"StatusStrip{{background:{background};border-radius:12px;}}"
        )
        muted = QColor(text)
        muted.setAlphaF(0.72)
        rgba = f"rgba({muted.red()},{muted.green()},{muted.blue()},{muted.alphaF():.2f})"
        for label in self._labels:
            label.setStyleSheet(
                f"color:{rgba};font-size:11px;background:transparent;"
            )
        self.state_label.setStyleSheet(
            f"color:{accent.name()};font-size:11px;font-weight:bold;"
            "background:transparent;letter-spacing:1px;"
        )
        rule = QColor(text)
        rule.setAlphaF(0.18)
        for line in self._dividers:
            line.setStyleSheet(
                f"background:rgba({rule.red()},{rule.green()},{rule.blue()},0.18);"
            )
