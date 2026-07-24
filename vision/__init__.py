"""Infrared beacon detection and GUIDED tracking primitives."""

from .config import CameraConfig, DetectorConfig, OpticalConfig, TrackerConfig
from .detector import BrightSpotDetector, Detection
from .guided_tracker import Correction, GuidedTracker
from .mode_controller import ControllerOutput, VisionModeController

__all__ = [
    "BrightSpotDetector",
    "CameraConfig",
    "ControllerOutput",
    "Correction",
    "Detection",
    "DetectorConfig",
    "GuidedTracker",
    "OpticalConfig",
    "TrackerConfig",
    "VisionModeController",
]
