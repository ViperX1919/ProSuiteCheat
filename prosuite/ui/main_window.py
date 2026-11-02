"""The application window: builds the menu pages and wires everything together."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6 import QtCore, QtGui
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from .. import constants as C
from ..core import capture
from ..core.aimer import SmoothAimer
from ..core.detection import DetectionWorker
from ..core.input_listener import InputListener
from ..paths import resource_path
from . import widgets as W
from .arraylist import ArrayListWidget
from .nav import IconNav
from .overlays import FOVOverlay, PredictionAimOverlay, RadarWidget, RenderOverlay
from .sections import CheatMenuPage, CollapsibleSection, make_separator
from .widgets import AnimatedStackedWidget, Control, ToggleSwitch

log = logging.getLogger(__name__)

#: Hotkeys are either held down or press-to-toggle.
HOLD = "hold"
TOGGLE = "toggle"

MIN_FOV = 50
#: The FOV ceiling and default, as fractions of the smaller screen dimension.
MAX_FOV_DIVISOR = 2
DEFAULT_FOV_DIVISOR = 6


@dataclass
class Feature:
    """A toggleable capability, its switch, and its optional hotkey."""

    name: str
    display_name: str = ""
    toggle: ToggleSwitch | None = None
    keybind: QPushButton | None = None
    bind_mode: str = TOGGLE
    #: Which feature's switch this hotkey flips, when it isn't its own.
    controls: str | None = None

    @property
    def keybind_prompt(self) -> str:
        """The button caption shown while no key is bound."""
        return "Set Hold Key" if self.bind_mode == HOLD else "Set Toggle Key"

    @property
    def shows_in_arraylist(self) -> bool:
        return bool(self.display_name) and self.toggle is not None

    def is_on(self) -> bool:
        return self.toggle is not None and self.toggle.isChecked()


class MainWindow(QWidget):
    def __init__(self, enable_hotkeys: bool = True):
        """
        Args:
            enable_hotkeys: When False, the global keyboard/mouse listener is
                never started. Everything else works, but keybinds do nothing.
                Useful for isolating faults, since the global low-level input
                hooks share the input path with third-party HID drivers.
        """
        super().__init__()
        self._hotkeys_enabled = enable_hotkeys
        self.setWindowTitle("Pro Suite")
        self.resize(1000, 750)

        icon_path = resource_path("Logo.ico")
        icon = QIcon(icon_path)
        if icon.isNull():
            log.warning("Could not load window icon: %s", icon_path)
        else:
            self.setWindowIcon(icon)

        # --- state ---
        self.theme_color = QColor(C.ACCENT_RED)
        self.detection_color = QColor(255, 0, 0)
        self.prediction_color = QColor(0, 255, 255)
        self.esp_color = QColor(self.theme_color)
        self.arraylist_color = QColor(self.theme_color)

        self.features: dict[str, Feature] = {}
        self.controls: list[Control] = []
        self.pages: dict[str, CheatMenuPage] = {}
        self.sections: list[CollapsibleSection] = []
        self.key_states: dict[str, bool] = {}
        self._overlays_shown = False

        self._refresh_screen_info()

        # --- overlays ---
        self.fov_overlay = FOVOverlay(self.esp_color)
        self.render_overlay = RenderOverlay()
        self.prediction_overlay = PredictionAimOverlay(self.prediction_color)
        self.radar_widget = RadarWidget(esp_color=self.esp_color)
        self.array_list = ArrayListWidget()
        self.radar_widget.hide()

        # --- workers ---
        self.aimer = SmoothAimer(smoothness=50, speed=10)
        self.worker = DetectionWorker(self.aimer, parent=self)
        self.input_listener = InputListener(parent=self)

        # --- layout ---
        self.nav = IconNav(self.theme_color)
        self.stack = AnimatedStackedWidget()

        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self._build_pages()
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)

        self._connect()
        self.apply_theme(C.DEFAULT_THEME)

        if self._hotkeys_enabled:
            self.input_listener.start()
        else:
            log.warning("Hotkeys disabled: no global input hooks installed")

        # Windows can be dragged at any time; keep the detector's idea of
        # where they are fresh without re-pushing every setting each tick.
        self._rect_timer = QtCore.QTimer(self)
        self._rect_timer.setInterval(300)
        self._rect_timer.timeout.connect(self._sync_own_rects)
        self._rect_timer.start()

        self.worker.result_ready.connect(self._on_result, Qt.QueuedConnection)
        self.worker.start()

        QtCore.QTimer.singleShot(0, lambda: self.nav.nav_list.setCurrentRow(0))
        QtCore.QTimer.singleShot(0, self._reset_fov_for_screen)
        QtCore.QTimer.singleShot(0, self.push_settings)

        app = QApplication.instance()
        if app is not None:
            app.primaryScreenChanged.connect(self._on_screen_changed)
            # Guarantees the capture thread is joined even if the app exits
            # without the window receiving a close event.
            app.aboutToQuit.connect(self.shutdown)

    # ------------------------------------------------------------------
    # Screen geometry
    # ------------------------------------------------------------------
    def _refresh_screen_info(self) -> None:
        geometry = QtGui.QGuiApplication.primaryScreen().geometry()
        self.screen_geometry = geometry
        self.screen_center = geometry.center()
        self.screen_width = geometry.width()
        self.screen_height = geometry.height()

    def _reset_fov_for_screen(self) -> None:
        self.update_fov_range()
        self.set_default_fov()
        self.sync_radar_cone()

    def update_fov_range(self) -> None:
        """Clamp the FOV slider to what the current resolution can support."""
        self._refresh_screen_info()
        maximum = min(self.screen_width, self.screen_height) // MAX_FOV_DIVISOR
        self.fov_slider.setRange(MIN_FOV, max(MIN_FOV, maximum))

    def set_default_fov(self) -> None:
        default = min(self.screen_width, self.screen_height) // DEFAULT_FOV_DIVISOR
        self.fov_slider.setValue(
            max(self.fov_slider.minimum(), min(default, self.fov_slider.maximum()))
        )

    def _on_screen_changed(self) -> None:
        self._reset_fov_for_screen()
        for overlay in (self.fov_overlay, self.render_overlay, self.prediction_overlay):
            if overlay.isVisible():
                overlay.update()
        log.info("Screen resolution changed to %dx%d", self.screen_width, self.screen_height)

    # ------------------------------------------------------------------
    # Page construction
    # ------------------------------------------------------------------
    def _register(self, control: Control) -> Control:
        """Track a control so the theming pass can restyle it."""
        if control.kind == W.SLIDER:
            control.input.setStyleSheet(C.slider_style(self.theme_color.name()))
        self.controls.append(control)
        return control

    def _add_feature(self, feature: Feature) -> Feature:
        self.features[feature.name] = feature
        return feature

    def _build_pages(self) -> None:
        self._build_aim_page()
        self._build_visuals_page()
        self._build_settings_page()

    def _build_aim_page(self) -> None:
        page = CheatMenuPage("Aimbot")
        self.pages["aim"] = page

        aim_toggle = self._register(W.make_toggle("Enable Smooth Aim", self.theme_color))
        aim_hold = self._register(W.make_keybind_button("Set Hold Key"))
        self._add_feature(
            Feature(
                name="aim",
                display_name="Smooth Aim",
                toggle=aim_toggle.input,
                keybind=aim_hold.input,
                bind_mode=HOLD,
            )
        )

        section = CollapsibleSection(aim_toggle.widget, header_controls=[aim_hold.widget])

        aim_switch = self._register(W.make_keybind_button("Set Toggle Key"))
        self._add_feature(
            Feature(
                name="aim_toggle",
                keybind=aim_switch.input,
                bind_mode=TOGGLE,
                controls="aim",
            )
        )

        det_color = self._register(W.make_color_picker("Detection Color"))
        self.detection_color_button = det_color.input

        priority = self._register(W.make_dropdown("Aim Priority", C.AIM_PRIORITIES))
        self.priority_dropdown = priority.input

        aim_pos = self._register(W.make_dropdown("Aiming Position", C.AIM_POSITIONS))
        self.aim_position_dropdown = aim_pos.input

        y_offset = self._register(
            W.make_slider("Y Offset", -50, 50, 0, value_format=W.signed)
        )
        self.y_offset_widget = y_offset.widget
        self.y_offset_slider = y_offset.input
        self.y_offset_widget.hide()

        tolerance = self._register(W.make_slider("Color Tolerance", 0, 255, 80))
        self.tolerance_slider = tolerance.input

        smoothness = self._register(W.make_slider("Smoothness", 1, 100, 50))
        self.smoothness_slider = smoothness.input

        speed = self._register(W.make_slider("Speed", 1, 100, 10))
        self.speed_slider = speed.input

        predict = self._register(W.make_toggle("Prediction Aim", self.theme_color))
        self._add_feature(
            Feature(name="prediction", display_name="Prediction", toggle=predict.input)
        )

        predict_factor = self._register(
            W.make_slider("Prediction Lead", 1, 50, 5, value_format=W.tens_of_ms)
        )
        self.prediction_slider = predict_factor.input

        predict_visual = self._register(
            W.make_toggle("Show Prediction Aim Visual", self.theme_color)
        )
        self._add_feature(
            Feature(
                name="prediction_visual",
                display_name="Prediction Visual",
                toggle=predict_visual.input,
            )
        )

        pred_color = self._register(
            W.make_color_picker("Prediction Color", self.prediction_color.name())
        )
        self.prediction_color_button = pred_color.input

        for control in (
            aim_switch,
            det_color,
            priority,
            aim_pos,
            y_offset,
            tolerance,
            smoothness,
            speed,
        ):
            section.add_widget(control.widget)
        section.add_widget(make_separator())
        for control in (predict, predict_factor, predict_visual, pred_color):
            section.add_widget(control.widget)

        page.add_section(section)
        self.sections.append(section)
        page.add_separator()

        trigger_toggle = self._register(
            W.make_toggle("Enable Triggerbot", self.theme_color)
        )
        trigger_key = self._register(W.make_keybind_button("Set Toggle Key"))
        self._add_feature(
            Feature(
                name="triggerbot",
                display_name="Triggerbot",
                toggle=trigger_toggle.input,
                keybind=trigger_key.input,
            )
        )

        trigger_section = CollapsibleSection(
            trigger_toggle.widget, header_controls=[trigger_key.widget]
        )
        trigger_reaction = self._register(
            W.make_slider("Reaction Delay (ms)", 0, 500, 0)
        )
        self.trigger_reaction_slider = trigger_reaction.input
        trigger_section.add_widget(trigger_reaction.widget)
        trigger_delay = self._register(
            W.make_slider("Repeat Interval (ms)", 0, 1000, 0)
        )
        self.trigger_delay_slider = trigger_delay.input
        trigger_section.add_widget(trigger_delay.widget)
        page.add_section(trigger_section)
        self.sections.append(trigger_section)

        self.stack.addWidget(page)

    def _build_visuals_page(self) -> None:
        page = CheatMenuPage("Visuals")
        self.pages["esp"] = page

        # --- ESP ---
        esp_toggle = self._register(W.make_toggle("Enable ESP", self.theme_color))
        esp_key = self._register(W.make_keybind_button("Set Toggle Key"))
        self._add_feature(
            Feature(
                name="esp",
                display_name="ESP",
                toggle=esp_toggle.input,
                keybind=esp_key.input,
            )
        )
        esp_section = CollapsibleSection(
            esp_toggle.widget, header_controls=[esp_key.widget]
        )

        esp_color = self._register(
            W.make_color_picker("ESP Color", self.esp_color.name())
        )
        self.esp_color_button = esp_color.input

        glow = self._register(W.make_toggle("Enable Glow", self.theme_color))
        self._add_feature(Feature(name="glow", display_name="Glow", toggle=glow.input))

        tracers = self._register(W.make_toggle("Enable Tracers", self.theme_color))
        self._add_feature(
            Feature(name="tracers", display_name="Tracers", toggle=tracers.input)
        )

        opacity = self._register(W.make_slider("ESP Opacity", 10, 255, 100))
        self.opacity_slider = opacity.input

        esp_mode = self._register(W.make_dropdown("ESP Mode", C.ESP_MODES))
        self.esp_mode_dropdown = esp_mode.input

        for control in (esp_color, glow, tracers, opacity, esp_mode):
            esp_section.add_widget(control.widget)
        page.add_section(esp_section)
        self.sections.append(esp_section)
        page.add_separator()

        # --- Radar ---
        radar_toggle = self._register(W.make_toggle("Enable Radar", self.theme_color))
        self._add_feature(
            Feature(name="radar", display_name="Radar", toggle=radar_toggle.input)
        )
        radar_section = CollapsibleSection(radar_toggle.widget)

        radar_color = self._register(
            W.make_color_picker("Radar Color", self.esp_color.name())
        )
        self.radar_color_button = radar_color.input
        self.radar_reposition_button = QPushButton("Reposition Radar")
        radar_section.add_widget(radar_color.widget)
        radar_section.add_widget(self.radar_reposition_button)
        page.add_section(radar_section)
        self.sections.append(radar_section)
        page.add_separator()

        # --- FOV ---
        fov_toggle = self._register(W.make_toggle("Enable FOV", self.theme_color))
        # The internal name stays "show_fov" - it is the key saved configs use.
        self._add_feature(
            Feature(name="show_fov", display_name="FOV", toggle=fov_toggle.input)
        )
        fov_section = CollapsibleSection(fov_toggle.widget)
        fov = self._register(W.make_slider("FOV Radius", MIN_FOV, 1000, 200))
        self.fov_slider = fov.input
        self.update_fov_range()
        fov_section.add_widget(fov.widget)
        page.add_section(fov_section)
        self.sections.append(fov_section)
        page.add_separator()

        # --- Arraylist ---
        arraylist_toggle = self._register(
            W.make_toggle("Enable Arraylist", self.theme_color)
        )
        self.arraylist_toggle = arraylist_toggle.input
        self._add_feature(
            Feature(
                name="arraylist",
                display_name="Arraylist",
                toggle=arraylist_toggle.input,
            )
        )
        arraylist_section = CollapsibleSection(arraylist_toggle.widget)

        al_color = self._register(
            W.make_color_picker("Arraylist Color", self.arraylist_color.name())
        )
        self.arraylist_color_button = al_color.input
        al_size = self._register(W.make_slider("Arraylist Size", 8, 32, 14))
        self.arraylist_size_slider = al_size.input
        al_style = self._register(
            W.make_dropdown("Arraylist Style", C.ARRAYLIST_STYLES)
        )
        self.arraylist_style_dropdown = al_style.input
        self.arraylist_reposition_button = QPushButton("Reposition Arraylist")

        for control in (al_color, al_size, al_style):
            arraylist_section.add_widget(control.widget)
        arraylist_section.add_widget(self.arraylist_reposition_button)
        page.add_section(arraylist_section)
        self.sections.append(arraylist_section)

        self.stack.addWidget(page)

    def _build_settings_page(self) -> None:
        page = CheatMenuPage("Settings")
        self.pages["settings"] = page

        self.save_button = QPushButton("Save Settings")
        self.load_button = QPushButton("Load Settings")
        theme_color = self._register(
            W.make_color_picker("UI Theme Color", self.theme_color.name())
        )
        self.theme_color_button = theme_color.input
        theme_select = self._register(W.make_dropdown("UI Theme", C.THEME_NAMES))
        self.theme_dropdown = theme_select.input
        self.reset_ui_button = QPushButton("Reset UI to Default")
        capture_select = self._register(
            W.make_dropdown("Capture Source", [capture.SCREEN_LABEL])
        )
        self.capture_dropdown = capture_select.input
        self.capture_dropdown.setMinimumWidth(220)

        page.add_heading("Capture")
        page.add_widget(capture_select.widget)
        page.add_separator()
        page.add_heading("Config")
        page.add_widget(self.save_button)
        page.add_widget(self.load_button)
        page.add_separator()
        page.add_heading("UI Customization")
        page.add_widget(theme_color.widget)
        page.add_widget(theme_select.widget)
        page.add_widget(self.reset_ui_button)

        self.stack.addWidget(page)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self.nav.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.nav_list.currentRowChanged.connect(self.nav.update_selection)

        self.input_listener.input_pressed.connect(self._on_hotkey)
        self.input_listener.input_set.connect(self._on_hotkey_assigned)

        for feature in self.features.values():
            if feature.shows_in_arraylist:
                feature.toggle.toggled.connect(
                    lambda on, f=feature: self._on_feature_toggled(f, on)
                )
            if feature.keybind is not None:
                feature.keybind.clicked.connect(
                    lambda _=False, name=feature.name: self.input_listener.set_new_bind(name)
                )

        # Aim
        self.features["aim"].toggle.toggled.connect(
            lambda _on: self.worker.reset_prediction()
        )
        self.detection_color_button.clicked.connect(self._pick_detection_color)
        self.prediction_color_button.clicked.connect(self._pick_prediction_color)
        self.aim_position_dropdown.currentTextChanged.connect(
            lambda text: self.y_offset_widget.setVisible(text == "Custom")
        )

        # Visuals
        self.fov_slider.valueChanged.connect(self.fov_overlay.set_fov_radius)
        self.fov_slider.valueChanged.connect(self.sync_radar_cone)
        self.features["show_fov"].toggle.toggled.connect(
            lambda on: self._set_overlay_visible(self.fov_overlay, on)
        )
        self.features["esp"].toggle.toggled.connect(
            lambda on: self._set_overlay_visible(self.render_overlay, on)
        )
        self.esp_mode_dropdown.currentTextChanged.connect(
            lambda mode: setattr(self.render_overlay, "esp_mode", mode)
        )
        self.esp_color_button.clicked.connect(self._pick_esp_color)
        self.features["glow"].toggle.toggled.connect(
            lambda on: setattr(self.render_overlay, "glow", on)
        )
        self.features["tracers"].toggle.toggled.connect(
            lambda on: setattr(self.render_overlay, "tracers", on)
        )
        self.opacity_slider.valueChanged.connect(
            lambda v: setattr(self.render_overlay, "opacity", v)
        )

        # Radar
        self.features["radar"].toggle.toggled.connect(
            lambda on: self.radar_widget.setVisible(on)
        )
        self.radar_color_button.clicked.connect(self._pick_radar_color)
        self.radar_reposition_button.clicked.connect(self._toggle_radar_move)

        # Arraylist
        self.arraylist_toggle.toggled.connect(self._on_arraylist_toggled)
        self.arraylist_color_button.clicked.connect(self._pick_arraylist_color)
        self.arraylist_size_slider.valueChanged.connect(self.array_list.set_font_size)
        self.arraylist_style_dropdown.currentTextChanged.connect(
            self.array_list.set_style
        )
        self.arraylist_reposition_button.clicked.connect(
            self.array_list.toggle_move_mode
        )
        # Driven by the signal rather than the click so the caption stays right
        # when move mode is left with Escape.
        self.array_list.move_mode_changed.connect(self._on_arraylist_move_mode)

        # Settings
        self.theme_color_button.clicked.connect(self._pick_theme_color)
        self.theme_dropdown.currentTextChanged.connect(self.apply_theme)
        self.reset_ui_button.clicked.connect(self._reset_theme)
        self.save_button.clicked.connect(self._save_settings)
        self.load_button.clicked.connect(self._load_settings)

        # Anything the detection thread reads must re-push the snapshot.
        for slider in (
            self.fov_slider,
            self.tolerance_slider,
            self.smoothness_slider,
            self.speed_slider,
            self.y_offset_slider,
            self.prediction_slider,
            self.trigger_delay_slider,
            self.trigger_reaction_slider,
        ):
            slider.valueChanged.connect(self.push_settings)
        for dropdown in (
            self.priority_dropdown,
            self.aim_position_dropdown,
            self.esp_mode_dropdown,
            self.capture_dropdown,
        ):
            dropdown.currentTextChanged.connect(self.push_settings)
        self.capture_dropdown.popupAboutToShow.connect(self.refresh_capture_windows)
        for name in ("aim", "prediction", "triggerbot", "esp", "radar", "show_fov", "tracers"):
            self.features[name].toggle.toggled.connect(self.push_settings)

    # ------------------------------------------------------------------
    # Detection settings
    # ------------------------------------------------------------------
    def push_settings(self) -> None:
        """Send the current UI state to the detection thread."""
        self.worker.update_settings(
            screen_center=(self.screen_center.x(), self.screen_center.y()),
            capture_window=self.capture_window(),
            ignore_rects=self._own_rects(),
            fov_ring=self.features["show_fov"].is_on(),
            esp_tracers=self.features["tracers"].is_on(),
            fov=self.fov_slider.value(),
            detection_rgb=(
                self.detection_color.red(),
                self.detection_color.green(),
                self.detection_color.blue(),
            ),
            tolerance=self.tolerance_slider.value(),
            aim_enabled=self.features["aim"].is_on(),
            aim_key_held=self.key_states.get("aim", False),
            priority=self.priority_dropdown.currentText(),
            aim_position=self.aim_position_dropdown.currentText(),
            y_offset=self.y_offset_slider.value(),
            smoothness=self.smoothness_slider.value(),
            speed=self.speed_slider.value(),
            prediction_enabled=self.features["prediction"].is_on(),
            prediction_factor=self.prediction_slider.value(),
            trigger_enabled=self.features["triggerbot"].is_on(),
            trigger_delay=self.trigger_delay_slider.value(),
            trigger_reaction=self.trigger_reaction_slider.value(),
            esp_enabled=self.features["esp"].is_on(),
            esp_mode=self.esp_mode_dropdown.currentText(),
            radar_enabled=self.features["radar"].is_on(),
        )

    # --- our own windows, for the detector to ignore on a screen grab ---
    def _own_rects(self) -> tuple[tuple[int, int, int, int], ...]:
        """Screen rectangles of our small windows (not the full-screen overlays)."""
        rects = []
        for widget in (self, self.array_list, self.radar_widget):
            if widget.isVisible() and not widget.isMinimized():
                g = widget.frameGeometry()
                rects.append((g.left(), g.top(), g.right(), g.bottom()))
        return tuple(rects)

    def _sync_own_rects(self) -> None:
        if self._own_rects() != self.worker.settings.ignore_rects:
            self.push_settings()

    # --- capture source ---
    def capture_window(self) -> str:
        """Title of the window being captured, or "" for the whole screen."""
        text = self.capture_dropdown.currentText()
        return capture.SCREEN if text == capture.SCREEN_LABEL else text

    def set_capture_window(self, title: str) -> None:
        combo = self.capture_dropdown
        label = title or capture.SCREEN_LABEL
        if combo.findText(label) < 0:
            # A saved window that isn't open right now stays selectable;
            # detection simply waits for it to appear.
            combo.addItem(label)
        combo.setCurrentText(label)

    def refresh_capture_windows(self) -> None:
        """Repopulate the dropdown with the windows open right now."""
        combo = self.capture_dropdown
        current = combo.currentText()
        titles = [title for _hwnd, title in capture.list_windows()]
        if current != capture.SCREEN_LABEL and current not in titles:
            titles.append(current)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems([capture.SCREEN_LABEL, *titles])
        combo.setCurrentText(current)
        combo.blockSignals(False)

    def _on_result(self, result) -> None:
        """Paint the latest detection frame. Runs on the GUI thread."""
        if self.features["esp"].is_on() and result.has_target:
            self.render_overlay.show_target(
                result.bbox, result.offset, result.screen_pos, result.mask
            )
        else:
            self.render_overlay.clear()

        show_prediction = (
            self.features["prediction"].is_on()
            and self.features["prediction_visual"].is_on()
        )
        if show_prediction:
            if not self.prediction_overlay.isVisible():
                self.prediction_overlay.show_overlay()
            self.prediction_overlay.show_lines(result.velocity_lines)
        elif self.prediction_overlay.isVisible():
            self.prediction_overlay.hide()

        if self.features["radar"].is_on():
            self.radar_widget.set_targets(result.radar_targets)

        if result.prediction_label and "prediction" in self.array_list.features:
            self.array_list.update_feature("prediction", result.prediction_label)

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------
    def _on_hotkey(self, name: str, pressed: bool) -> None:
        feature = self.features.get(name)
        if feature is None:
            return

        if feature.bind_mode == HOLD:
            self.key_states[name] = pressed
            self.push_settings()
            return

        if not pressed:
            return

        target = self.features.get(feature.controls or name)
        if target is not None and target.toggle is not None:
            target.toggle.setChecked(not target.toggle.isChecked())

    def _on_hotkey_assigned(self, name: str, label: str) -> None:
        feature = self.features.get(name)
        if feature is not None and feature.keybind is not None:
            feature.keybind.setText(label)
            log.info("Bound %s to %s", name, label)

    def _on_feature_toggled(self, feature: Feature, enabled: bool) -> None:
        if enabled:
            self.array_list.add_feature(feature.name, feature.display_name)
        else:
            self.array_list.remove_feature(feature.name)

    # ------------------------------------------------------------------
    # Overlay helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _set_overlay_visible(overlay, visible: bool) -> None:
        if visible:
            overlay.show_overlay()
        else:
            overlay.hide()

    def sync_radar_cone(self) -> None:
        self.radar_widget.set_cone_angle(
            self.fov_slider.value(),
            self.fov_slider.minimum(),
            self.fov_slider.maximum(),
        )

    def _on_arraylist_toggled(self, enabled: bool) -> None:
        self.array_list.setVisible(enabled)
        if enabled:
            self.array_list.raise_()

    def _toggle_radar_move(self) -> None:
        moving = self.radar_widget.toggle_move_mode()
        self.radar_reposition_button.setText(
            "Stop Repositioning" if moving else "Reposition Radar"
        )

    def _on_arraylist_move_mode(self, moving: bool) -> None:
        self.arraylist_reposition_button.setText(
            "Stop Repositioning" if moving else "Reposition Arraylist"
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._overlays_shown:
            return
        self._overlays_shown = True
        self._set_overlay_visible(self.render_overlay, self.features["esp"].is_on())
        self._set_overlay_visible(self.fov_overlay, self.features["show_fov"].is_on())
        self.array_list.setVisible(self.arraylist_toggle.isChecked())
        self.radar_widget.setVisible(self.features["radar"].is_on())
        self.sync_radar_cone()

    # ------------------------------------------------------------------
    # Colour pickers
    # ------------------------------------------------------------------
    def _choose(self, current: QColor) -> QColor | None:
        color = QColorDialog.getColor(current, self)
        return color if color.isValid() else None

    def set_detection_color(self, color: QColor) -> None:
        self.detection_color = color
        self.detection_color_button.setStyleSheet(C.color_button_style(color.name()))
        self.push_settings()

    def set_prediction_color(self, color: QColor) -> None:
        self.prediction_color = color
        self.prediction_color_button.setStyleSheet(C.color_button_style(color.name()))
        self.prediction_overlay.set_color(color)

    def set_esp_color(self, color: QColor) -> None:
        self.esp_color = color
        self.esp_color_button.setStyleSheet(C.color_button_style(color.name()))
        self.fov_overlay.set_color(color)
        self.render_overlay.color = color

    def set_radar_color(self, color: QColor) -> None:
        self.radar_widget.esp_color = color
        self.radar_color_button.setStyleSheet(C.color_button_style(color.name()))
        self.radar_widget.update()

    def set_arraylist_color(self, color: QColor) -> None:
        self.arraylist_color = color
        self.arraylist_color_button.setStyleSheet(C.color_button_style(color.name()))
        self.array_list.set_text_color(color)

    def _pick_detection_color(self) -> None:
        if (c := self._choose(self.detection_color)) is not None:
            self.set_detection_color(c)

    def _pick_prediction_color(self) -> None:
        if (c := self._choose(self.prediction_color)) is not None:
            self.set_prediction_color(c)

    def _pick_esp_color(self) -> None:
        if (c := self._choose(self.esp_color)) is not None:
            self.set_esp_color(c)

    def _pick_radar_color(self) -> None:
        if (c := self._choose(self.radar_widget.esp_color)) is not None:
            self.set_radar_color(c)

    def _pick_arraylist_color(self) -> None:
        if (c := self._choose(self.arraylist_color)) is not None:
            self.set_arraylist_color(c)

    def _pick_theme_color(self) -> None:
        if (c := self._choose(self.theme_color)) is not None:
            self.theme_color = c
            self.apply_theme(self.theme_dropdown.currentText())

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------
    def _reset_theme(self) -> None:
        self.theme_color = QColor(C.ACCENT_RED)
        self.theme_dropdown.setCurrentText(C.DEFAULT_THEME)
        self.apply_theme(C.DEFAULT_THEME)

    def apply_theme(self, theme_name: str) -> None:
        palette = C.palette_for(theme_name)

        self.setStyleSheet(f"background:{palette.background};")
        self.theme_color_button.setStyleSheet(
            C.color_button_style(self.theme_color.name())
        )
        self.nav.apply_theme(palette.sidebar, palette.selection, self.theme_color)

        for page in self.pages.values():
            page.apply_theme(palette.content_box, palette.text, palette.border)

        text_style = f"color:{palette.text};"
        for control in self.controls:
            if control.kind == W.TOGGLE:
                control.input.theme_color = self.theme_color
                control.input.update()
            elif control.kind == W.SLIDER:
                control.input.setStyleSheet(C.slider_style(self.theme_color.name()))
                if control.value_label is not None:
                    control.value_label.setStyleSheet(text_style)
            if control.label is not None:
                control.label.setStyleSheet(text_style)

        self.array_list.set_text_color(self.arraylist_color)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save_settings(self) -> None:
        from ..settings import save_dialog

        save_dialog(self)

    def _load_settings(self) -> None:
        from ..settings import load_dialog

        load_dialog(self)

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Stop background threads and close overlays. Safe to call twice."""
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True

        log.info("Shutting down")
        self.worker.stop()
        self.input_listener.stop()
        for overlay in (
            self.array_list,
            self.render_overlay,
            self.fov_overlay,
            self.radar_widget,
            self.prediction_overlay,
        ):
            overlay.close()

    def closeEvent(self, event) -> None:
        self.shutdown()
        event.accept()
