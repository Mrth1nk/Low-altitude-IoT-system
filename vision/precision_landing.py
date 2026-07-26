"""Angle-only ArduPilot precision-landing targets for PLND_EST_TYPE=0."""

from dataclasses import dataclass
import math
from typing import Optional, Tuple

from .detector import Detection
from .geometry import BodyAngles, BodyOffset


MAV_FRAME_BODY_FRD = 12
LANDING_MODES = frozenset(("LAND", "QLAND"))


@dataclass(frozen=True)
class PrecisionLandingConfig:
    acquire_count: int = 3
    loss_count: int = 2
    send_hz: float = 10.0
    stale_after_s: float = 0.20
    min_altitude_m: float = 0.05
    plnd_est_type: int = 0

    def __post_init__(self):
        if self.acquire_count < 1 or self.loss_count < 1:
            raise ValueError("precision-landing counters must be positive")
        if self.send_hz <= 0 or self.stale_after_s < 0:
            raise ValueError("precision-landing timing must be positive")
        if self.min_altitude_m <= 0:
            raise ValueError("min_altitude_m must be positive")
        if self.plnd_est_type != 0:
            raise ValueError("Task 8 supports PLND_EST_TYPE=0 only")


@dataclass(frozen=True)
class LandingTarget:
    time_usec: int
    frame: int
    angle_x: float
    angle_y: float
    distance: float
    position_valid: int
    x: float
    y: float
    z: float
    q: Tuple[float, float, float, float]
    target_type: int
    altitude_m: float
    altitude_source: str
    confidence: float
    horizontal_error_m: float
    frequency_hz: float


@dataclass(frozen=True)
class PrecisionLandingSnapshot:
    acquired: bool
    acquire_count: int
    loss_count: int
    last_angle_x: Optional[float]
    last_angle_y: Optional[float]
    altitude_m: Optional[float]
    altitude_source: str
    confidence: float
    horizontal_error_m: Optional[float]
    frequency_hz: float


class PrecisionLandingController:
    def __init__(self, config: PrecisionLandingConfig):
        self.config = config
        self._acquire_count = 0
        self._loss_count = 0
        self._acquired = False
        self._last_emit: Optional[float] = None
        self._snapshot = PrecisionLandingSnapshot(
            acquired=False,
            acquire_count=0,
            loss_count=0,
            last_angle_x=None,
            last_angle_y=None,
            altitude_m=None,
            altitude_source="unknown",
            confidence=0.0,
            horizontal_error_m=None,
            frequency_hz=0.0,
        )

    @property
    def snapshot(self) -> PrecisionLandingSnapshot:
        return self._snapshot

    def update(
        self,
        offset: Optional[BodyOffset],
        *,
        detection: Optional[Detection],
        mode: str,
        altitude_m: float,
        altitude_source: str,
        frame_timestamp: Optional[float],
        now: float,
        angles: Optional[BodyAngles] = None,
        distance_m: Optional[float] = None,
    ) -> Optional[LandingTarget]:
        if mode.strip().upper() not in LANDING_MODES:
            self.reset()
            return None

        valid = (
            detection is not None
            and frame_timestamp is not None
            and 0.0 <= now - frame_timestamp <= self.config.stale_after_s
            and (
                angles is not None
                or (
                    offset is not None
                    and altitude_m >= self.config.min_altitude_m
                    and bool(altitude_source.strip())
                    and altitude_source.strip().lower() != "unknown"
                )
            )
        )
        if not valid:
            self._record_loss(altitude_source=altitude_source)
            return None

        self._loss_count = 0
        self._acquire_count += 1
        if self._acquire_count >= self.config.acquire_count:
            self._acquired = True

        if angles is None:
            angle_x = math.atan2(offset.right_m, altitude_m)
            angle_y = math.atan2(-offset.forward_m, altitude_m)
        else:
            angle_x = angles.angle_x
            angle_y = angles.angle_y
        horizontal_error = (
            math.hypot(offset.forward_m, offset.right_m)
            if offset is not None
            else 0.0
        )
        frequency_hz = 0.0
        if self._last_emit is not None:
            elapsed = now - self._last_emit
            if elapsed < 1.0 / self.config.send_hz:
                self._set_snapshot(
                    angle_x,
                    angle_y,
                    altitude_m,
                    altitude_source,
                    detection.confidence,
                    horizontal_error,
                    self._snapshot.frequency_hz,
                )
                return None
            frequency_hz = 1.0 / elapsed if elapsed > 0 else 0.0

        self._set_snapshot(
            angle_x,
            angle_y,
            altitude_m,
            altitude_source,
            detection.confidence,
            horizontal_error,
            frequency_hz,
        )
        if not self._acquired:
            return None

        self._last_emit = now
        return LandingTarget(
            time_usec=int(now * 1_000_000),
            frame=MAV_FRAME_BODY_FRD,
            angle_x=angle_x,
            angle_y=angle_y,
            distance=(
                float(distance_m)
                if distance_m is not None
                else math.sqrt(altitude_m**2 + horizontal_error**2)
            ),
            position_valid=0,
            x=0.0,
            y=0.0,
            z=0.0,
            q=(0.0, 0.0, 0.0, 0.0),
            target_type=0,
            altitude_m=float(altitude_m),
            altitude_source=altitude_source,
            confidence=detection.confidence,
            horizontal_error_m=horizontal_error,
            frequency_hz=frequency_hz,
        )

    def reset(self):
        self._acquire_count = 0
        self._loss_count = 0
        self._acquired = False
        self._last_emit = None
        self._snapshot = PrecisionLandingSnapshot(
            acquired=False,
            acquire_count=0,
            loss_count=0,
            last_angle_x=None,
            last_angle_y=None,
            altitude_m=None,
            altitude_source="unknown",
            confidence=0.0,
            horizontal_error_m=None,
            frequency_hz=0.0,
        )

    def _record_loss(self, *, altitude_source: str):
        self._loss_count += 1
        if self._loss_count >= self.config.loss_count:
            self._acquired = False
            self._acquire_count = 0
        self._snapshot = PrecisionLandingSnapshot(
            acquired=self._acquired,
            acquire_count=self._acquire_count,
            loss_count=self._loss_count,
            last_angle_x=None,
            last_angle_y=None,
            altitude_m=None,
            altitude_source=altitude_source or "unknown",
            confidence=0.0,
            horizontal_error_m=None,
            frequency_hz=0.0,
        )

    def _set_snapshot(
        self,
        angle_x: float,
        angle_y: float,
        altitude_m: float,
        altitude_source: str,
        confidence: float,
        horizontal_error_m: float,
        frequency_hz: float,
    ):
        self._snapshot = PrecisionLandingSnapshot(
            acquired=self._acquired,
            acquire_count=self._acquire_count,
            loss_count=self._loss_count,
            last_angle_x=angle_x,
            last_angle_y=angle_y,
            altitude_m=float(altitude_m),
            altitude_source=altitude_source,
            confidence=float(confidence),
            horizontal_error_m=horizontal_error_m,
            frequency_hz=frequency_hz,
        )


def send_landing_target(mav, target: LandingTarget):
    """Send one MAVLink 2 LANDING_TARGET without claiming position validity."""
    mav.landing_target_send(
        target.time_usec,
        0,
        target.frame,
        target.angle_x,
        target.angle_y,
        target.distance,
        0.0,
        0.0,
        target.x,
        target.y,
        target.z,
        target.q,
        target.target_type,
        target.position_valid,
    )
