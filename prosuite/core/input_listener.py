"""Global keyboard/mouse hotkey listener.

``pynput`` already runs each listener on its own thread, so this class is a
plain ``QObject`` rather than a ``QThread``/``threading.Thread`` hybrid. Signals
are emitted from the listener threads and delivered to the GUI thread through
Qt's queued connections.
"""

from __future__ import annotations

import logging

from PySide6 import QtCore
from pynput import keyboard, mouse

log = logging.getLogger(__name__)

UNBOUND = "Not Set"


class InputListener(QtCore.QObject):
    #: (action_name, is_pressed) - a bound hotkey changed state.
    input_pressed = QtCore.Signal(str, bool)
    #: (action_name, key_label) - a hotkey was assigned or cleared.
    input_set = QtCore.Signal(str, str)

    def __init__(self, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._keyboard_listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._pending_bind: str | None = None
        self.keybinds: dict[str, str] = {}

    # --- lifecycle ---
    def start(self) -> None:
        if self._keyboard_listener is not None:
            return
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._keyboard_listener.start()
        self._mouse_listener.start()
        log.info("Hotkey listeners started")

    def stop(self) -> None:
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                listener.stop()
        self._keyboard_listener = None
        self._mouse_listener = None

    # --- binding ---
    def set_new_bind(self, action: str) -> None:
        """Capture the next input press and assign it to ``action``."""
        self._pending_bind = action

    def _assign(self, action: str, label: str) -> None:
        # A key can only drive one action, so clear whoever held it before.
        for other, bound_label in list(self.keybinds.items()):
            if bound_label == label and other != action:
                del self.keybinds[other]
                self.input_set.emit(other, UNBOUND)

        self.keybinds[action] = label
        self.input_set.emit(action, label)

    # --- event translation ---
    @staticmethod
    def _key_label(key) -> str | None:
        char = getattr(key, "char", None)
        if char:
            return char.upper()
        name = getattr(key, "name", None)
        return name.upper() if name else None

    @staticmethod
    def _button_label(button) -> str:
        return f"{button.name.upper()} CLICK"

    def _handle(self, label: str | None, pressed: bool) -> None:
        if label is None:
            return

        if pressed and self._pending_bind is not None:
            action, self._pending_bind = self._pending_bind, None
            self._assign(action, label)
            return

        for action, bound_label in self.keybinds.items():
            if bound_label == label:
                self.input_pressed.emit(action, pressed)

    def _on_press(self, key) -> None:
        self._handle(self._key_label(key), True)

    def _on_release(self, key) -> None:
        self._handle(self._key_label(key), False)

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        self._handle(self._button_label(button), pressed)
