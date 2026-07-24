"""Validated configuration for infrared tracking."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CameraConfig:
    width: int = 640
    height: int = 480
    fx: float = 460.0
    fy: float = 460.0
    cx: float = 320.0
    cy: float = 240.0
    orientation: str = "FORWARD"
    offset_forward_m: float = 0.04
    offset_right_m: float = 0.01

    def __post_init__(self):
        if self.orientation.strip().upper() != "FORWARD":
            raise ValueError("Task 7 requires camera orientation FORWARD")
        if self.fx <= 0 or self.fy <= 0:
            raise ValueError("camera focal lengths must be positive")


@dataclass(frozen=True)
class DetectorConfig:
    threshold: int = 235
    min_area: float = 8.0
    max_area: float = 12000.0
    min_circularity: float = 0.20
    min_brightness: float = 180.0
    blur_size: int = 5
    morph_kernel: int = 3
    auto_threshold: bool = False
    auto_percentile: float = 99.7
    auto_margin: int = 8

    def __post_init__(self):
        if not 0 <= self.threshold <= 255:
            raise ValueError("threshold must be in [0, 255]")
        if self.min_area <= 0 or self.max_area <= self.min_area:
            raise ValueError("invalid detector area bounds")
        if not 0 <= self.min_circularity <= 1:
            raise ValueError("min_circularity must be in [0, 1]")


@dataclass(frozen=True)
class TrackerConfig:
    low_pass_alpha: float = 0.65
    deadband_m: float = 0.03
    gain_forward: float = 0.45
    gain_right: float = 0.45
    max_speed_mps: float = 0.6
    max_accel_mps2: float = 0.8
    stale_after_s: float = 0.20

    def __post_init__(self):
        if not 0 < self.low_pass_alpha <= 1:
            raise ValueError("low_pass_alpha must be in (0, 1]")
        if min(
            self.deadband_m,
            self.max_speed_mps,
            self.max_accel_mps2,
            self.stale_after_s,
        ) < 0:
            raise ValueError("tracker limits must be non-negative")


@dataclass(frozen=True)
class OpticalConfig:
    acquire_count: int = 3
    loss_count: int = 1

    def __post_init__(self):
        if self.acquire_count < 1 or self.loss_count < 1:
            raise ValueError("optical counters must be positive")
