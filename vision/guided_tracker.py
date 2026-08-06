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
        self._last_pixel_emit: Optional[float] = None

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

    def update_pixels(
        self,
        *,
        center_x: Optional[float],
        center_y: Optional[float],
        frame_width: int,
        frame_height: int,
        desired_center_x: Optional[float] = None,
        desired_center_y: Optional[float] = None,
        mode: str,
        frame_timestamp: Optional[float],
        now: float,
    ) -> Optional[Correction]:
        if (
            str(mode).strip().upper() != "GUIDED"
            or center_x is None
            or center_y is None
            or frame_timestamp is None
            or now - frame_timestamp > self.config.stale_after_s
            or frame_width <= 0
            or frame_height <= 0
        ):
            self.reset()
            return None

        if (
            self._last_pixel_emit is not None
            and now - self._last_pixel_emit < 1.0 / self.config.pixel_send_hz
        ):
            return None

        target_x = (
            float(frame_width) / 2.0
            if desired_center_x is None
            else float(desired_center_x)
        )
        target_y = (
            float(frame_height) / 2.0
            if desired_center_y is None
            else float(desired_center_y)
        )
        error_x = float(center_x) - target_x
        error_y = float(center_y) - target_y
        normalized_x = error_x / (float(frame_width) / 2.0)
        # FORWARD mount: image top is aircraft-forward, so image Y is inverted.
        normalized_forward = -error_y / (float(frame_height) / 2.0)
        forward = self._pixel_axis(
            error_y,
            normalized_forward,
            self.config.pixel_gain_forward,
        )
        right = self._pixel_axis(
            error_x,
            normalized_x,
            self.config.pixel_gain_right,
        )
        self._last_pixel_emit = float(now)
        return Correction(forward, right, 0.0, "BODY_NED", float(now))

    def reset(self) -> None:
        self._filtered = None
        self._last_velocity = BodyOffset(0.0, 0.0)
        self._last_update = None
        self._last_pixel_emit = None

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

    def _pixel_axis(
        self,
        error_pixels: float,
        normalized_error: float,
        gain: float,
    ) -> float:
        if abs(error_pixels) <= self.config.pixel_deadzone:
            return 0.0
        value = gain * normalized_error
        return max(
            -self.config.pixel_max_speed_mps,
            min(self.config.pixel_max_speed_mps, value),
        )
