"""Constant-velocity Kalman filter used to estimate target motion."""

from __future__ import annotations

import numpy as np


class KalmanFilter:
    """2D position/velocity filter over the state ``[x, y, vx, vy]``.

    Only position is measured; velocity is inferred. The numeric constants and
    update rules are unchanged from the original implementation so tracking
    behaviour is identical.
    """

    #: Measurement matrix - we observe position only.
    H = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])

    #: Frame-to-frame movement below this is treated as noise, not velocity.
    VELOCITY_DEADZONE = 0.5
    #: Exponential smoothing weight applied to velocity updates.
    VELOCITY_SMOOTHING = 0.8

    def __init__(self) -> None:
        self.Q = np.eye(4) * 0.2       # process noise
        self.R = np.eye(2) * 0.2       # measurement noise
        self._identity = np.eye(4)
        self.reset()

    def reset(self) -> None:
        """Return the filter to its uninitialised state."""
        self.state = np.zeros(4)
        self.P = np.eye(4) * 2.0
        self.initialized = False
        self._last_measurement: np.ndarray | None = None
        self._velocity_initialized = False

    @staticmethod
    def _transition(dt: float) -> np.ndarray:
        return np.array(
            [
                [1.0, 0.0, dt, 0.0],
                [0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )

    def predict(self, dt: float = 1.0) -> np.ndarray:
        """Advance the state by ``dt`` and return the predicted position."""
        if not self.initialized:
            return self.state[:2]

        F = self._transition(dt)
        self.state = F @ self.state
        self.P = F @ self.P @ F.T + self.Q
        return self.state[:2]

    def update(self, measurement: np.ndarray) -> None:
        """Fold a new position observation into the state estimate."""
        if not self.initialized:
            self.state[:2] = measurement
            self.initialized = True
            self._last_measurement = measurement.copy()
            return

        if self._last_measurement is not None:
            self._seed_velocity(measurement - self._last_measurement)
        self._last_measurement = measurement.copy()

        # Kalman gain. solve() is used instead of inv() for stability and speed.
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.solve(S, np.eye(S.shape[0]))

        residual = measurement - self.H @ self.state
        self.state = self.state + K @ residual
        self.P = (self._identity - K @ self.H) @ self.P

    def _seed_velocity(self, velocity: np.ndarray) -> None:
        """Nudge the velocity component using raw frame-to-frame displacement.

        Screen-space targets in a 3D scene jump around, so tiny displacements
        are ignored and larger ones are blended in rather than applied directly.
        """
        if np.linalg.norm(velocity) <= self.VELOCITY_DEADZONE:
            return

        if not self._velocity_initialized:
            self.state[2:4] = velocity
            self._velocity_initialized = True
        else:
            w = self.VELOCITY_SMOOTHING
            self.state[2:4] = self.state[2:4] * w + velocity * (1.0 - w)

    def get_velocity(self) -> np.ndarray:
        return self.state[2:4] if self.initialized else np.zeros(2)

    def get_prediction_confidence(self) -> float:
        """Confidence in [0, 1) derived from positional covariance."""
        if not self.initialized:
            return 0.0
        return 1.0 / (1.0 + np.trace(self.P[:2, :2]))

    def handle_target_loss(self) -> None:
        """Widen positional uncertainty after the target disappears."""
        if self.initialized:
            self.P[:2, :2] *= 2.0
