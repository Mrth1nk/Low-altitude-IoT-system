"""Camera and vision-MAVLink runtime, isolated from aircraft link ownership."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aircraft_agent.state_store import AtomicJsonStore
from .config import CameraConfig, DetectorConfig, OpticalConfig, TrackerConfig
from .detector import BrightSpotDetector
from .guided_tracker import GuidedTracker
from .mode_controller import VisionModeController
from .optical_state import OpticalStateMachine
from .precision_landing import send_landing_target


def reopen_camera(capture_factory, camera_device, previous=None):
    if previous is not None:
        previous.release()
    camera = capture_factory(camera_device)
    if camera.isOpened():
        return camera
    camera.release()
    return None


class PreviewServer:
    def __init__(self, host, port):
        self.latest = None
        latest = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                if self.path not in ("/", "/stream"):
                    self.send_error(404)
                    return
                if self.path == "/":
                    body = b'<html><body style="margin:0;background:#111"><img src="/stream" style="max-width:100vw"></body></html>'
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        jpeg = latest.latest
                        if jpeg:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                            self.wfile.write(jpeg + b"\r\n")
                            self.wfile.flush()
                        time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer((host, int(port)), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def update(self, jpeg):
        self.latest = jpeg

    def close(self):
        self.server.shutdown()
        self.server.server_close()


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
    camera = reopen_camera(cv2.VideoCapture, camera_device)
    if camera is None:
        raise RuntimeError(f"camera unavailable: {camera_device}")
    preview = PreviewServer(
        os.environ.get("VISION_STREAM_HOST", "0.0.0.0"),
        int(os.environ.get("VISION_STREAM_PORT", "8090")),
    )

    detector_config = DetectorConfig(
        threshold=int(os.environ.get("IR_THRESHOLD", "235")),
        min_area=float(os.environ.get("IR_MIN_AREA", "8")),
        max_area=float(os.environ.get("IR_MAX_AREA", "12000")),
        min_circularity=float(os.environ.get("IR_MIN_CIRCULARITY", "0.20")),
        min_brightness=float(os.environ.get("IR_MIN_BRIGHTNESS", "180")),
        blur_size=int(os.environ.get("IR_BLUR", "5")),
        morph_kernel=int(os.environ.get("IR_MORPH_KERNEL", "3")),
        auto_threshold=os.environ.get("IR_AUTO_THRESHOLD", "0") == "1",
        auto_percentile=float(os.environ.get("IR_AUTO_PERCENTILE", "99.7")),
        auto_margin=int(os.environ.get("IR_AUTO_MARGIN", "8")),
    )
    tracker_config = TrackerConfig(
        low_pass_alpha=float(os.environ.get("TRACKER_ALPHA", "0.65")),
        deadband_m=float(os.environ.get("TRACKER_DEADBAND_M", "0.03")),
        gain_forward=float(os.environ.get("TRACKER_GAIN_FORWARD", "0.45")),
        gain_right=float(os.environ.get("TRACKER_GAIN_RIGHT", "0.45")),
        max_speed_mps=float(os.environ.get("TRACKER_MAX_SPEED_MPS", "0.6")),
        max_accel_mps2=float(os.environ.get("TRACKER_MAX_ACCEL_MPS2", "0.8")),
        stale_after_s=float(os.environ.get("TRACKER_STALE_AFTER_S", "0.20")),
    )
    optical_config = OpticalConfig(
        acquire_count=int(os.environ.get("OPTICAL_ACQUIRE_COUNT", "3")),
        loss_count=int(os.environ.get("OPTICAL_LOSS_COUNT", "1")),
    )
    controller = VisionModeController(
        detector=BrightSpotDetector(detector_config),
        tracker=GuidedTracker(tracker_config),
        optical=OpticalStateMachine(optical_config),
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

            wall_timestamp = time.time()
            if camera is None:
                camera = reopen_camera(cv2.VideoCapture, camera_device)
                if camera is None:
                    publisher.publish(
                        locked=False,
                        confidence=0.0,
                        area=0.0,
                        mode=mode,
                        timestamp=wall_timestamp,
                        last_error="camera_unavailable",
                    )
                    time.sleep(0.20)
                    continue
            ok, frame = camera.read()
            if not ok:
                publisher.publish(
                    locked=False,
                    confidence=0.0,
                    area=0.0,
                    mode=mode,
                    timestamp=wall_timestamp,
                    last_error="camera_frame_failed",
                )
                camera = reopen_camera(
                    cv2.VideoCapture,
                    camera_device,
                    camera,
                )
                time.sleep(0.20 if camera is None else 0.05)
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

            preview_frame = frame.copy()
            optical = output.optical
            label = (
                f"{mode}  "
                f"{'LOCKED' if optical.locked else 'BLOCKED'}  "
                f"conf={optical.confidence:.2f} area={optical.area:.0f}"
            )
            cv2.rectangle(preview_frame, (8, 8), (min(780, 16 + len(label) * 12), 42), (0, 0, 0), -1)
            cv2.putText(
                preview_frame, label, (16, 31),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (0, 255, 0) if optical.locked else (0, 140, 255), 2,
                cv2.LINE_AA,
            )
            ok_jpeg, jpeg = cv2.imencode(
                ".jpg", preview_frame, [cv2.IMWRITE_JPEG_QUALITY, 70]
            )
            if ok_jpeg:
                preview.update(jpeg.tobytes())
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
        if camera is not None:
            camera.release()
        preview.close()
        master.close()


if __name__ == "__main__":
    run()
