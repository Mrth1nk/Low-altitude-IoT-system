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
    ):
        self.detector = detector
        self.tracker = tracker
        self.optical = optical
        self.camera = camera
        self.precision_landing = precision_landing or PrecisionLandingController(
            PrecisionLandingConfig()
        )

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
        if (
            normalized_mode == "GUIDED"
            and effective_detection is not None
            and optical.locked
        ):
            correction = self.tracker.update_pixels(
                center_x=effective_detection.center_x,
                center_y=effective_detection.center_y,
                frame_width=int(gray.shape[1]),
                frame_height=int(gray.shape[0]),
                mode=mode,
                frame_timestamp=effective_detection.timestamp,
                now=now,
            )
        else:
            self.tracker.update(
                None, mode=mode, frame_timestamp=None, now=now
            )
        if normalized_mode in LANDING_MODES:
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
        else:
            self.precision_landing.update(
                None,
                detection=None,
                mode=normalized_mode,
                altitude_m=altitude_m,
                altitude_source=altitude_source,
                frame_timestamp=None,
                now=now,
            )
        return ControllerOutput(
            detection=effective_detection,
            optical=optical,
            correction=correction,
            rc_takeover=optical.blocked,
            landing_target=landing_target,
        )
