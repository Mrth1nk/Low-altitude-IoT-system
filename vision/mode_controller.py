"""Pure mode gate joining detection, optical state and GUIDED tracking."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import CameraConfig
from .detector import BrightSpotDetector, Detection
from .geometry import detection_to_body_angles, detection_to_body_offset
from .guided_tracker import Correction, GuidedTracker
from .optical_state import OpticalSnapshot, OpticalStateMachine
from .precision_landing import (
    LANDING_MODES,
    LandingTarget,
    PrecisionLandingConfig,
    PrecisionLandingController,
)


@dataclass(frozen=True)
class ControllerOutput:
    detection: Optional[Detection]
    optical: OpticalSnapshot
    correction: Optional[Correction]
    rc_takeover: bool
    landing_target: Optional[LandingTarget] = None
    final_descent_active: bool = False
    requested_mode: None = None


class VisionModeController:
    def __init__(
        self,
        *,
        detector: BrightSpotDetector,
        tracker: GuidedTracker,
        optical: OpticalStateMachine,
        camera: CameraConfig,
        precision_landing: Optional[PrecisionLandingController] = None,
        final_descent_altitude_m: float = 0.25,
    ):
        if final_descent_altitude_m <= 0:
            raise ValueError("final_descent_altitude_m must be positive")
        self.detector = detector
        self.tracker = tracker
        self.optical = optical
        self.camera = camera
        self.final_descent_altitude_m = float(final_descent_altitude_m)
        self._final_descent_active = False
        self.precision_landing = precision_landing or PrecisionLandingController(
            PrecisionLandingConfig()
        )

    @property
    def final_descent_active(self) -> bool:
        return self._final_descent_active

    def process(
        self,
        gray: np.ndarray,
        *,
        mode: str,
        altitude_m: float,
        timestamp: float,
        now: float,
        altitude_source: str = "unknown",
    ) -> ControllerOutput:
        detection = self.detector.detect(gray, timestamp=timestamp)
        fresh = (
            detection is not None
            and now - detection.timestamp <= self.tracker.config.stale_after_s
        )
        effective_detection = detection if fresh else None
        optical = self.optical.update(effective_detection, timestamp=timestamp)
        correction = None
        landing_target = None
        offset = None
        if effective_detection is not None and altitude_m > 0:
            offset = detection_to_body_offset(
                effective_detection.center_x,
                effective_detection.center_y,
                altitude_m=altitude_m,
                camera=self.camera,
            )
        normalized_mode = mode.strip().upper()
        if normalized_mode not in LANDING_MODES:
            self._final_descent_active = False
        elif (
            not self._final_descent_active
            and altitude_m > 0
            and bool(altitude_source.strip())
            and altitude_source.strip().lower() != "unknown"
            and altitude_m <= self.final_descent_altitude_m
        ):
            self._final_descent_active = True
        if (
            normalized_mode == "GUIDED"
            and effective_detection is not None
            and optical.locked
        ):
            frame_width = int(gray.shape[1])
            frame_height = int(gray.shape[0])
            desired_center_x = float(frame_width) / 2.0
            desired_center_y = float(frame_height) / 2.0
            if altitude_m > 0:
                scaled_fx = self.camera.fx * frame_width / self.camera.width
                scaled_fy = self.camera.fy * frame_height / self.camera.height
                # With the aircraft origin over the beacon, a forward/right
                # displaced camera sees the beacon behind/left of image center.
                desired_center_x -= (
                    self.camera.offset_right_m / altitude_m * scaled_fx
                )
                desired_center_y += (
                    self.camera.offset_forward_m / altitude_m * scaled_fy
                )
                desired_center_x = max(0.0, min(float(frame_width), desired_center_x))
                desired_center_y = max(0.0, min(float(frame_height), desired_center_y))
            correction = self.tracker.update_pixels(
                center_x=effective_detection.center_x,
                center_y=effective_detection.center_y,
                frame_width=frame_width,
                frame_height=frame_height,
                desired_center_x=desired_center_x,
                desired_center_y=desired_center_y,
                mode=mode,
                frame_timestamp=effective_detection.timestamp,
                now=now,
            )
        else:
            self.tracker.update(
                None, mode=mode, frame_timestamp=None, now=now
            )
        angles = (
            detection_to_body_angles(
                effective_detection.center_x,
                effective_detection.center_y,
                camera=self.camera,
                altitude_m=altitude_m,
            )
            if effective_detection is not None
            else None
        )
        if not self._final_descent_active:
            landing_target = self.precision_landing.update(
                offset,
                detection=effective_detection,
                mode=normalized_mode,
                altitude_m=altitude_m,
                altitude_source=altitude_source,
                frame_timestamp=(
                    effective_detection.timestamp
                    if effective_detection is not None
                    else None
                ),
                now=now,
                angles=angles,
                distance_m=(
                    None
                    if altitude_m > 0
                    else 0.0
                ),
            )
        return ControllerOutput(
            detection=effective_detection,
            optical=optical,
            correction=correction,
            rc_takeover=optical.blocked,
            landing_target=landing_target,
            final_descent_active=self._final_descent_active,
        )
