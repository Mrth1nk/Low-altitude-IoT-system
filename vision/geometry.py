"""Camera-pixel to aircraft-body geometry for the forward-mounted camera."""

from dataclasses import dataclass
import math

from .config import CameraConfig


@dataclass(frozen=True)
class BodyOffset:
    forward_m: float
    right_m: float


@dataclass(frozen=True)
class BodyAngles:
    angle_x: float
    angle_y: float


def detection_to_body_offset(
    center_x: float,
    center_y: float,
    *,
    altitude_m: float,
    camera: CameraConfig,
) -> BodyOffset:
    if altitude_m <= 0:
        raise ValueError("altitude_m must be positive")
    camera_right = (float(center_x) - camera.cx) / camera.fx * altitude_m
    camera_down_image = (float(center_y) - camera.cy) / camera.fy * altitude_m
    forward = -camera_down_image - camera.offset_forward_m
    right = camera_right - camera.offset_right_m
    return BodyOffset(float(forward), float(right))


def detection_to_body_angles(
    center_x: float,
    center_y: float,
    *,
    camera: CameraConfig,
    altitude_m: float = 0.0,
) -> BodyAngles:
    right_ray = (float(center_x) - camera.cx) / camera.fx
    forward_ray = -(float(center_y) - camera.cy) / camera.fy
    if altitude_m > 0:
        forward_ray -= camera.offset_forward_m / altitude_m
        right_ray -= camera.offset_right_m / altitude_m
    return BodyAngles(
        angle_x=math.atan2(right_ray, 1.0),
        angle_y=math.atan2(-forward_ray, 1.0),
    )
