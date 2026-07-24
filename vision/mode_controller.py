"""Pure mode gate joining detection, optical state and GUIDED tracking."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import CameraConfig
from .detector import BrightSpotDetector, Detection
from .geometry import detection_to_body_offset
from .guided_tracker import Correction, GuidedTracker
from .optical_state import OpticalSnapshot, OpticalStateMachine


@dataclass(frozen=True)
class ControllerOutput:
    detection: Optional[Detection]
    optical: OpticalSnapshot
    correction: Optional[Correction]
    rc_takeover: bool
    requested_mode: None = None


class VisionModeController:
    def __init__(
        self,
        *,
        detector: BrightSpotDetector,
        tracker: GuidedTracker,
        optical: OpticalStateMachine,
        camera: CameraConfig,
    ):
        self.detector = detector
        self.tracker = tracker
        self.optical = optical
        self.camera = camera

    def process(
        self,
        gray: np.ndarray,
        *,
        mode: str,
        altitude_m: float,
        timestamp: float,
        now: float,
    ) -> ControllerOutput:
        detection = self.detector.detect(gray, timestamp=timestamp)
        fresh = (
            detection is not None
            and now - detection.timestamp <= self.tracker.config.stale_after_s
        )
        effective_detection = detection if fresh else None
        optical = self.optical.update(effective_detection, timestamp=timestamp)
        correction = None
        if effective_detection is not None and optical.locked:
            offset = detection_to_body_offset(
                effective_detection.center_x,
                effective_detection.center_y,
                altitude_m=altitude_m,
                camera=self.camera,
            )
            correction = self.tracker.update(
                offset,
                mode=mode,
                frame_timestamp=effective_detection.timestamp,
                now=now,
            )
        else:
            self.tracker.update(
                None, mode=mode, frame_timestamp=None, now=now
            )
        return ControllerOutput(
            detection=effective_detection,
            optical=optical,
            correction=correction,
            rc_takeover=optical.blocked,
        )
