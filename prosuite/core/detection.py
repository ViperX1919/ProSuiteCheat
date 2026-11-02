"""Screen capture, colour-keyed target detection and aim output.

Everything in this module runs on a dedicated worker thread. It never touches
Qt widgets: the GUI thread pushes an immutable :class:`DetectionSettings`
snapshot in, and the worker emits an immutable :class:`DetectionResult` back
through a queued signal.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, replace

import cv2
import mss
import numpy as np
from PySide6 import QtCore

from .aimer import SmoothAimer
from .kalman import KalmanFilter

log = logging.getLogger(__name__)

#: Contours smaller than this many pixels are noise.
MIN_CONTOUR_AREA = 10
#: Blobs whose centroids are within this distance are treated as one target.
CONTOUR_GROUP_DISTANCE = 80
#: Half-width of the square sampled under the crosshair for the triggerbot.
TRIGGER_REGION = 4
#: Saturation/value floor for a pixel to count as the target colour.
SATURATION_FLOOR = 100
VALUE_FLOOR = 100

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
    fov: int = 200
    detection_rgb: tuple[int, int, int] = (255, 0, 0)
    tolerance: int = 80

    aim_enabled: bool = False
    aim_key_held: bool = False
    priority: str = "Proximity"
    aim_position: str = "Body"
    y_offset: int = 0
    smoothness: int = 50
    speed: int = 10

    prediction_enabled: bool = False
    prediction_factor: int = 5

    trigger_enabled: bool = False
    #: Minimum gap between triggerbot shots, in milliseconds. 0 fires as fast
    #: as the capture loop allows, which is roughly 100 clicks a second.
    trigger_delay: int = 0
    esp_enabled: bool = False
    esp_mode: str = "Box"
    radar_enabled: bool = False

    @property
    def aiming(self) -> bool:
        return self.aim_enabled and self.aim_key_held

    @property
    def should_scan(self) -> bool:
        return (
            self.trigger_enabled
            or self.esp_enabled
            or self.radar_enabled
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
        self._last_target_pos: np.ndarray | None = None
        self._smoothed_velocity: np.ndarray | None = None
        self._last_aim_pos: tuple[float, float] | None = None

        #: perf_counter timestamp of the last triggerbot click, for the delay.
        self._last_trigger = float("-inf")

        # Cached HSV threshold, recomputed only when colour/tolerance change.
        self._hsv_cache_key: tuple[tuple[int, int, int], int] | None = None
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

        # mss binds to the thread that creates it, so it must live in here.
        # Creating it once instead of per-frame is the single biggest win over
        # the original implementation.
        with mss.mss() as sct:
            while self._running:
                started = time.perf_counter()
                try:
                    self._process_frame(sct, self.settings)
                except Exception:
                    log.exception("Detection frame failed")
                    time.sleep(0.1)

                elapsed = time.perf_counter() - started
                if elapsed < frame_budget:
                    time.sleep(frame_budget - elapsed)

    def _process_frame(self, sct: mss.base.MSSBase, cfg: DetectionSettings) -> None:
        if not cfg.should_scan:
            self._reset_tracking()
            self.result_ready.emit(DetectionResult())
            return

        region, mask = self._capture_mask(sct, cfg)

        if cfg.trigger_enabled and cfg.aim_key_held:
            self._maybe_trigger(mask, cfg)

        contours = self._find_targets(mask)
        if not contours:
            self._on_target_lost(cfg)
            self.result_ready.emit(DetectionResult())
            return

        centroids = np.array([c["center"] for c in contours])
        radar = self._build_radar(contours, centroids, cfg) if cfg.radar_enabled else ()

        if not (cfg.esp_enabled or cfg.aiming):
            self.result_ready.emit(DetectionResult(radar_targets=radar))
            return

        self._emit_target(contours, centroids, region, mask, radar, cfg)

    # --- capture ---
    def _capture_mask(
        self, sct: mss.base.MSSBase, cfg: DetectionSettings
    ) -> tuple[dict, np.ndarray]:
        cx, cy = cfg.screen_center
        fov = cfg.fov
        region = {
            "left": cx - fov,
            "top": cy - fov,
            "width": 2 * fov,
            "height": 2 * fov,
        }

        shot = sct.grab(region)
        # .raw is BGRA with no Python-level conversion, unlike mss's .rgb.
        frame = np.frombuffer(shot.raw, dtype=np.uint8).reshape(
            shot.height, shot.width, 4
        )
        bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        mask = None
        for lower, upper in self._hsv_ranges(cfg):
            part = cv2.inRange(hsv, lower, upper)
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        return region, mask

    def _hsv_ranges(
        self, cfg: DetectionSettings
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Hue window(s) around the target colour, cached between frames.

        Hue is circular (0 and 179 are neighbours), so a window that runs off
        either end - which is every red - wraps into a second range.
        """
        key = (cfg.detection_rgb, cfg.tolerance)
        if key != self._hsv_cache_key:
            r, g, b = cfg.detection_rgb
            swatch = np.array([[[b, g, r]]], dtype=np.uint8)
            hue = int(cv2.cvtColor(swatch, cv2.COLOR_BGR2HSV)[0][0][0])
            tol = max(1, cfg.tolerance // 4)
            low, high = hue - tol, hue + tol

            def window(lo: int, hi: int):
                return (
                    np.array([lo, SATURATION_FLOOR, VALUE_FLOOR]),
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

        Shots are spaced by at least ``cfg.trigger_delay`` milliseconds. The
        gap is fixed, not randomised - this only limits the rate.
        """
        now = time.perf_counter()
        if now - self._last_trigger < cfg.trigger_delay / 1000.0:
            return

        centre = cfg.fov
        half = TRIGGER_REGION // 2
        patch = mask[centre - half : centre + half, centre - half : centre + half]
        if patch.any():
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
        velocity_lines: tuple = ()
        prediction_label = None

        if cfg.prediction_enabled:
            aim_x, aim_y, velocity_lines, prediction_label = self._apply_prediction(
                np.array([cx, cy]), screen_pos, region, cfg
            )
        else:
            self._smoothed_velocity = None
            self._last_aim_pos = None

        self._last_target_pos = np.array([cx, cy])

        if cfg.aiming:
            self._aimer.smoothness = cfg.smoothness
            self._aimer.speed = cfg.speed
            self._aimer.move(aim_x - fov, aim_y - fov)

        # The mask is only worth shipping across threads for Pixel ESP, which
        # is the one mode that draws every matching pixel rather than a box.
        pixel_mask = (
            mask if cfg.esp_enabled and cfg.esp_mode == "Pixel" else None
        )

        self.result_ready.emit(
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
        current: np.ndarray,
        screen_pos: tuple[int, int],
        region: dict,
        cfg: DetectionSettings,
    ):
        """Blend a velocity-extrapolated position into the aim point."""
        self._kalman.update(current)
        velocity = self._kalman.get_velocity()
        speed = float(np.linalg.norm(velocity))
        confidence = self._kalman.get_prediction_confidence()

        scaled_factor = 0.0
        predicted = current

        if confidence > 0.1 and speed > 1.0:
            scaled_factor = (cfg.prediction_factor / 10.0) * confidence

            if self._smoothed_velocity is None:
                self._smoothed_velocity = velocity.copy()
            else:
                self._smoothed_velocity = self._smoothed_velocity * 0.7 + velocity * 0.3

            # Extrapolate along the smoothed heading only while the raw and
            # smoothed directions agree; otherwise trust the instantaneous one.
            heading = velocity / (speed + 1e-6)
            smoothed_heading = self._smoothed_velocity / (
                np.linalg.norm(self._smoothed_velocity) + 1e-6
            )
            if float(np.dot(heading, smoothed_heading)) > 0.5:
                predicted = current + self._smoothed_velocity * scaled_factor
            else:
                predicted = current + velocity * scaled_factor

        elif speed > 1.0 and self._last_target_pos is not None:
            scaled_factor = cfg.prediction_factor / 20.0
            predicted = current + (current - self._last_target_pos) * scaled_factor

        predicted = np.clip(predicted, 0, 2 * cfg.fov)

        lines = (
            (
                screen_pos,
                (
                    int(region["left"] + predicted[0]),
                    int(region["top"] + predicted[1]),
                ),
                speed,
            ),
        )

        blend = min(0.5, scaled_factor * 0.05)
        raw_aim = current * (1.0 - blend) + predicted * blend

        if self._last_aim_pos is not None:
            aim_x = self._last_aim_pos[0] * 0.8 + raw_aim[0] * 0.2
            aim_y = self._last_aim_pos[1] * 0.8 + raw_aim[1] * 0.2
        else:
            aim_x, aim_y = float(raw_aim[0]), float(raw_aim[1])

        self._last_aim_pos = (aim_x, aim_y)

        label = f"Prediction [{blend:.2f}]" if speed > 1.0 else "Prediction"
        return aim_x, aim_y, lines, label

    # --- tracking state ---
    def _on_target_lost(self, cfg: DetectionSettings) -> None:
        self._aimer.reset_carry()
        self._last_target_pos = None
        if cfg.prediction_enabled:
            self._kalman.handle_target_loss()
        self._smoothed_velocity = None
        self._last_aim_pos = None

    def _reset_tracking(self) -> None:
        self._aimer.reset_carry()
        self._last_target_pos = None
        self._smoothed_velocity = None
        self._last_aim_pos = None

    def reset_prediction(self) -> None:
        """Clear all tracking state - called when the aimbot is toggled."""
        self._kalman.reset()
        self._reset_tracking()
