"""Estimate how far the view itself moved between two frames.

Predicting a target means knowing how it moves through the *world*, not across
the screen. The screen position mixes the target's motion with the camera's -
yours and the aimer's. While the aim is locked on, the target barely moves on
screen at all even if it is running flat out, so on-screen velocity says
nothing about where it is going.

This module measures the camera part, so the tracker can subtract it. Two
sources, best first:

* **Image**: phase-correlate the background between consecutive frames, with
  the target masked out. It sees every cause of camera motion - your mouse,
  the aimer, anything - and needs no calibration. It needs some texture in the
  scene to lock onto.
* **Counts**: when the background is too blank to measure, use the mouse
  counts the aimer itself sent, scaled by a pixels-per-count gain learned
  online from the frames where the image estimate was good. This misses
  movement you made with your own mouse, but never guesses.
"""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np

#: Frames are shrunk so their longest side is this many pixels. Phase
#: correlation cost grows with area and the answer needs no more precision.
WORK_SIZE = 256
#: Minimum phase-correlation peak (0-1) to trust an image measurement.
MIN_RESPONSE = 0.12
#: Brightness spread (grey levels) the background needs for phase correlation
#: to mean anything. A flat wall correlates "perfectly" at zero shift.
MIN_TEXTURE_STD = 2.5
#: Pixels around the target blob that are ignored, at full resolution.
TARGET_MARGIN = 12
#: Per-frame decay of the gain regression, so it follows sensitivity changes.
GAIN_DECAY = 0.97
#: Sum of squared counts needed before the learned gain is believed.
GAIN_MIN_EVIDENCE = 200.0

#: Frames of history kept for finding the input lag, and how far to look.
HISTORY = 90
MAX_LAG = 10
LAG_RECHECK_EVERY = 10
#: Normalised correlation needed before a lag estimate is believed.
LAG_MIN_CORRELATION = 0.35

IMAGE, COUNTS, NONE = "img", "cnt", "none"

#: Error of each estimate as a fraction of the motion it reports, plus a
#: floor in pixels. Phase correlation reads a few percent low and gets worse
#: as shifts grow; counts add latency and gain error; with no estimate at all
#: the whole commanded movement is unaccounted for.
UNCERTAINTY = {IMAGE: (0.12, 0.4), COUNTS: (0.35, 0.8), NONE: (1.0, 0.8)}


class EgoMotion:
    def __init__(self) -> None:
        self._window_cache: dict[tuple[int, int], np.ndarray] = {}
        self.reset()

    def reset(self) -> None:
        self._prev: np.ndarray | None = None
        self._prev_cut: np.ndarray | None = None
        self._scale = 1.0
        self._sum_dc = 0.0  # sum of (content shift . counts sent)
        self._sum_cc = 0.0  # sum of counts sent squared
        #: Pixel error to assume on the last estimate (see UNCERTAINTY).
        self.uncertainty = 0.0
        # Per-frame counts sent and scene shift measured, to find how many
        # frames pass between sending a movement and seeing it on screen.
        self._sent_hist: deque = deque(maxlen=HISTORY)
        self._shift_hist: deque = deque(maxlen=HISTORY)
        self._frames = 0
        self.lag = 0

    @property
    def gain(self) -> float | None:
        """Learned pixels of scene shift per mouse count, once there's evidence."""
        if self._sum_cc < GAIN_MIN_EVIDENCE:
            return None
        gain = -self._sum_dc / self._sum_cc
        return gain if gain > 0 else None

    def _hanning(self, shape: tuple[int, int]) -> np.ndarray:
        window = self._window_cache.get(shape)
        if window is None:
            window = cv2.createHanningWindow((shape[1], shape[0]), cv2.CV_32F)
            self._window_cache[shape] = window
        return window

    def _prepare(self, bgr: np.ndarray, exclude: np.ndarray | None):
        """Shrink to grayscale; return it with the grown exclusion mask."""
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        scale = WORK_SIZE / max(height, width)
        small_size = (max(8, round(width * scale)), max(8, round(height * scale)))
        small = cv2.resize(gray, small_size, interpolation=cv2.INTER_AREA).astype(
            np.float32
        )

        grown = np.zeros(small.shape, dtype=bool)
        if exclude is not None and exclude.any():
            kernel = np.ones((2 * TARGET_MARGIN + 1,) * 2, np.uint8)
            big = cv2.dilate(exclude, kernel)
            grown = cv2.resize(big, small_size, interpolation=cv2.INTER_NEAREST) > 0

        self._scale = scale
        return small, grown

    @staticmethod
    def _blank_out(image: np.ndarray, region: np.ndarray) -> np.ndarray:
        """Paint ``region`` with the rest of the image's mean brightness."""
        if region.any() and not region.all():
            image = image.copy()
            image[region] = image[~region].mean()
        return image

    def _estimate(
        self,
        bgr: np.ndarray,
        exclude: np.ndarray | None,
        sent: tuple[float, float],
    ) -> tuple[np.ndarray, str]:
        """Return ``(shift, source)``.

        ``shift`` is how far the scene *content* moved on screen since the
        previous call, in full-resolution pixels (turn the camera right and
        the content moves left, so the shift is negative). ``exclude`` is the
        target mask to ignore; ``sent`` the mouse counts the aimer sent over
        the same interval.
        """
        current, current_cut = self._prepare(bgr, exclude)
        previous, previous_cut = self._prev, self._prev_cut
        self._prev, self._prev_cut = current, current_cut

        if previous is None or previous.shape != current.shape:
            return np.zeros(2), NONE

        # Blank out where the target was *and* where it is, identically in
        # both frames. Blanking each frame's own target instead leaves a patch
        # that moves with the target, which the correlation mistakes for the
        # whole scene moving.
        cut = previous_cut | current_cut
        previous = self._blank_out(previous, cut)
        current = self._blank_out(current, cut)

        counts = np.asarray(sent, dtype=float)
        textured = float(current[~cut].std() if (~cut).any() else 0.0) >= MIN_TEXTURE_STD
        response, dx, dy = 0.0, 0.0, 0.0
        if textured:
            (dx, dy), response = cv2.phaseCorrelate(
                previous, current, self._hanning(current.shape)
            )

        limit = 0.5 * max(current.shape)
        if response >= MIN_RESPONSE and abs(dx) < limit and abs(dy) < limit:
            shift = np.array([dx, dy]) / self._scale
            # Learn pixels-per-count from frames where we know both.
            self._sum_dc = GAIN_DECAY * self._sum_dc + float(shift @ counts)
            self._sum_cc = GAIN_DECAY * self._sum_cc + float(counts @ counts)
            return shift, IMAGE

        gain = self.gain
        if gain is not None:
            return -gain * counts, COUNTS
        return np.zeros(2), NONE

    def estimate(
        self,
        bgr: np.ndarray,
        exclude: np.ndarray | None,
        sent: tuple[float, float],
    ) -> tuple[np.ndarray, str]:
        """See :meth:`_estimate`; also records how far to trust the result."""
        shift, source = self._estimate(bgr, exclude, sent)
        self._sent_hist.append(np.asarray(sent, dtype=float))
        self._shift_hist.append(shift if source == IMAGE else None)
        self._frames += 1
        if self._frames % LAG_RECHECK_EVERY == 0:
            self._update_lag()
        fraction, floor = UNCERTAINTY[source]
        # With no estimate, the unknown motion is what we commanded (taking
        # one pixel per count as a rough guess).
        scale = float(np.linalg.norm(sent)) if source == NONE else float(np.linalg.norm(shift))
        self.uncertainty = fraction * scale + floor
        return shift, source

    def _update_lag(self) -> None:
        """Find the delay, in frames, between a mouse command and its effect.

        The scene shift measured at frame ``t`` should mirror the counts sent
        ``lag`` frames earlier, with opposite sign. Pick the lag where they
        line up best; keep the old answer if nothing lines up convincingly.
        """
        if len(self._sent_hist) < 3 * MAX_LAG or self.gain is None:
            return
        sent = np.array(self._sent_hist)
        measured = [i for i, sh in enumerate(self._shift_hist) if sh is not None]
        if len(measured) < 2 * MAX_LAG:
            return
        shifts = np.array([self._shift_hist[i] for i in measured])
        idx = np.array(measured)

        best_lag, best_r = self.lag, 0.0
        for lag in range(MAX_LAG + 1):
            keep = idx - lag >= 0
            if keep.sum() < MAX_LAG:
                continue
            a = shifts[keep]
            b = sent[idx[keep] - lag]
            denom = float(np.linalg.norm(a) * np.linalg.norm(b))
            if denom < 1e-6:
                continue
            r = -float((a * b).sum()) / denom  # opposite sign => positive r
            if r > best_r:
                best_lag, best_r = lag, r
        if best_r >= LAG_MIN_CORRELATION:
            self.lag = best_lag

    def pending_shift(self) -> np.ndarray:
        """Scene movement still on its way from counts already sent.

        Anything sent in the last ``lag`` frames hasn't reached the screen
        yet, so the offset the aimer sees is out of date by this much. Adding
        it to the offset gives where the target will be once those commands
        land - which is what the next command should be measured against.
        """
        gain = self.gain
        if gain is None or self.lag <= 0 or not self._sent_hist:
            return np.zeros(2)
        recent = list(self._sent_hist)[-self.lag :]
        return -gain * np.sum(recent, axis=0)
