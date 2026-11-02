"""Readout of the practice-session statistics, with a reset button."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QWidget

from ..constants import keybind_button_style
from ..core.session import SessionSummary

ROWS = (
    ("scan", "Time scanning"),
    ("targets", "Targets seen"),
    ("locks", "Locked on"),
    ("lock_time", "Avg / best time to lock"),
    ("on_target", "Time on target"),
    ("shots", "Triggerbot shots"),
)


def fmt_seconds(value: float) -> str:
    if value >= 3600:
        return f"{int(value // 3600)}h {int(value % 3600 // 60)}m"
    if value >= 60:
        return f"{int(value // 60)}m {int(value % 60):02d}s"
    return f"{value:.0f}s"


def format_summary(summary: SessionSummary) -> dict[str, str]:
    """Text for each row. ``-`` stands for "nothing measured yet"."""
    dash = "-"
    if summary.mean_lock_s is None:
        lock = dash
    else:
        lock = f"{summary.mean_lock_s * 1000:.0f} ms / {summary.best_lock_s * 1000:.0f} ms"
    return {
        "scan": fmt_seconds(summary.scan_seconds),
        "targets": str(summary.targets),
        "locks": str(summary.locks),
        "lock_time": lock,
        "on_target": dash if summary.on_target is None else f"{summary.on_target * 100:.0f}%",
        "shots": str(summary.shots),
    }


class SessionPanel(QWidget):
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        self._names: list[QLabel] = []
        self.values: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate(ROWS):
            name = QLabel(caption)
            value = QLabel("-")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._names.append(name)
            self.values[key] = value
        grid.setColumnStretch(0, 1)

        self.note = QLabel("Needs Smooth Aim or ESP on, so a target is being tracked.")
        self.note.setWordWrap(True)
        grid.addWidget(self.note, len(ROWS), 0, 1, 2)

        self.reset_button = QPushButton("Reset Session")
        self.reset_button.setStyleSheet(keybind_button_style())
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.clicked.connect(self.reset_requested)
        grid.addWidget(self.reset_button, len(ROWS) + 1, 0, 1, 2)

    def set_summary(self, summary: SessionSummary) -> None:
        for key, text in format_summary(summary).items():
            label = self.values[key]
            if label.text() != text:
                label.setText(text)

    def apply_theme(self, text: str, accent: QColor) -> None:
        muted = QColor(text)
        muted.setAlphaF(0.6)
        rgba = f"rgba({muted.red()},{muted.green()},{muted.blue()},{muted.alphaF():.2f})"
        for name in self._names:
            name.setStyleSheet(f"color:{text};")
        for value in self.values.values():
            value.setStyleSheet(f"color:{accent.name()};font-weight:bold;")
        self.note.setStyleSheet(f"color:{rgba};font-size:11px;")
