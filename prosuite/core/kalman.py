"""Constant-velocity Kalman filter used to estimate target motion.

State is ``[x, y, vx, vy]`` with positions in pixels and velocities in pixels
per second. Only position is measured; velocity is inferred from how the
position changes over *real* elapsed time, so the estimate does not depend on
the capture loop's frame rate.
"""

from __future__ import annotations

import numpy as np


class KalmanFilter:
    #: Measurement matrix - we observe position only.
    H = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])

    #: Std-dev of target acceleration the model should tolerate, px/s^2.
    #: Higher follows manoeuvres faster but passes more noise into velocity.
    ACCEL_SIGMA = 2500.0
    #: Std-dev of the colour-blob centroid jitter, in pixels.
    MEASUREMENT_SIGMA = 2.0
    #: Velocity std-dev (px/s) at which confidence has fallen to one half.
    VELOCITY_SCALE = 150.0
    #: Updates needed before the velocity estimate is trusted at all.
    WARMUP_UPDATES = 4

    def __init__(self) -> None:
        self.R = np.eye(2) * self.MEASUREMENT_SIGMA**2
        self._identity = np.eye(4)
        self.reset()

    def reset(self) -> None:
        """Return the filter to its uninitialised state."""
        self.state = np.zeros(4)
        self.P = np.eye(4)
        self.initialized = False
        self.updates = 0

    # --- model ---
    @staticmethod
    def _transition(dt: float) -> np.ndarray:
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        return F

    def _process_noise(self, dt: float) -> np.ndarray:
        """White-noise-acceleration covariance for a step of ``dt`` seconds."""
        var = self.ACCEL_SIGMA**2
        pos, cross, vel = dt**4 / 4.0, dt**3 / 2.0, dt**2
        Q = np.zeros((4, 4))
        for axis in (0, 1):
            Q[axis, axis] = pos * var
            Q[axis, axis + 2] = Q[axis + 2, axis] = cross * var
            Q[axis + 2, axis + 2] = vel * var
        return Q

    # --- filtering ---
    def predict(self, dt: float) -> np.ndarray:
        """Advance the state by ``dt`` seconds and return the position."""
        if not self.initialized or dt <= 0:
            return self.state[:2]
        F = self._transition(dt)
        self.state = F @ self.state
        self.P = F @ self.P @ F.T + self._process_noise(dt)
        return self.state[:2]

    def update(
        self, measurement: np.ndarray, dt: float = 1.0 / 60.0, extra_sigma: float = 0.0
    ) -> None:
        """Fold in a position observed ``dt`` seconds after the last one.

        ``extra_sigma`` is additional pixel uncertainty for *this* reading
        only. Camera motion has to be subtracted to get a world position, and
        that subtraction is only as good as the motion estimate - so the faster
        the view moved, the less this reading should be believed.
        """
        if not self.initialized:
            self.state[:2] = measurement
            self.state[2:] = 0.0
            # Position is known to within the sensor noise; velocity is not
            # known at all, so start with a deliberately huge variance.
            self.P = np.diag(
                [self.MEASUREMENT_SIGMA**2] * 2 + [self.VELOCITY_SCALE**2 * 100] * 2
            )
            self.initialized = True
            self.updates = 1
            return

        self.predict(dt)

        R = self.R + np.eye(2) * extra_sigma**2
        S = self.H @ self.P @ self.H.T + R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.state = self.state + K @ (measurement - self.H @ self.state)
        # Joseph form keeps P symmetric and positive-definite over long runs.
        IKH = self._identity - K @ self.H
        self.P = IKH @ self.P @ IKH.T + K @ R @ K.T
        self.updates += 1

    # --- readouts ---
    def get_velocity(self) -> np.ndarray:
        """Velocity in pixels per second."""
        return self.state[2:4] if self.initialized else np.zeros(2)

    def get_position(self) -> np.ndarray:
        return self.state[:2] if self.initialized else np.zeros(2)

    def get_prediction_confidence(self) -> float:
        """0 to 1: how much the velocity estimate deserves to be believed.

        Driven by the filter's own velocity variance, so it falls when the
        target manoeuvres and rises again as the track settles. Zero until
        enough updates have arrived to say anything.
        """
        if not self.initialized or self.updates < self.WARMUP_UPDATES:
            return 0.0
        vel_sigma = float(np.sqrt(max(self.P[2, 2] + self.P[3, 3], 0.0)))
        return 1.0 / (1.0 + (vel_sigma / self.VELOCITY_SCALE) ** 2)
