"""Screen capture, colour-keyed target detection and aim output.

Everything in this module runs on a dedicated worker thread. It never touches
Qt widgets: the GUI thread pushes an immutable :class:`DetectionSettings`
snapshot in, and the worker emits an immutable :class:`DetectionResult` back
through a queued signal.
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass, replace

import cv2
import numpy as np
from PySide6 import QtCore

from . import capture
from .aimer import SmoothAimer
from .capture import ScreenSource, WindowSource
from .egomotion import EgoMotion
from .kalman import KalmanFilter

log = logging.getLogger(__name__)

#: Contours smaller than this many pixels are noise.
#: Longest side, in pixels, of the mask copy sent to the preview panel.
PREVIEW_SIZE = 180
MIN_CONTOUR_AREA = 10
#: Blobs whose centroids are within this distance are treated as one target.
CONTOUR_GROUP_DISTANCE = 80
#: Half-width of the square sampled under the crosshair for the triggerbot.
TRIGGER_REGION = 4

#: A measurement further than this from where the track says the target
#: should be is a different target, not a fast one: start a new track.
PREDICTION_GATE_PX = 120.0
#: How long a track survives with no detection (blob flicker) before reset.
PREDICTION_COAST_S = 0.25
#: Below this speed (px/s) the target is treated as stationary: no lead.
PREDICTION_MIN_SPEED = 40.0
#: Bounds on the time step fed to the filter, in seconds.
MIN_DT, MAX_DT = 1.0 / 240.0, 0.25
#: The lead never exceeds this fraction of the FOV, nor MAX_LEAD_PX: a bad
#: velocity reading should nudge the aim, not fling it across the screen.
MAX_LEAD_FRACTION = 0.35
MAX_LEAD_PX = 160.0

#: Our own drawing that lands inside the captured frame (screen mode only).
FOV_RING_WIDTH = 10
BOX_MARGIN = 14
TRACER_WIDTH = 10

# Radar distance is inferred from how large a target appears. These are the
# apparent linear sizes, in screen pixels, that read as point-blank and as far
# away; anything between is placed on a log scale, because apparent size falls
# off with distance rather than linearly.
RADAR_NEAR_PX = 100.0
RADAR_FAR_PX = 5.0
#: Most dots the radar will ever show, keeping the largest targets.
RADAR_MAX_TARGETS = 5
#: Apparent width below which a blob is noise rather than a contact.
RADAR_MIN_SIZE_PX = 8.0
#: Two dots closer than this in both bearing and distance are the same
#: contact - usually one target whose blob broke into pieces - so only the
#: larger of the pair gets drawn.
RADAR_MERGE_BEARING = 0.12
RADAR_MERGE_DISTANCE = 0.15


@dataclass(frozen=True)
class DetectionSettings:
    """Immutable snapshot of everything the worker needs for one frame."""

    screen_center: tuple[int, int] = (0, 0)
    #: Title of the window to capture, or "" for the whole screen.
    capture_window: str = capture.SCREEN
    fov: int = 200
    detection_rgb: tuple[int, int, int] = (255, 0, 0)
    tolerance: int = 80
    #: Minimum saturation / brightness (0-255) for a pixel to count. Lower
    #: them to catch dark or washed-out versions of the target colour.
    sat_floor: int = 100
    val_floor: int = 100
    #: Ship a small copy of the colour mask to the UI for the live preview.
    preview_enabled: bool = False

    aim_enabled: bool = False
    aim_key_held: bool = False
    priority: str = "Proximity"
    aim_position: str = "Body"
    y_offset: int = 0
    smoothness: int = 50
    speed: int = 10

    prediction_enabled: bool = False
    #: Lead time in units of 10 ms: the aim point is placed where the target
    #: is expected to be this far in the future. 5 means 50 ms.
    prediction_factor: int = 5

    trigger_enabled: bool = False
    #: Minimum gap between triggerbot shots, in milliseconds. 0 fires as fast
    #: as the capture loop allows, which is roughly 100 clicks a second.
    trigger_delay: int = 0
    #: How long the colour must stay under the crosshair before the first
    #: shot, in milliseconds. Fixed, not random: it is a debounce.
    trigger_reaction: int = 0
    esp_enabled: bool = False
    esp_mode: str = "Box"
    radar_enabled: bool = False
    #: Screen rectangles (left, top, right, bottom) of our own windows.
    ignore_rects: tuple[tuple[int, int, int, int], ...] = ()
    fov_ring: bool = False
    esp_tracers: bool = False

    @property
    def aiming(self) -> bool:
        return self.aim_enabled and self.aim_key_held

    @property
    def should_scan(self) -> bool:
        return (
            self.trigger_enabled
            or self.esp_enabled
            or self.radar_enabled
            or self.preview_enabled
            or self.aiming
        )


@dataclass(frozen=True)
class RadarTarget:
    """One blob's position on the radar, in FOV-independent terms.

    The worker reports geometry the widget can place at any cone width, so
    the dots stay lined up with the cone whatever the FOV slider is set to.
    """

    id: int
    #: Horizontal offset from the crosshair as a fraction of the FOV:
    #: -1 is the left edge of the captured region, +1 the right.
    bearing: float
    #: Radial position, 0 at the centre (close) to 1 at the rim (far).
    distance: float
    #: Apparent linear size in screen pixels, used to scale the dot.
    size: float


@dataclass(frozen=True)
class FrameStats:
    """How the worker is doing, for the status strip."""

    fps: float = 0.0
    #: Time spent processing a frame, excluding the pacing sleep.
    frame_ms: float = 0.0
    #: "img", "cnt" or "none" - how camera motion is being measured.
    ego: str = "none"
    #: Measured input lag in frames, 0 if unknown.
    lag: int = 0
    #: "Screen" or the captured window's title.
    source: str = "Screen"
    #: False while every module is off and the worker is just idling.
    scanning: bool = False


@dataclass(frozen=True)
class DetectionResult:
    """What the worker found in one frame. Positions are in screen pixels."""

    has_target: bool = False
    #: (x, y, w, h) of the target, relative to the capture region.
    bbox: tuple[int, int, int, int] | None = None
    #: Top-left of the capture region in screen coordinates.
    offset: tuple[int, int] = (0, 0)
    #: Target centroid in screen coordinates.
    screen_pos: tuple[int, int] | None = None
    #: Binary target mask, only populated for Pixel ESP.
    mask: np.ndarray | None = None
    radar_targets: tuple[RadarTarget, ...] = ()
    #: (start, end, magnitude) arrows for the prediction overlay.
    velocity_lines: tuple[tuple[tuple[int, int], tuple[int, int], float], ...] = ()
    #: Arraylist caption for the prediction entry, or None to leave it alone.
    prediction_label: str | None = None
    stats: FrameStats | None = None
    #: Small uint8 copy of the colour mask, only when the preview is open.
    preview: np.ndarray | None = None


def _begin_precise_timer() -> bool:
    """Request 1 ms sleep granularity from Windows. True if it was granted."""
    if sys.platform != "win32":
        return False
    try:
        return ctypes.windll.winmm.timeBeginPeriod(1) == 0
    except OSError:
        return False


def _end_precise_timer() -> None:
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except OSError:
        pass


class DetectionWorker(QtCore.QThread):
    """Runs the capture/detect/aim loop until :meth:`stop` is called."""

    result_ready = QtCore.Signal(object)

    #: Upper bound on loop rate. Capture is the bottleneck well before this.
    TARGET_FPS = 144

    def __init__(self, aimer: SmoothAimer, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._aimer = aimer
        self._kalman = KalmanFilter()
        self._settings = DetectionSettings()
        self._settings_lock = threading.Lock()
        self._running = False

        # Per-target tracking state, reset whenever the target is lost.
        self._last_update: float | None = None
        self._lost_since: float | None = None
        self._ego = EgoMotion()
        #: Total scene shift since tracking began. A target's *world* position
        #: is its screen position minus this, so camera motion drops out.
        self._camera = np.zeros(2)
        self._ego_source = "none"

        #: Screen rect of the ESP box we drew last frame, so it can be masked.
        self._last_box: tuple[int, int, int, int] | None = None

        self._preview: np.ndarray | None = None
        self._fps = 0.0
        self._frame_ms = 0.0
        self._loop_started = time.perf_counter()

        self._source: ScreenSource | WindowSource | None = None
        self._source_key: str | None = None
        self._next_source_try = 0.0

        #: perf_counter timestamp of the last triggerbot click, for the delay.
        self._last_trigger = float("-inf")
        #: When the colour first appeared under the crosshair, or None.
        self._trigger_since: float | None = None

        # Cached HSV threshold, recomputed only when colour/tolerance change.
        self._hsv_cache_key: tuple | None = None
        self._hsv_bounds: tuple[np.ndarray, np.ndarray] | None = None

    # --- public API (GUI thread) ---
    def update_settings(self, **changes) -> None:
        """Merge field changes into the settings snapshot."""
        with self._settings_lock:
            self._settings = replace(self._settings, **changes)

    @property
    def settings(self) -> DetectionSettings:
        with self._settings_lock:
            return self._settings

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

    # --- worker thread ---
    def run(self) -> None:
        self._running = True
        frame_budget = 1.0 / self.TARGET_FPS

        # Windows sleeps in ~15.6 ms steps by default, which silently caps the
        # loop near 64 FPS whatever TARGET_FPS says. Ask for 1 ms steps.
        precise = _begin_precise_timer()
        try:
            while self._running:
                started = time.perf_counter()
                try:
                    self._process_frame(self.settings)
                except Exception:
                    log.exception("Detection frame failed")
                    time.sleep(0.1)

                elapsed = time.perf_counter() - started
                self._record_timing(started, elapsed)
                if elapsed < frame_budget:
                    time.sleep(frame_budget - elapsed)
        finally:
            self._close_source()
            if precise:
                _end_precise_timer()

    def _record_timing(self, started: float, elapsed: float) -> None:
        """Smooth frame time and rate so the readout doesn't flicker."""
        period = started - self._loop_started
        self._loop_started = started
        blend = 0.1
        self._frame_ms += (elapsed * 1000.0 - self._frame_ms) * blend
        if period > 0:
            self._fps += (1.0 / period - self._fps) * blend

    def _stats(self, cfg: DetectionSettings) -> FrameStats:
        return FrameStats(
            fps=self._fps,
            frame_ms=self._frame_ms,
            ego=self._ego_source,
            lag=self._ego.lag,
            source=cfg.capture_window or "Screen",
            scanning=cfg.should_scan,
        )

    def _emit(self, result: DetectionResult) -> None:
        """Send a result, stamped with timing stats and the mask preview."""
        self.result_ready.emit(
            replace(result, stats=self._stats(self._settings), preview=self._preview)
        )

    # --- capture source ---
    def _close_source(self) -> None:
        if self._source is not None:
            self._source.close()
        self._source = None
        self._source_key = None

    def _ensure_source(self, cfg: DetectionSettings):
        """Open (or switch) the capture source; None while it's unavailable.

        A window that can't be found pauses detection rather than falling back
        to the whole screen, which would quietly change what is being scanned.
        """
        wanted = cfg.capture_window
        if self._source is not None and self._source_key == wanted:
            if isinstance(self._source, WindowSource) and not self._source.alive:
                self._close_source()  # window closed; look for it again
            else:
                return self._source

        now = time.perf_counter()
        if now < self._next_source_try:
            return None
        self._close_source()
        self._reset_tracking()

        source = ScreenSource() if wanted == capture.SCREEN else WindowSource(wanted)
        try:
            # mss binds to this thread, which is why opening happens here.
            source.open()
        except Exception as error:
            log.warning("Capture source unavailable (%s)", error)
            self._next_source_try = now + 1.0
            return None

        self._source, self._source_key = source, wanted
        return source

    def _process_frame(self, cfg: DetectionSettings) -> None:
        if not cfg.should_scan:
            self._preview = None
            self._reset_tracking()
            self._aimer.reset_carry()
            self._aimer.take_sent()
            self._emit(DetectionResult())
            return

        source = self._ensure_source(cfg)
        if source is None:
            self._preview = None
            self._emit(DetectionResult())
            return

        cx, cy = cfg.screen_center
        grabbed = source.grab(cx, cy, cfg.fov)
        if grabbed is None:  # window produced no new frame
            return
        region, bgr, stamp = grabbed
        mask = self._colour_mask(bgr, cfg)
        self._preview = self._make_preview(mask, cfg)

        # On a full-screen grab our own overlays are in the picture. Treat
        # them as not-there for both detection and the ego-motion estimate.
        ignore = None
        if source.sees_overlays:
            ignore = self._ignore_mask(region, bgr.shape[:2], cfg)
            if ignore is not None:
                mask = cv2.bitwise_and(mask, cv2.bitwise_not(ignore))

        sent = self._aimer.take_sent()
        if cfg.prediction_enabled or cfg.aiming:
            blanked = mask if ignore is None else cv2.bitwise_or(mask, ignore)
            shift, self._ego_source = self._ego.estimate(bgr, blanked, sent)
            self._camera += shift
        else:
            self._ego.reset()

        if cfg.trigger_enabled and cfg.aim_key_held:
            self._maybe_trigger(mask, cfg)

        contours = self._find_targets(mask)
        if not contours:
            self._on_target_lost(cfg)
            self._emit(DetectionResult())
            return

        centroids = np.array([c["center"] for c in contours])
        radar = self._build_radar(contours, centroids, cfg) if cfg.radar_enabled else ()

        if not (cfg.esp_enabled or cfg.aiming):
            self._emit(DetectionResult(radar_targets=radar))
            return

        self._emit_target(contours, centroids, region, mask, radar, cfg, stamp)

    # --- our own drawing, when the capture can see it ---
    def _ignore_mask(
        self, region: dict, shape: tuple[int, int], cfg: DetectionSettings
    ) -> np.ndarray | None:
        """Mask of pixels that are our overlays rather than the scene."""
        left, top = region["left"], region["top"]
        mask = np.zeros(shape, np.uint8)

        box = None
        if cfg.esp_enabled and self._last_box is not None:
            box = (
                self._last_box[0] - left,
                self._last_box[1] - top,
                self._last_box[2] - left,
                self._last_box[3] - top,
            )

        # Last frame's ESP box: only the band *outside* it. Masking the
        # inside would eat the target's own edge pixels every frame and the
        # box would shrink toward nothing.
        if box is not None:
            l, t, r, b = box
            cv2.rectangle(
                mask, (l - BOX_MARGIN, t - BOX_MARGIN), (r + BOX_MARGIN, b + BOX_MARGIN), 255, -1
            )
            cv2.rectangle(mask, (l, t), (r, b), 0, -1)

            if cfg.esp_tracers:
                cx, cy = cfg.screen_center
                tip = ((l + r) // 2, (t + b) // 2)
                line = np.zeros(shape, np.uint8)
                cv2.line(line, (cx - left, 2 * cy - top), tip, 255, TRACER_WIDTH)
                # Stop at the box so the line never cuts through the target.
                cv2.rectangle(
                    line, (l - BOX_MARGIN, t - BOX_MARGIN), (r + BOX_MARGIN, b + BOX_MARGIN), 0, -1
                )
                mask = cv2.bitwise_or(mask, line)

        if cfg.fov_ring:
            cv2.circle(mask, (cfg.fov, cfg.fov), cfg.fov, 255, FOV_RING_WIDTH)

        for rl, rt, rr, rb in cfg.ignore_rects:
            cv2.rectangle(mask, (rl - left, rt - top), (rr - left, rb - top), 255, -1)

        return mask if mask.any() else None

    @staticmethod
    def _make_preview(mask: np.ndarray, cfg: DetectionSettings) -> np.ndarray | None:
        if not cfg.preview_enabled:
            return None
        side = max(mask.shape)
        scale = PREVIEW_SIZE / side
        if scale >= 1.0:
            return mask.copy()
        size = (max(1, round(mask.shape[1] * scale)), max(1, round(mask.shape[0] * scale)))
        return cv2.resize(mask, size, interpolation=cv2.INTER_AREA)

    # --- colour filter ---
    def _colour_mask(self, bgr: np.ndarray, cfg: DetectionSettings) -> np.ndarray:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = None
        for lower, upper in self._hsv_ranges(cfg):
            part = cv2.inRange(hsv, lower, upper)
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        return mask

    def _hsv_ranges(
        self, cfg: DetectionSettings
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Hue window(s) around the target colour, cached between frames.

        Hue is circular (0 and 179 are neighbours), so a window that runs off
        either end - which is every red - wraps into a second range.
        """
        key = (cfg.detection_rgb, cfg.tolerance, cfg.sat_floor, cfg.val_floor)
        if key != self._hsv_cache_key:
            r, g, b = cfg.detection_rgb
            swatch = np.array([[[b, g, r]]], dtype=np.uint8)
            hue = int(cv2.cvtColor(swatch, cv2.COLOR_BGR2HSV)[0][0][0])
            tol = max(1, cfg.tolerance // 4)
            low, high = hue - tol, hue + tol

            def window(lo: int, hi: int):
                return (
                    np.array([lo, cfg.sat_floor, cfg.val_floor]),
                    np.array([hi, 255, 255]),
                )

            if high - low >= 179:
                spans = [(0, 179)]
            elif low < 0:
                spans = [(0, high), (180 + low, 179)]
            elif high > 179:
                spans = [(low, 179), (0, high - 180)]
            else:
                spans = [(low, high)]
            self._hsv_bounds = [window(lo, hi) for lo, hi in spans]
            self._hsv_cache_key = key
        return self._hsv_bounds

    def _maybe_trigger(self, mask: np.ndarray, cfg: DetectionSettings) -> None:
        """Click if the target colour sits under the crosshair.

        The colour has to stay there for ``cfg.trigger_reaction`` ms before the
        first shot, then shots are spaced by at least ``cfg.trigger_delay`` ms.
        Both are fixed values, not randomised - they only limit the rate.
        """
        now = time.perf_counter()
        centre = cfg.fov
        half = TRIGGER_REGION // 2
        patch = mask[centre - half : centre + half, centre - half : centre + half]

        if not patch.any():
            self._trigger_since = None
            return
        if self._trigger_since is None:
            self._trigger_since = now

        if now - self._trigger_since < cfg.trigger_reaction / 1000.0:
            return
        if now - self._last_trigger < cfg.trigger_delay / 1000.0:
            return

        self._aimer.click()
        self._last_trigger = now

    # --- target selection ---
    def _find_targets(self, mask: np.ndarray) -> list[dict]:
        """Return grouped targets as dicts of contour, centroid and area."""
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        blobs = []
        for contour in contours:
            moments = cv2.moments(contour)
            if moments["m00"] <= 0:
                continue
            area = cv2.contourArea(contour)
            if area <= MIN_CONTOUR_AREA:
                continue
            blobs.append(
                {
                    "contour": contour,
                    "center": (
                        moments["m10"] / moments["m00"],
                        moments["m01"] / moments["m00"],
                    ),
                    "area": area,
                }
            )

        return self._group_blobs(blobs)

    @staticmethod
    def _group_blobs(
        blobs: list[dict], max_distance: int = CONTOUR_GROUP_DISTANCE
    ) -> list[dict]:
        """Merge nearby blobs so one figure is one target, not many.

        Largest blob seeds each group and claims every unclaimed blob within
        ``max_distance`` of it - the same leader-clustering the original used,
        but with the distance pass vectorised instead of a nested Python loop.
        """
        if not blobs:
            return []

        centers = np.array([b["center"] for b in blobs])
        areas = np.array([b["area"] for b in blobs])
        order = np.argsort(-areas)
        claimed = np.zeros(len(blobs), dtype=bool)

        groups = []
        for seed in order:
            if claimed[seed]:
                continue

            distances = np.linalg.norm(centers - centers[seed], axis=1)
            members = np.flatnonzero((distances <= max_distance) & ~claimed)
            claimed[members] = True

            if len(members) == 1:
                groups.append(blobs[seed])
                continue

            points = np.vstack([blobs[i]["contour"].reshape(-1, 2) for i in members])
            if len(points) >= 3:
                hull = cv2.convexHull(points)
                moments = cv2.moments(hull)
                center = (
                    (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])
                    if moments["m00"] > 0
                    else tuple(centers[seed])
                )
                groups.append(
                    {
                        "contour": hull,
                        "center": center,
                        "area": float(areas[members].sum()),
                    }
                )
            else:
                groups.append(max((blobs[i] for i in members), key=lambda b: b["area"]))

        return groups

    def _build_radar(
        self, targets: list[dict], centroids: np.ndarray, cfg: DetectionSettings
    ) -> tuple[RadarTarget, ...]:
        """Map every detected blob onto a radar bearing and distance.

        Bearing is normalised against the FOV, and distance comes from how
        large the target appears - a bigger blob is a nearer target.

        The dial is a summary, not a dump: specks are ignored, dots that land
        on top of each other are collapsed, and only the largest
        ``RADAR_MAX_TARGETS`` survive. Without that, one target whose blob
        breaks into pieces reads as a crowd.
        """
        fov = max(cfg.fov, 1)
        # Horizontal displacement only; vertical position is irrelevant to a
        # top-down radar.
        offsets = centroids[:, 0] - fov
        span = math.log(RADAR_NEAR_PX / RADAR_FAR_PX)

        contacts = []
        for index, target in enumerate(targets):
            # Area is a squared quantity; its root is the apparent width, which
            # is what actually scales with 1/distance.
            size = math.sqrt(max(float(target["area"]), 1.0))
            if size < RADAR_MIN_SIZE_PX:
                continue
            ratio = math.log(RADAR_NEAR_PX / max(size, 1e-6)) / span
            contacts.append(
                RadarTarget(
                    id=index,
                    bearing=float(np.clip(offsets[index] / fov, -1.0, 1.0)),
                    distance=float(np.clip(ratio, 0.0, 1.0)),
                    size=size,
                )
            )

        # Largest first, so a merged pair keeps the more substantial reading
        # and the cap keeps the targets that matter.
        contacts.sort(key=lambda t: -t.size)

        merged: list[RadarTarget] = []
        for contact in contacts:
            if any(
                abs(contact.bearing - kept.bearing) < RADAR_MERGE_BEARING
                and abs(contact.distance - kept.distance) < RADAR_MERGE_DISTANCE
                for kept in merged
            ):
                continue
            merged.append(contact)
            if len(merged) == RADAR_MAX_TARGETS:
                break

        # Renumber so ids stay contiguous for anything keying off them.
        return tuple(replace(t, id=i) for i, t in enumerate(merged))

    def _emit_target(
        self,
        targets: list[dict],
        centroids: np.ndarray,
        region: dict,
        mask: np.ndarray,
        radar: tuple[RadarTarget, ...],
        cfg: DetectionSettings,
        stamp: float | None = None,
    ) -> None:
        fov = cfg.fov
        if cfg.priority == "Proximity":
            distances = np.linalg.norm(centroids - np.array([fov, fov]), axis=1)
            index = int(np.argmin(distances))
        else:
            index = int(np.argmax([t["area"] for t in targets]))

        target = targets[index]
        cx, cy = target["center"]
        x, y, w, h = cv2.boundingRect(target["contour"])

        aim_x, aim_y = cx, cy
        if cfg.aim_position == "Head":
            aim_y = y + h * 0.15
        elif cfg.aim_position == "Custom":
            aim_y += cfg.y_offset

        screen_pos = (int(region["left"] + cx), int(region["top"] + cy))
        self._last_box = (
            region["left"] + x,
            region["top"] + y,
            region["left"] + x + w,
            region["top"] + y + h,
        )
        velocity_lines: tuple = ()
        prediction_label = None

        if cfg.prediction_enabled:
            aim_x, aim_y, velocity_lines, prediction_label = self._apply_prediction(
                np.array([aim_x, aim_y]), np.array([cx, cy]), screen_pos, region, cfg, stamp
            )
        else:
            self._reset_tracking()

        if cfg.aiming:
            self._aimer.smoothness = cfg.smoothness
            self._aimer.speed = cfg.speed
            # Moves we already sent haven't reached the screen yet (input
            # lag), so the offset we can see overstates what's left to do.
            # Aim at where the target will be once they land, or each frame
            # re-orders the same correction and the aim overshoots and snaps
            # back.
            pending = self._ego.pending_shift()
            self._aimer.move(aim_x - fov + pending[0], aim_y - fov + pending[1])

        # The mask is only worth shipping across threads for Pixel ESP, which
        # is the one mode that draws every matching pixel rather than a box.
        pixel_mask = (
            mask if cfg.esp_enabled and cfg.esp_mode == "Pixel" else None
        )

        self._emit(
            DetectionResult(
                has_target=True,
                bbox=(x, y, w, h),
                offset=(region["left"], region["top"]),
                screen_pos=screen_pos,
                mask=pixel_mask,
                radar_targets=radar,
                velocity_lines=velocity_lines,
                prediction_label=prediction_label,
            )
        )

    # --- prediction ---
    def _apply_prediction(
        self,
        aim_point: np.ndarray,
        centroid: np.ndarray,
        screen_pos: tuple[int, int],
        region: dict,
        cfg: DetectionSettings,
        timestamp: float | None = None,
    ):
        """Lead the aim point by the target's world velocity times a lead time.

        The filter tracks the blob centroid in *world* coordinates - screen
        position minus the camera's accumulated motion - so it measures how
        the target moves through the scene, not how it moves across a screen
        that the aimer is itself dragging along. A target the aim holds dead
        centre therefore still has its true velocity, and still gets a lead.

        The centroid is tracked rather than the aim point so a Head or Y
        offset doesn't masquerade as motion. All smoothing is the Kalman
        filter's job; the aimer's damping does the rest.
        """
        now = time.perf_counter() if timestamp is None else timestamp
        dt = 1.0 / 60.0
        if self._last_update is not None:
            dt = min(max(now - self._last_update, MIN_DT), MAX_DT)
        self._last_update = now
        self._lost_since = None

        kalman = self._kalman
        world = centroid - self._camera
        if kalman.initialized:
            expected = kalman.get_position() + kalman.get_velocity() * dt
            if float(np.linalg.norm(world - expected)) > PREDICTION_GATE_PX:
                kalman.reset()
        # The faster the view moved, the less the camera subtraction can be
        # trusted - which is exactly when the aimer is swinging hardest, so
        # leaning on momentum then is what keeps the loop from ringing.
        kalman.update(world, dt, extra_sigma=self._ego.uncertainty)

        velocity = kalman.get_velocity()
        speed = float(np.linalg.norm(velocity))
        confidence = kalman.get_prediction_confidence()

        lead = np.zeros(2)
        if confidence > 0.0 and speed > PREDICTION_MIN_SPEED:
            # The slider is extra lead on top of the system's own delay: a
            # command sent now lands `lag` frames from now, and the target
            # keeps moving until it does.
            lead_seconds = cfg.prediction_factor * 0.010 + self._ego.lag * dt
            lead = velocity * lead_seconds * confidence
            reach = float(np.linalg.norm(lead))
            cap = min(MAX_LEAD_PX, MAX_LEAD_FRACTION * cfg.fov)
            if reach > cap:
                lead *= cap / reach

        limit = 2 * cfg.fov
        aim = np.clip(aim_point + lead, 0, limit)
        tip = np.clip(centroid + lead, 0, limit)

        lines = (
            (
                screen_pos,
                (int(region["left"] + tip[0]), int(region["top"] + tip[1])),
                # The overlay's styling is tuned for pixels per frame.
                speed / 60.0,
            ),
        )

        lead_ms = float(np.linalg.norm(lead) / max(speed, 1e-6)) * 1000.0
        tag = {"img": "img", "cnt": "cnt"}.get(self._ego_source, "no ego")
        label = f"Prediction [{lead_ms:.0f}ms {tag}]" if lead.any() else "Prediction"
        return float(aim[0]), float(aim[1]), lines, label

    # --- tracking state ---
    def _on_target_lost(self, cfg: DetectionSettings) -> None:
        self._aimer.reset_carry()
        self._last_box = None
        # Coast through a brief dropout so velocity survives a flickering
        # blob, but don't carry a stale track onto a later, different target.
        now = time.perf_counter()
        if self._lost_since is None:
            self._lost_since = now
        elif now - self._lost_since > PREDICTION_COAST_S:
            self._reset_tracking()

    def _reset_tracking(self) -> None:
        self._kalman.reset()
        self._ego.reset()
        self._camera = np.zeros(2)
        self._last_box = None
        self._last_update = None
        self._lost_since = None

    def reset_prediction(self) -> None:
        """Clear all tracking state - called when the aimbot is toggled."""
        self._reset_tracking()
