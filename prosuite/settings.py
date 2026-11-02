"""Save/load of the full UI state as JSON.

The key names and value formats match the original implementation, so configs
written by older builds still load.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from PySide6.QtCore import QObject, QTimer
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
        "sat_floor": window.sat_floor_slider.value(),
        "val_floor": window.val_floor_slider.value(),
        "shade_tolerance": window.shade_slider.value(),
        "noise_filter": window.noise_slider.value(),
        "max_size": window.max_size_slider.value(),
        "confirm_frames": window.confirm_slider.value(),
        "smoothness": window.smoothness_slider.value(),
        "speed": window.speed_slider.value(),
        "priority": window.priority_dropdown.currentText(),
        "aim_pos": window.aim_position_dropdown.currentText(),
        "y_offset": window.y_offset_slider.value(),
        "prediction_factor": window.prediction_slider.value(),
        "trigger_delay": window.trigger_delay_slider.value(),
        "trigger_reaction": window.trigger_reaction_slider.value(),
        "capture_window": window.capture_window(),
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
        # Crosshair
        "crosshair_color": window.crosshair_overlay.color.name(),
        "crosshair_style": window.crosshair_style_dropdown.currentText(),
        "crosshair_size": window.crosshair_size_slider.value(),
        "crosshair_thickness": window.crosshair_thickness_slider.value(),
        "crosshair_gap": window.crosshair_gap_slider.value(),
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
    window._toasts_muted = True
    try:
        _apply(window, data)
    finally:
        window._toasts_muted = False


def _apply(window, data: dict[str, Any]) -> None:
    window.update_fov_range()
    fov = data.get("fov")
    if isinstance(fov, int):
        window.fov_slider.setValue(
            max(window.fov_slider.minimum(), min(fov, window.fov_slider.maximum()))
        )
    else:
        window.set_default_fov()

    title = data.get("capture_window")
    window.set_capture_window(title if isinstance(title, str) else "")

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
        (window.sat_floor_slider, "sat_floor", 100),
        (window.val_floor_slider, "val_floor", 100),
        (window.shade_slider, "shade_tolerance", 120),
        (window.noise_slider, "noise_filter", 1),
        (window.max_size_slider, "max_size", 50),
        (window.confirm_slider, "confirm_frames", 2),
        (window.smoothness_slider, "smoothness", 50),
        (window.speed_slider, "speed", 10),
        (window.y_offset_slider, "y_offset", 0),
        (window.prediction_slider, "prediction_factor", 5),
        (window.trigger_delay_slider, "trigger_delay", 0),
        (window.trigger_reaction_slider, "trigger_reaction", 0),
        (window.opacity_slider, "opacity", 100),
        (window.arraylist_size_slider, "arraylist_size", 14),
        (window.crosshair_size_slider, "crosshair_size", 10),
        (window.crosshair_thickness_slider, "crosshair_thickness", 2),
        (window.crosshair_gap_slider, "crosshair_gap", 4),
    ):
        value = data.get(key, default)
        slider.setValue(value if isinstance(value, int) else default)


def _apply_dropdowns(window, data: dict[str, Any]) -> None:
    for dropdown, key, default, allowed in (
        (window.priority_dropdown, "priority", "Proximity", C.AIM_PRIORITIES),
        (window.aim_position_dropdown, "aim_pos", "Body", C.AIM_POSITIONS),
        (window.esp_mode_dropdown, "esp_mode", "Box", C.ESP_MODES),
        (window.crosshair_style_dropdown, "crosshair_style", "Cross", C.CROSSHAIR_STYLES),
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
    window.set_crosshair_color(_color(data.get("crosshair_color"), "#FFFFFF"))
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
        # Snap rather than animate: every section changes at once here.
        section.set_collapsed(
            bool(collapsed.get(section.objectName(), True)), animate=False
        )


# --- autosave ---
AUTOSAVE_NAME = "autosave.json"
#: Modules that are always off after a launch, whatever was saved.
LAUNCH_OFF = ("aim", "triggerbot")


def autosave_path() -> str:
    return os.path.join(config_dir(), AUTOSAVE_NAME)


def apply_safely(window, data: dict[str, Any]) -> None:
    """Apply settings, except that nothing starts acting on its own.

    Everything comes back as it was except the modules that move the mouse or
    click: loading a saved state must never start clicking or steering.
    """
    apply(window, data)
    window._toasts_muted = True
    try:
        for name in LAUNCH_OFF:
            feature = window.features.get(name)
            if feature is not None and feature.toggle is not None:
                feature.toggle.setChecked(False)
    finally:
        window._toasts_muted = False


def restore(window, path: str | None = None) -> bool:
    """Load the autosave into ``window``. True if something was applied.

    A missing file is normal (first run). A damaged one is set aside as
    ``.bad`` instead of being retried - and overwritten - on every launch.
    """
    path = path or autosave_path()
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("autosave is not a JSON object")
        apply_safely(window, data)
        log.info("Restored settings from %s", path)
        return True
    except Exception:
        log.exception("Could not restore %s; setting it aside", path)
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        return False


class AutoSaver(QObject):
    """Writes the UI state to disk shortly after it changes.

    Polls instead of hooking every widget's signal: one cheap dict compare a
    few times a second is far less fragile than wiring forty controls, and
    new controls are covered the moment they join :func:`collect`.
    """

    INTERVAL_MS = 2500

    def __init__(self, window, path: str | None = None, parent=None):
        super().__init__(parent or window)
        self._window = window
        self._path = path or autosave_path()
        self._last: str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self.save_if_changed)

    def start(self) -> None:
        # Treat the just-restored state as already saved.
        self._last = self._snapshot()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _snapshot(self) -> str:
        return json.dumps(collect(self._window), sort_keys=True)

    def save_if_changed(self) -> bool:
        try:
            snapshot = self._snapshot()
        except Exception:
            log.exception("Could not collect settings for autosave")
            return False
        if snapshot == self._last:
            return False

        temp = self._path + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                handle.write(snapshot)
            os.replace(temp, self._path)  # atomic: never a half-written file
        except OSError:
            log.exception("Autosave to %s failed", self._path)
            return False
        self._last = snapshot
        return True


# --- profiles ---
PROFILE_EXT = ".json"
_PROFILE_BAD = re.compile(r"[^A-Za-z0-9 _.\-]")
#: Windows treats these as devices whatever the extension ("con.json" is the
#: console), so a profile with one of these names would not be a file at all.
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}


def profiles_dir() -> str:
    path = os.path.join(config_dir(), "profiles")
    os.makedirs(path, exist_ok=True)
    return path


def clean_profile_name(name: str) -> str:
    """A safe file stem for ``name``, or "" if nothing usable is left.

    Anything that could steer a path somewhere else - separators, drive
    colons, dot-only names - is removed rather than trusted.
    """
    cleaned = _PROFILE_BAD.sub("", name or "").strip(" .")[:40]
    if cleaned.split(".")[0].strip().upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def profile_path(name: str, directory: str | None = None) -> str:
    stem = clean_profile_name(name)
    if not stem:
        raise ValueError("empty profile name")
    return os.path.join(directory or profiles_dir(), stem + PROFILE_EXT)


def list_profiles(directory: str | None = None) -> list[str]:
    folder = directory or profiles_dir()
    try:
        names = [
            os.path.splitext(f)[0]
            for f in os.listdir(folder)
            if f.lower().endswith(PROFILE_EXT)
        ]
    except OSError:
        return []
    return sorted(names, key=str.lower)


def save_profile(window, name: str, directory: str | None = None) -> str:
    """Write the current state as a profile; returns the stored name."""
    path = profile_path(name, directory)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(collect(window), handle, indent=4)
    os.replace(temp, path)
    return os.path.splitext(os.path.basename(path))[0]


def load_profile(window, name: str, directory: str | None = None) -> bool:
    try:
        path = profile_path(name, directory)
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("profile is not a JSON object")
        apply_safely(window, data)
        return True
    except (OSError, ValueError):
        log.exception("Could not load profile %r", name)
        return False


def delete_profile(name: str, directory: str | None = None) -> bool:
    try:
        os.remove(profile_path(name, directory))
        return True
    except (OSError, ValueError):
        return False


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
