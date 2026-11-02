"""Where frames come from: the whole screen, or a single application window.

Window capture uses Windows Graphics Capture, which renders *that window's*
content only. Our own overlays are separate windows, so they never appear in
the frames - the detector can't mistake its own ESP box or FOV ring for a
target. Screen capture (``mss``) sees everything on the monitor, overlays
included, so pick a colour for them that is far from the target colour.

Both sources return ``(region, bgr, stamp)``: ``region`` is the screen-space
rectangle the frame covers (``left``/``top``/``width``/``height``), ``bgr`` is
an ``HxWx3`` uint8 array of exactly that area and ``stamp`` the
``perf_counter`` time the frame was captured.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes

import cv2
import numpy as np

log = logging.getLogger(__name__)

SCREEN = ""  # the capture_window value that means "whole screen"
SCREEN_LABEL = "Entire Screen"

_IS_WINDOWS = sys.platform == "win32"
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14


# --- window discovery ---
def _frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Visible (left, top, right, bottom) of a window, minus invisible borders."""
    rect = wintypes.RECT()
    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if hr != 0 and not ctypes.windll.user32.GetWindowRect(
        wintypes.HWND(hwnd), ctypes.byref(rect)
    ):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def list_windows() -> list[tuple[int, str]]:
    """Visible, titled top-level windows, excluding this process's own."""
    if not _IS_WINDOWS:
        return []

    user32 = ctypes.windll.user32
    own_pid = os.getpid()
    found: list[tuple[int, str]] = []
    seen: set[str] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True

        cloaked = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        if cloaked.value:
            return True

        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if title and title not in seen:
            seen.add(title)
            found.append((int(hwnd), title))
        return True

    user32.EnumWindows(visit, 0)
    return sorted(found, key=lambda item: item[1].lower())


def find_window(title: str) -> int | None:
    for hwnd, name in list_windows():
        if name == title:
            return hwnd
    return None


# --- sources ---
class ScreenSource:
    """Grabs a square around a point on the monitor with ``mss``.

    The monitor includes our own overlays, so the detector has to mask them.

    ``mss`` binds to the thread that creates it, so :meth:`open` must be
    called from the detection thread.
    """

    #: Whether our overlay windows show up in the frames.
    sees_overlays = True

    def __init__(self) -> None:
        self._sct = None

    def open(self) -> None:
        import mss

        self._sct = mss.mss()

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None

    def grab(self, cx: int, cy: int, fov: int):
        region = {"left": cx - fov, "top": cy - fov, "width": 2 * fov, "height": 2 * fov}
        shot = self._sct.grab(region)
        # .raw is BGRA with no Python-level conversion, unlike mss's .rgb.
        frame = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return region, cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR), time.perf_counter()


class WindowSource:
    """Captures one window via Windows Graphics Capture.

    Frames arrive on the library's own thread. The crop is taken right there,
    because the delivered buffer is only valid inside the callback and copying
    just the region of interest is far cheaper than copying the whole window.
    """

    sees_overlays = False

    #: How long :meth:`grab` waits for the window to produce a new frame.
    FRAME_TIMEOUT = 0.05

    def __init__(self, title: str) -> None:
        self.title = title
        self._control = None
        self._lock = threading.Lock()
        self._fresh = threading.Event()
        self._latest = None
        self._want = (0, 0, 100)
        self._hwnd: int | None = None

    def open(self) -> None:
        from windows_capture import WindowsCapture

        hwnd = find_window(self.title)
        if hwnd is None:
            raise LookupError(f"window not found: {self.title!r}")
        self._hwnd = hwnd

        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=hwnd,
        )

        @capture.event
        def on_frame_arrived(frame, _control):
            self._on_frame(frame)

        @capture.event
        def on_closed():
            log.info("Capture of %r closed", self.title)

        self._control = capture.start_free_threaded()
        log.info("Capturing window %r", self.title)

    def close(self) -> None:
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                log.exception("Stopping window capture failed")
            self._control = None

    @property
    def alive(self) -> bool:
        return self._control is not None and not self._control.is_finished()

    def _on_frame(self, frame) -> None:
        bounds = _frame_bounds(self._hwnd)
        if bounds is None:
            return
        cx, cy, fov = self._want
        left, top = bounds[0], bounds[1]

        # Crop in frame coordinates, padding black where the region hangs off
        # the window so the crosshair stays at the crop's centre.
        x0, y0 = cx - fov - left, cy - fov - top
        size = 2 * fov
        buffer = frame.frame_buffer
        height, width = buffer.shape[:2]

        sx0, sy0 = max(x0, 0), max(y0, 0)
        sx1, sy1 = min(x0 + size, width), min(y0 + size, height)
        out = np.zeros((size, size, 3), dtype=np.uint8)
        if sx1 > sx0 and sy1 > sy0:
            out[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = buffer[sy0:sy1, sx0:sx1, :3]

        region = {"left": cx - fov, "top": cy - fov, "width": size, "height": size}
        # Stamped when the frame arrived, not when we get round to it: the
        # tracker divides by the time between frames, and the worker's own
        # scheduling jitter would otherwise show up as target velocity.
        stamp = time.perf_counter()
        with self._lock:
            self._latest = (region, out, stamp)
        self._fresh.set()

    def grab(self, cx: int, cy: int, fov: int):
        """Return the newest frame, or ``None`` if the window hasn't changed."""
        self._want = (cx, cy, fov)
        if not self._fresh.wait(self.FRAME_TIMEOUT):
            return None
        self._fresh.clear()
        with self._lock:
            latest, self._latest = self._latest, None
        if latest is None:
            return None
        region, bgr, stamp = latest
        # A frame cropped for an older FOV is the wrong size - drop it.
        if bgr.shape[0] != 2 * fov:
            return None
        return region, bgr, stamp
