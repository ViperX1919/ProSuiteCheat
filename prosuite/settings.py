"""Save/load of the full UI state as JSON.

The key names and value formats match the original implementation, so configs
written by older builds still load.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog

from . import constants as C

from .paths import config_dir

log = logging.getLogger(__name__)

JSON_FILTER = "JSON Files (*.json)"


def collect(window) -> dict[str, Any]:
    """Snapshot the window's state into a JSON-serialisable dict."""
    return {
        # Aim
        "fov": window.fov_slider.value(),
        "detection_color": window.detection_color.name(),
        "prediction_color": window.prediction_color.name(),
        "tolerance": window.tolerance_slider.value(),
        "smoothness": window.smoothness_slider.value(),
        "speed": window.speed_slider.value(),
        "priority": window.priority_dropdown.currentText(),
        "aim_pos": window.aim_position_dropdown.currentText(),
        "y_offset": window.y_offset_slider.value(),
        "prediction_factor": window.prediction_slider.value(),
        "trigger_delay": window.trigger_delay_slider.value(),
        # ESP
        "esp_color": window.esp_color.name(),
        "opacity": window.opacity_slider.value(),
        "esp_mode": window.esp_mode_dropdown.currentText(),
        # Arraylist
        "arraylist_enabled": window.arraylist_toggle.isChecked(),
        "arraylist_color": window.arraylist_color.rgba(),
        "arraylist_size": window.arraylist_size_slider.value(),
        "arraylist_pos": [window.array_list.x(), window.array_list.y()],
        "arraylist_style": window.arraylist_style_dropdown.currentText(),
        # Radar
        "radar_pos": [window.radar_widget.x(), window.radar_widget.y()],
        "radar_color": window.radar_widget.esp_color.name(),
        # UI
        "ui_theme_color": window.theme_color.name(),
        "ui_theme": window.theme_dropdown.currentText(),
        "window_pos": [window.x(), window.y()],
        "window_size": [window.width(), window.height()],
        # Feature state
        "toggles": {
            name: feature.toggle.isChecked()
            for name, feature in window.features.items()
            if feature.toggle is not None
        },
        "keybinds": dict(window.input_listener.keybinds),
        "sections_collapsed": {
            section.objectName(): section.is_collapsed() for section in window.sections
        },
    }


def _color(value: Any, fallback: str) -> QColor:
    """Accept either a ``#rrggbb`` string or a packed ARGB integer."""
    if isinstance(value, int):
        return QColor.fromRgba(value)
    if isinstance(value, str) and QColor(value).isValid():
        return QColor(value)
    return QColor(fallback)


def _point(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def apply(window, data: dict[str, Any]) -> None:
    """Push a settings dict back into the window."""
    # FOV depends on the current resolution, so range first, then value.
    window.update_fov_range()
    fov = data.get("fov")
    if isinstance(fov, int):
        window.fov_slider.setValue(
            max(window.fov_slider.minimum(), min(fov, window.fov_slider.maximum()))
        )
    else:
        window.set_default_fov()

    _apply_sliders(window, data)
    _apply_dropdowns(window, data)
    _apply_colors(window, data)
    _apply_geometry(window, data)
    _apply_features(window, data)

    window.apply_theme(window.theme_dropdown.currentText())
    window.sync_radar_cone()
    window.push_settings()


def _apply_sliders(window, data: dict[str, Any]) -> None:
    for slider, key, default in (
        (window.tolerance_slider, "tolerance", 80),
        (window.smoothness_slider, "smoothness", 50),
        (window.speed_slider, "speed", 10),
        (window.y_offset_slider, "y_offset", 0),
        (window.prediction_slider, "prediction_factor", 5),
        (window.trigger_delay_slider, "trigger_delay", 0),
        (window.opacity_slider, "opacity", 100),
        (window.arraylist_size_slider, "arraylist_size", 14),
    ):
        value = data.get(key, default)
        slider.setValue(value if isinstance(value, int) else default)


def _apply_dropdowns(window, data: dict[str, Any]) -> None:
    for dropdown, key, default, allowed in (
        (window.priority_dropdown, "priority", "Proximity", C.AIM_PRIORITIES),
        (window.aim_position_dropdown, "aim_pos", "Body", C.AIM_POSITIONS),
        (window.esp_mode_dropdown, "esp_mode", "Box", C.ESP_MODES),
        (
            window.arraylist_style_dropdown,
            "arraylist_style",
            "Default",
            C.ARRAYLIST_STYLES,
        ),
        (window.theme_dropdown, "ui_theme", C.DEFAULT_THEME, C.THEME_NAMES),
    ):
        value = data.get(key, default)
        dropdown.setCurrentText(value if value in allowed else default)


def _apply_colors(window, data: dict[str, Any]) -> None:
    window.set_detection_color(_color(data.get("detection_color"), "#FF0000"))
    window.set_prediction_color(_color(data.get("prediction_color"), "#00FFFF"))
    window.set_esp_color(_color(data.get("esp_color"), C.ACCENT_RED))
    window.set_radar_color(_color(data.get("radar_color"), C.ACCENT_RED))
    window.set_arraylist_color(_color(data.get("arraylist_color"), C.ACCENT_RED))
    window.theme_color = _color(data.get("ui_theme_color"), C.ACCENT_RED)


def _apply_geometry(window, data: dict[str, Any]) -> None:
    if (pos := _point(data.get("arraylist_pos"))) is not None:
        window.array_list.move(*pos)
    if (pos := _point(data.get("radar_pos"))) is not None:
        window.radar_widget.move(*pos)
    if (pos := _point(data.get("window_pos"))) is not None:
        window.move(*pos)
    if (size := _point(data.get("window_size"))) is not None:
        window.resize(*size)


def _apply_features(window, data: dict[str, Any]) -> None:
    toggles = data.get("toggles") or {}
    for name, state in toggles.items():
        feature = window.features.get(name)
        if feature is not None and feature.toggle is not None:
            feature.toggle.setChecked(bool(state))

    keybinds = data.get("keybinds") or {}
    window.input_listener.keybinds = dict(keybinds)
    for name, feature in window.features.items():
        if feature.keybind is not None:
            # Unbound buttons keep their "Set ... Key" prompt rather than
            # showing a bare "Not Set".
            feature.keybind.setText(keybinds.get(name) or feature.keybind_prompt)

    collapsed = data.get("sections_collapsed") or {}
    for section in window.sections:
        section.set_collapsed(bool(collapsed.get(section.objectName(), True)))


# --- dialogs ---
def save_dialog(window) -> None:
    path, _ = QFileDialog.getSaveFileName(
        window, "Save Config", config_dir(), JSON_FILTER
    )
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(collect(window), handle, indent=4)
        log.info("Settings saved to %s", path)
    except OSError:
        log.exception("Could not save settings to %s", path)


def load_dialog(window) -> None:
    path, _ = QFileDialog.getOpenFileName(
        window, "Load Config", config_dir(), JSON_FILTER
    )
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read settings from %s", path)
        return

    if not isinstance(data, dict):
        log.error("Config %s is not a JSON object", path)
        return

    try:
        apply(window, data)
        log.info("Settings loaded from %s", path)
    except Exception:
        log.exception("Failed to apply settings from %s", path)
        window.apply_theme(window.theme_dropdown.currentText())
