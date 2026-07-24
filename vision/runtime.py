"""Camera and vision-MAVLink runtime, isolated from aircraft link ownership."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import threading
import time

from aircraft_agent.state_store import AtomicJsonStore
from .config import CameraConfig, DetectorConfig, OpticalConfig, TrackerConfig
from .detector import BrightSpotDetector
from .guided_tracker import GuidedTracker
from .mode_controller import VisionModeController
from .optical_state import OpticalStateMachine
from .precision_landing import send_landing_target


class OpticalStatePublisher:
    def __init__(self, store):
        self.store = store

    def publish(
        self,
        *,
        locked,
        confidence,
        area,
        mode,
        timestamp,
        last_error,
    ):
        self.store.save(
            {
                "locked": bool(locked),
                "blocked": not bool(locked),
                "confidence": float(confidence),
                "area": float(area),
                "mode": str(mode),
                "timestamp": float(timestamp),
                "last_error": str(last_error or "")[:256],
            }
        )


def _send_guided_velocity(master, correction):
    mavlink = master.mav
    mavlink.set_position_target_local_ned_send(
        int(correction.timestamp * 1000) & 0xFFFFFFFF,
        master.target_system or 1,
        master.target_component or 1,
        8,  # MAV_FRAME_BODY_NED
        3527,  # velocity only; ignore position, acceleration, yaw and yaw rate
        0.0,
        0.0,
        0.0,
        correction.forward_mps,
        correction.right_mps,
        correction.down_mps,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )


def run():
    import cv2
    from pymavlink import mavutil

    runtime_dir = Path(os.environ.get("AIRCRAFT_RUNTIME_DIR", "/run/low-altitude-iot"))
    serial_path = os.environ.get("VISION_MAV_DEVICE", "/dev/ttyS9")
    camera_device = os.environ.get("VISION_CAMERA_DEVICE", "/dev/video0")
    master = mavutil.mavlink_connection(
        serial_path,
        baud=int(os.environ.get("VISION_MAV_BAUD", "115200")),
        autoreconnect=False,
        source_system=254,
    )
    master.wait_heartbeat(timeout=10)
    camera = cv2.VideoCapture(camera_device)
    if not camera.isOpened():
        raise RuntimeError(f"camera unavailable: {camera_device}")

    controller = VisionModeController(
        detector=BrightSpotDetector(DetectorConfig()),
        tracker=GuidedTracker(TrackerConfig()),
        optical=OpticalStateMachine(OpticalConfig()),
        camera=CameraConfig(),
    )
    publisher = OpticalStatePublisher(
        AtomicJsonStore(runtime_dir / "optical-state.json", max_bytes=64 * 1024)
    )
    stop = threading.Event()
    mode = "UNKNOWN"
    altitude_m = 0.0
    altitude_source = "unknown"

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop.is_set():
            message = master.recv_match(blocking=False)
            while message is not None:
                kind = message.get_type()
                if kind == "HEARTBEAT":
                    mode = mavutil.mode_string_v10(message)
                elif kind == "DISTANCE_SENSOR" and int(message.current) > 0:
                    altitude_m = float(message.current) / 100.0
                    altitude_source = "rangefinder"
                elif kind == "GLOBAL_POSITION_INT" and altitude_source == "unknown":
                    altitude_m = max(0.0, float(message.relative_alt) / 1000.0)
                    altitude_source = "relative_altitude"
                message = master.recv_match(blocking=False)

            ok, frame = camera.read()
            wall_timestamp = time.time()
            if not ok:
                publisher.publish(
                    locked=False,
                    confidence=0.0,
                    area=0.0,
                    mode=mode,
                    timestamp=wall_timestamp,
                    last_error="camera_frame_failed",
                )
                time.sleep(0.02)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            now = time.monotonic()
            output = controller.process(
                gray,
                mode=mode,
                altitude_m=altitude_m,
                altitude_source=altitude_source,
                timestamp=now,
                now=now,
            )
            if output.correction is not None:
                _send_guided_velocity(master, output.correction)
            if output.landing_target is not None:
                send_landing_target(master.mav, output.landing_target)
            publisher.publish(
                locked=output.optical.locked,
                confidence=output.optical.confidence,
                area=output.optical.area,
                mode=mode,
                timestamp=wall_timestamp,
                last_error="" if output.optical.locked else "optical_blocked",
            )
    finally:
        publisher.publish(
            locked=False,
            confidence=0.0,
            area=0.0,
            mode=mode,
            timestamp=time.time(),
            last_error="vision_stopped",
        )
        camera.release()
        master.close()


if __name__ == "__main__":
    run()
