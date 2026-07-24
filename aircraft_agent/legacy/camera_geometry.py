"""Camera-image to aircraft-body coordinate transforms."""

from typing import Iterable, Tuple


def camera_to_body_offsets(
    tx: float,
    ty: float,
    orientation: str,
    camera_offset_x: float = 0.0,
    camera_offset_y: float = 0.0,
) -> Tuple[float, float]:
    """Return target offset as aircraft-forward and aircraft-right metres."""
    normalized = orientation.strip().upper()
    if normalized == "FORWARD":
        forward = -ty
        right = tx
    elif normalized == "BACKWARD":
        forward = ty
        right = -tx
    elif normalized == "LEFT":
        forward = tx
        right = ty
    elif normalized == "RIGHT":
        forward = -tx
        right = -ty
    else:
        raise ValueError(f"Unsupported camera orientation: {orientation!r}")
    return forward - camera_offset_x, right - camera_offset_y


def control_mode_for_flight_mode(
    configured_mode: str,
    flight_mode: str,
    precision_land_modes: Iterable[str],
) -> str:
    """Switch to LANDING_TARGET only while the autopilot is landing."""
    normalized_mode = (flight_mode or "").strip().upper()
    precision_modes = {mode.strip().upper() for mode in precision_land_modes}
    if normalized_mode in precision_modes:
        return "LANDING_TARGET"
    return (configured_mode or "GUIDED_VELOCITY").strip().upper()
