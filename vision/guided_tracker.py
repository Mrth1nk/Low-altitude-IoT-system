"""Bounded horizontal corrections for ArduPilot GUIDED mode."""

from dataclasses import dataclass
from typing import Optional

from .config import TrackerConfig
from .geometry import BodyOffset


@dataclass(frozen=True)
class Correction:
    forward_mps: float
    right_mps: float
    down_mps: float
    frame: str
    timestamp: float


class GuidedTracker:
    def __init__(self, config: TrackerConfig):
        self.config = config
        self._filtered: Optional[BodyOffset] = None
        self._last_velocity = BodyOffset(0.0, 0.0)
        self._last_update: Optional[float] = None

    def update(
        self,
        offset: Optional[BodyOffset],
        *,
        mode: str,
        frame_timestamp: Optional[float],
        now: float,
    ) -> Optional[Correction]:
        if (
            str(mode).strip().upper() != "GUIDED"
            or offset is None
            or frame_timestamp is None
            or now - frame_timestamp > self.config.stale_after_s
        ):
            if offset is None:
                self.reset()
            return None

        filtered = self._low_pass(offset)
        target_forward = self._axis_target(
            filtered.forward_m, self.config.gain_forward
        )
        target_right = self._axis_target(filtered.right_m, self.config.gain_right)
        if self._last_update is None:
            self._last_update = float(now)
            self._last_velocity = BodyOffset(0.0, 0.0)
            return Correction(0.0, 0.0, 0.0, "BODY_NED", float(now))
        dt = max(0.0, now - self._last_update)
        forward = self._accel_limit(
            self._last_velocity.forward_m, target_forward, dt
        )
        right = self._accel_limit(self._last_velocity.right_m, target_right, dt)
        self._last_velocity = BodyOffset(forward, right)
        self._last_update = float(now)
        return Correction(forward, right, 0.0, "BODY_NED", float(now))

    def reset(self) -> None:
        self._filtered = None
        self._last_velocity = BodyOffset(0.0, 0.0)
        self._last_update = None

    def _low_pass(self, offset: BodyOffset) -> BodyOffset:
        if self._filtered is None:
            self._filtered = offset
            return offset
        alpha = self.config.low_pass_alpha
        self._filtered = BodyOffset(
            alpha * offset.forward_m
            + (1.0 - alpha) * self._filtered.forward_m,
            alpha * offset.right_m + (1.0 - alpha) * self._filtered.right_m,
        )
        return self._filtered

    def _axis_target(self, error: float, gain: float) -> float:
        if abs(error) < self.config.deadband_m:
            return 0.0
        value = gain * error
        return max(-self.config.max_speed_mps, min(self.config.max_speed_mps, value))

    def _accel_limit(self, previous: float, target: float, dt: float) -> float:
        max_delta = self.config.max_accel_mps2 * dt
        return max(previous - max_delta, min(previous + max_delta, target))
