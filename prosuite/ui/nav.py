"""Left-hand icon navigation strip with an animated selection highlight."""

from __future__ import annotations

import logging

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..constants import nav_list_style
from ..paths import resource_path

log = logging.getLogger(__name__)

#: Page key -> icon filename, in display order.
NAV_ITEMS = (
    ("aim", "Aim.png"),
    ("esp", "Render-ESP.png"),
    ("settings", "Settings.png"),
)


class IconNav(QWidget):
    ITEM_HEIGHT = 130
    WIDTH = 100
    ICON_SIZE = 48
    LOGO_SIZE = 64

    def __init__(self, theme_color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme_color = theme_color
        self.setFixedWidth(self.WIDTH)
        self.current_index = -1
        self._hovered_item: QListWidgetItem | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.selection_background = QWidget(self)
        self.selection_background.setFixedSize(self.WIDTH, self.ITEM_HEIGHT)
        self.selection_background.hide()

        layout.addWidget(self._build_logo())

        self.item_keys = [key for key, _ in NAV_ITEMS]
        self.icons: dict[str, dict[str, QPixmap]] = {}
        self.nav_list = self._build_list()
        layout.addWidget(self.nav_list)
        layout.addStretch()

        self._animation = QPropertyAnimation(self.selection_background, b"pos")
        self._animation.setDuration(250)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

        self._refresh_icons()

    # --- construction ---
    def _build_logo(self) -> QLabel:
        logo = QLabel()
        pixmap = QPixmap(resource_path("Logo.png"))
        if pixmap.isNull():
            log.warning("Logo.png missing; using placeholder")
            pixmap = self._placeholder(self.LOGO_SIZE, "#646464")
        else:
            pixmap = pixmap.scaled(
                self.LOGO_SIZE,
                self.LOGO_SIZE,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        logo.setPixmap(pixmap)
        logo.setAlignment(Qt.AlignCenter)
        logo.setContentsMargins(0, 20, 0, 20)
        return logo

    def _build_list(self) -> QListWidget:
        nav_list = QListWidget()
        nav_list.setFocusPolicy(Qt.NoFocus)
        nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_list.setStyleSheet(nav_list_style())
        nav_list.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        nav_list.setFlow(QListWidget.TopToBottom)
        nav_list.setMovement(QListWidget.Static)
        nav_list.setResizeMode(QListWidget.Adjust)
        nav_list.setUniformItemSizes(True)

        for key, filename in NAV_ITEMS:
            path = resource_path(filename)
            pixmap = QPixmap(path)
            if pixmap.isNull():
                log.warning("Could not load icon: %s", path)
                pixmap = self._placeholder(self.ICON_SIZE, "#505050")
            self.icons[key] = {
                "normal": pixmap,
                "active": self._colorize(pixmap, self.theme_color.name()),
            }

            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setAlignment(Qt.AlignCenter)
            icon_label = QLabel()
            icon_label.setPixmap(pixmap)
            item_layout.addWidget(icon_label)

            item = QListWidgetItem()
            item.setSizeHint(QSize(self.WIDTH, self.ITEM_HEIGHT))
            nav_list.addItem(item)
            nav_list.setItemWidget(item, item_widget)

        nav_list.setFixedHeight(self.ITEM_HEIGHT * len(NAV_ITEMS))
        nav_list.setMouseTracking(True)
        nav_list.itemEntered.connect(self._on_item_entered)
        nav_list.viewport().installEventFilter(self)
        return nav_list

    @staticmethod
    def _placeholder(size: int, color: str) -> QPixmap:
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(color))
        return pixmap

    @staticmethod
    def _colorize(pixmap: QPixmap, color: str) -> QPixmap:
        """Recolour a pixmap's opaque pixels, preserving its alpha mask."""
        if pixmap.isNull():
            return QPixmap()
        tinted = pixmap.copy()
        painter = QPainter(tinted)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(color))
        painter.end()
        return tinted

    # --- state ---
    def _refresh_icons(self) -> None:
        hover_row = (
            self.nav_list.row(self._hovered_item) if self._hovered_item else -1
        )
        for row, key in enumerate(self.item_keys):
            item_widget = self.nav_list.itemWidget(self.nav_list.item(row))
            if item_widget is None:
                continue
            label = item_widget.findChild(QLabel)
            highlighted = row in (self.current_index, hover_row)
            label.setPixmap(self.icons[key]["active" if highlighted else "normal"])

    def _on_item_entered(self, item: QListWidgetItem) -> None:
        if self._hovered_item is not item:
            self._hovered_item = item
            self._refresh_icons()

    def eventFilter(self, source, event) -> bool:
        if source is self.nav_list.viewport() and event.type() == QEvent.Leave:
            self._hovered_item = None
            self._refresh_icons()
        return super().eventFilter(source, event)

    def update_selection(self, row: int) -> None:
        first_run = self.selection_background.isHidden()
        self.current_index = row
        self._hovered_item = None
        self._refresh_icons()

        item = self.nav_list.item(row)
        if item is None:
            return

        target = self.nav_list.mapTo(self, self.nav_list.visualItemRect(item).topLeft())

        if self._animation.state() == QPropertyAnimation.Running:
            self._animation.stop()

        if first_run:
            self.selection_background.move(target)
        else:
            self._animation.setStartValue(self.selection_background.pos())
            self._animation.setEndValue(target)
            self._animation.start()

        self.selection_background.show()
        self.nav_list.raise_()

    def apply_theme(
        self, sidebar_color: str, selection_color: str, theme_color: QColor
    ) -> None:
        self.setStyleSheet(f"background:{sidebar_color};")
        self.selection_background.setStyleSheet(
            f"background: {selection_color}; border-radius: 8px;"
        )
        self.theme_color = theme_color
        for key in self.item_keys:
            self.icons[key]["active"] = self._colorize(
                self.icons[key]["normal"], theme_color.name()
            )
        self._refresh_icons()
