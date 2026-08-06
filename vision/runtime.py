"""Camera and vision-MAVLink runtime, isolated from aircraft link ownership."""

from __future__ import annotations

import os
import glob
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


MAV_TYPE_GCS = 6
MAV_AUTOPILOT_INVALID = 8
MAV_CMD_SET_MESSAGE_INTERVAL = 511
MAV_SEVERITY_NOTICE = 5
VISION_MESSAGE_IDS = (33, 132, 32)  # GLOBAL_POSITION_INT, DISTANCE_SENSOR, LOCAL_POSITION_NED


def is_autopilot_heartbeat(message):
    return (
        message is not None
        and message.get_type() == "HEARTBEAT"
        and int(getattr(message, "type", MAV_TYPE_GCS)) != MAV_TYPE_GCS
        and int(getattr(message, "autopilot", MAV_AUTOPILOT_INVALID))
        != MAV_AUTOPILOT_INVALID
    )


def wait_for_autopilot_heartbeat(master, timeout=10.0):
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        message = master.recv_match(
            type="HEARTBEAT",
            blocking=True,
            timeout=min(1.0, max(0.0, deadline - time.monotonic())),
        )
        if not is_autopilot_heartbeat(message):
            continue
        master.target_system = message.get_srcSystem()
        master.target_component = message.get_srcComponent()
        return message
    raise TimeoutError("flight-controller heartbeat unavailable on vision UART")


def request_vision_messages(master, interval_us=100_000):
    for message_id in VISION_MESSAGE_IDS:
        master.mav.command_long_send(
            master.target_system or 1,
            master.target_component or 1,
            MAV_CMD_SET_MESSAGE_INTERVAL,
            0,
            message_id,
            int(interval_us),
            0,
            0,
            0,
            0,
            0,
        )


def make_camera_capture(cv2_module, camera_device):
    return cv2_module.VideoCapture(camera_device, cv2_module.CAP_V4L2)


def camera_candidates(primary, extra_devices=None):
    candidates = []

    def add(path):
        if path and path not in candidates:
            candidates.append(path)

    add(primary)
    if extra_devices:
        for path in extra_devices:
            add(path)
    for pattern in ("/dev/v4l/by-id/*", "/dev/video*"):
        for path in sorted(glob.glob(pattern)):
            add(path)
    return candidates


def send_target_status_text(master, *, locked):
    message = b"IR TARGET FOUND" if locked else b"IR TARGET LOST"
    master.mav.statustext_send(MAV_SEVERITY_NOTICE, message)


def send_guided_tracking_active_text(master):
    master.mav.statustext_send(MAV_SEVERITY_NOTICE, b"IR TRACKING ACTIVE")


def reopen_camera(
    capture_factory,
    camera_device,
    previous=None,
    *,
    width=640,
    height=480,
    fallback_devices=None,
    validate_frame=False,
):
    candidates = camera_candidates(camera_device, fallback_devices)
    if previous is not None:
        previous.release()
    for device in candidates:
        camera = capture_factory(device)
        if camera.isOpened():
            camera.set(3, int(width))
            camera.set(4, int(height))
            camera.set(38, 1)
            if validate_frame:
                ok, _frame = camera.read()
                if not ok:
                    camera.release()
                    continue
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
        **diagnostics,
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
                **diagnostics,
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
    wait_for_autopilot_heartbeat(master, timeout=10)
    request_vision_messages(master)
    capture_factory = lambda device: make_camera_capture(cv2, device)
    camera = reopen_camera(
        capture_factory,
        camera_device,
        validate_frame=True,
    )
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
        pixel_deadzone=float(os.environ.get("TRACKER_PIXEL_DEADZONE", "25")),
        pixel_gain_forward=float(
            os.environ.get("TRACKER_PIXEL_GAIN_FORWARD", "0.6")
        ),
        pixel_gain_right=float(
            os.environ.get("TRACKER_PIXEL_GAIN_RIGHT", "0.6")
        ),
        pixel_max_speed_mps=float(
            os.environ.get("TRACKER_PIXEL_MAX_SPEED_MPS", "0.35")
        ),
        pixel_send_hz=float(os.environ.get("TRACKER_PIXEL_SEND_HZ", "10")),
    )
    optical_config = OpticalConfig(
        acquire_count=int(os.environ.get("OPTICAL_ACQUIRE_COUNT", "3")),
        loss_count=int(os.environ.get("OPTICAL_LOSS_COUNT", "1")),
    )
    camera_config = CameraConfig()
    controller = VisionModeController(
        detector=BrightSpotDetector(detector_config),
        tracker=GuidedTracker(tracker_config),
        optical=OpticalStateMachine(optical_config),
        camera=camera_config,
    )
    publisher = OpticalStatePublisher(
        AtomicJsonStore(runtime_dir / "optical-state.json", max_bytes=64 * 1024)
    )
    stop = threading.Event()
    mode = "UNKNOWN"
    force_guided_tracking = os.environ.get("VISION_FORCE_GUIDED_TRACKING", "0") == "1"
    altitude_m = 0.0
    altitude_source = "unknown"
    guided_tx_count = 0
    landing_tx_count = 0
    status_text_tx_count = 0
    last_announced_lock = None
    guided_tracking_announced = False
    last_status_text = ""
    last_control = None
    last_guided_control = None
    last_landing_control = None

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop.is_set():
            message = master.recv_match(blocking=False)
            while message is not None:
                kind = message.get_type()
                if kind == "HEARTBEAT" and is_autopilot_heartbeat(message):
                    mode = mavutil.mode_string_v10(message)
                elif kind == "DISTANCE_SENSOR" and int(message.current) > 0:
                    altitude_m = float(message.current) / 100.0
                    altitude_source = "rangefinder"
                elif (
                    kind == "GLOBAL_POSITION_INT"
                    and altitude_source != "rangefinder"
                ):
                    altitude_m = max(0.0, float(message.relative_alt) / 1000.0)
                    altitude_source = "relative_altitude"
                message = master.recv_match(blocking=False)

            wall_timestamp = time.time()
            control_mode = "GUIDED" if force_guided_tracking else mode
            if camera is None:
                camera = reopen_camera(
                    capture_factory,
                    camera_device,
                    validate_frame=True,
                )
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
                    capture_factory,
                    camera_device,
                    camera,
                    validate_frame=True,
                )
                time.sleep(0.20 if camera is None else 0.05)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            now = time.monotonic()
            output = controller.process(
                gray,
                mode=control_mode,
                altitude_m=altitude_m,
                altitude_source=altitude_source,
                timestamp=now,
                now=now,
            )
            if output.optical.locked != last_announced_lock:
                send_target_status_text(
                    master,
                    locked=output.optical.locked,
                )
                last_announced_lock = output.optical.locked
                last_status_text = (
                    "IR TARGET FOUND"
                    if output.optical.locked
                    else "IR TARGET LOST"
                )
                status_text_tx_count += 1
            if output.correction is not None:
                if not guided_tracking_announced:
                    send_guided_tracking_active_text(master)
                    last_status_text = "IR TRACKING ACTIVE"
                    status_text_tx_count += 1
                    guided_tracking_announced = True
                _send_guided_velocity(master, output.correction)
                guided_tx_count += 1
                last_guided_control = {
                    "kind": "guided_velocity",
                    "forward_mps": output.correction.forward_mps,
                    "right_mps": output.correction.right_mps,
                    "timestamp": wall_timestamp,
                }
                last_control = last_guided_control
            else:
                if control_mode.strip().upper() != "GUIDED" or not output.optical.locked:
                    guided_tracking_announced = False
            if output.landing_target is not None:
                send_landing_target(master.mav, output.landing_target)
                landing_tx_count += 1
                last_landing_control = {
                    "kind": "landing_target",
                    "angle_x": output.landing_target.angle_x,
                    "angle_y": output.landing_target.angle_y,
                    "timestamp": wall_timestamp,
                }
                if last_control is None or last_control.get("kind") != "guided_velocity":
                    last_control = last_landing_control

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
                control_mode=control_mode,
                force_guided_tracking=force_guided_tracking,
                altitude_m=altitude_m,
                altitude_source=altitude_source,
                guided_tx_count=guided_tx_count,
                landing_tx_count=landing_tx_count,
                status_text_tx_count=status_text_tx_count,
                last_status_text=last_status_text,
                last_control=last_control,
                last_guided_control=last_guided_control,
                last_landing_control=last_landing_control,
                frame_width=int(gray.shape[1]),
                frame_height=int(gray.shape[0]),
                target_x=(
                    output.detection.center_x
                    if output.detection is not None
                    else None
                ),
                target_y=(
                    output.detection.center_y
                    if output.detection is not None
                    else None
                ),
                pixel_error_x=(
                    output.detection.center_x - float(gray.shape[1]) / 2.0
                    if output.detection is not None
                    else None
                ),
                pixel_error_y=(
                    output.detection.center_y - float(gray.shape[0]) / 2.0
                    if output.detection is not None
                    else None
                ),
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
