"""Practice-session statistics: how often a target is found, locked and held.

Pure Python with an injected clock, so it can be tested without a GUI or a
real clock. Fed once per detection result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: A target within this many pixels of the crosshair counts as locked on.
LOCK_RADIUS_PX = 30.0
#: A target must be gone this long before its return counts as a new one,
#: so a flickering blob isn't scored as a dozen separate targets.
REACQUIRE_GAP_S = 0.35
#: Longest single time step credited, so a stall doesn't inflate the totals.
MAX_STEP_S = 0.1


@dataclass(frozen=True)
class SessionSummary:
    scan_seconds: float
    targets: int
    locks: int
    #: Fraction (0-1) of target-visible time spent locked on, or None.
    on_target: float | None
    mean_lock_s: float | None
    best_lock_s: float | None
    shots: int


class SessionStats:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last_time: float | None = None
        self._scan = 0.0
        self._visible = 0.0
        self._locked = 0.0
        self._targets = 0
        self._lock_times: list[float] = []
        #: Targets that first appeared already under the crosshair: a lock, but
        #: not an aiming time worth recording.
        self._instant_locks = 0
        self._pending_since: float | None = None  # acquired, not yet locked
        self._gone_since: float | None = None
        self._had_target = False
        self._shots_base: int | None = None
        self._shots = 0

    def update(
        self,
        now: float,
        scanning: bool,
        target_distance: float | None,
        shots_total: int = 0,
    ) -> None:
        """Record one frame.

        ``target_distance`` is the target's distance from the crosshair in
        pixels, or ``None`` when there is no target. ``shots_total`` is the
        worker's running click count.
        """
        step = 0.0
        if self._last_time is not None:
            step = min(max(now - self._last_time, 0.0), MAX_STEP_S)
        self._last_time = now

        if self._shots_base is None:
            self._shots_base = shots_total
        self._shots = max(0, shots_total - self._shots_base)

        if not scanning:
            self._pending_since = None
            self._had_target = False
            self._gone_since = None
            return
        self._scan += step

        if target_distance is None:
            if self._had_target and self._gone_since is None:
                self._gone_since = now
            elif self._gone_since is not None and now - self._gone_since > REACQUIRE_GAP_S:
                self._had_target = False
                self._pending_since = None
            return

        # A target is on screen.
        self._visible += step
        if not self._had_target:
            self._had_target = True
            self._targets += 1
            self._pending_since = now
            if target_distance <= LOCK_RADIUS_PX:
                self._instant_locks += 1
                self._pending_since = None
        self._gone_since = None

        if target_distance <= LOCK_RADIUS_PX:
            self._locked += step
            if self._pending_since is not None:
                self._lock_times.append(now - self._pending_since)
                self._pending_since = None

    def summary(self) -> SessionSummary:
        locks = self._lock_times
        return SessionSummary(
            scan_seconds=self._scan,
            targets=self._targets,
            locks=len(locks) + self._instant_locks,
            on_target=(self._locked / self._visible) if self._visible > 0 else None,
            mean_lock_s=(sum(locks) / len(locks)) if locks else None,
            best_lock_s=min(locks) if locks else None,
            shots=self._shots,
        )


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
