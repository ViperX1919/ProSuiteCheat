"""Relative mouse output via the Win32 ``SendInput`` API."""

from __future__ import annotations

import ctypes
import logging
import sys
import time

log = logging.getLogger(__name__)

# --- Win32 structures ---
_ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

CLICK_HOLD_SECONDS = 0.01


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _InputUnion)]


_IS_WINDOWS = sys.platform == "win32"
_send_input = ctypes.windll.user32.SendInput if _IS_WINDOWS else None


class SmoothAimer:
    """Converts a pixel offset into damped relative mouse movement.

    The movement scale is ``(offset / smoothness) * (speed / 10)``, matching the
    original formula exactly so existing saved configs feel identical.
    """

    #: Sub-pixel moves are dropped; SendInput takes integers, so anything below
    #: this threshold would truncate to zero anyway.
    MIN_STEP = 0.5

    def __init__(self, smoothness: float = 10.0, speed: float = 1.0):
        self.smoothness = smoothness
        self.speed = speed
        # Reused across calls so the hot path does no per-frame allocation.
        # Fractional pixels SendInput could not express last time, carried over
        # so slow approaches still arrive instead of stalling short.
        self._carry_x = 0.0
        self._carry_y = 0.0
        self._extra = ctypes.c_ulong(0)
        self._extra_ptr = ctypes.pointer(self._extra)
        self._event = Input(ctypes.c_ulong(INPUT_MOUSE), _InputUnion())
        self._event_size = ctypes.sizeof(Input)

    def _dispatch(self, dx: int, dy: int, flags: int) -> None:
        if not _IS_WINDOWS:
            return
        self._event.ii.mi = MouseInput(dx, dy, 0, flags, 0, self._extra_ptr)
        _send_input(1, ctypes.byref(self._event), self._event_size)

    def move(self, dx: float, dy: float) -> None:
        """Nudge the cursor by a damped fraction of the given pixel offset."""
        smoothness = self.smoothness or 1.0
        step_x = (dx / smoothness) * (self.speed / 10.0)
        step_y = (dy / smoothness) * (self.speed / 10.0)

        total_x = step_x + self._carry_x
        total_y = step_y + self._carry_y
        out_x, out_y = int(total_x), int(total_y)
        self._carry_x = total_x - out_x
        self._carry_y = total_y - out_y
        if out_x or out_y:
            self._dispatch(out_x, out_y, MOUSEEVENTF_MOVE)

    def reset_carry(self) -> None:
        """Drop leftover sub-pixels, e.g. when the target changes or is lost."""
        self._carry_x = self._carry_y = 0.0

    def click(self) -> None:
        """Send a left press/release pair.

        Blocks for ``CLICK_HOLD_SECONDS``; only call this from the detection
        thread, never from the GUI thread.
        """
        self._dispatch(0, 0, MOUSEEVENTF_LEFTDOWN)
        time.sleep(CLICK_HOLD_SECONDS)
        self._dispatch(0, 0, MOUSEEVENTF_LEFTUP)
