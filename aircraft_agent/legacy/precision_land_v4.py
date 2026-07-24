#!/usr/bin/env python3
"""
IR beacon tracker for ArduPilot companion computers.

This replaces the old ArUco precision-landing detector with a bright IR beacon
tracker. The script keeps reporting the beacon offset and, when MAVLink is
enabled, can command the aircraft to stay above the beacon.

Camera input is intentionally by-id only. Set CAM_DEVICE to the wanted
/dev/v4l/by-id/... path.
"""

import json
import logging
import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List, Optional, Tuple

import cv2
import numpy as np

from camera_geometry import camera_to_body_offsets, control_mode_for_flight_mode

try:
    from pymavlink import mavutil
    HAS_MAVLINK = True
except ImportError:
    mavutil = None
    HAS_MAVLINK = False

try:
    from optical_link_state import DEFAULT_STATUS_FILE, write_optical_link_state
except ImportError:
    DEFAULT_STATUS_FILE = "/tmp/optical_link_status.json"

    def write_optical_link_state(path, *, blocked, state, detected, lost_frames):
        return None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("IRLock")

HEADLESS = os.environ.get("DISPLAY", "") == ""


@dataclass
class Config:
    NO_MAVLINK: bool = False

    DEV_PORT: str = os.environ.get("MAV_PORT", "/dev/ttyS9")
    BAUD_RATE: int = int(os.environ.get("MAV_BAUD", "57600"))
    SOURCE_SYSTEM: int = int(os.environ.get("SOURCE_SYSTEM", "1"))
    SOURCE_COMPONENT: int = int(os.environ.get("SOURCE_COMPONENT", "196"))

    CAM_DEVICE: str = os.environ.get(
        "CAM_DEVICE",
        "/dev/v4l/by-id/usb-Generic_Rmoncam_A2_1080P_200901010001-video-index0",
    ).strip()
    IMG_WIDTH: int = int(os.environ.get("IMG_WIDTH", "640"))
    IMG_HEIGHT: int = int(os.environ.get("IMG_HEIGHT", "480"))
    CAMERA_FX: float = float(os.environ.get("CAM_FX", "460.0"))
    CAMERA_FY: float = float(os.environ.get("CAM_FY", "460.0"))
    CAMERA_CX: float = float(os.environ.get("CAM_CX", "320.0"))
    CAMERA_CY: float = float(os.environ.get("CAM_CY", "240.0"))

    # Image top points to which aircraft direction when the camera faces down.
    # Valid values: FORWARD, BACKWARD, LEFT, RIGHT.
    CAM_ORIENTATION: str = os.environ.get("CAM_ORIENTATION", "FORWARD")
    CAM_OFFSET_X: float = float(os.environ.get("CAM_OFFSET_X", "0.0"))  # +forward, m
    CAM_OFFSET_Y: float = float(os.environ.get("CAM_OFFSET_Y", "0.0"))  # +right, m

    IR_THRESHOLD: int = int(os.environ.get("IR_THRESHOLD", "235"))
    IR_MIN_AREA: float = float(os.environ.get("IR_MIN_AREA", "8"))
    IR_MAX_AREA: float = float(os.environ.get("IR_MAX_AREA", "12000"))
    IR_MIN_CIRCULARITY: float = float(os.environ.get("IR_MIN_CIRCULARITY", "0.20"))
    IR_MIN_BRIGHTNESS: float = float(os.environ.get("IR_MIN_BRIGHTNESS", "180"))
    IR_BLUR: int = int(os.environ.get("IR_BLUR", "5"))
    IR_MORPH_KERNEL: int = int(os.environ.get("IR_MORPH_KERNEL", "3"))
    IR_AUTO_THRESHOLD: bool = os.environ.get("IR_AUTO_THRESHOLD", "0") == "1"
    IR_AUTO_PERCENTILE: float = float(os.environ.get("IR_AUTO_PERCENTILE", "99.7"))
    IR_AUTO_MARGIN: int = int(os.environ.get("IR_AUTO_MARGIN", "8"))

    ROI_SIZE: int = int(os.environ.get("ROI_SIZE", "220"))
    ROI_REACQUIRE_SIZE: int = int(os.environ.get("ROI_REACQUIRE_SIZE", "420"))
    ROI_MAX_MISS: int = int(os.environ.get("ROI_MAX_MISS", "8"))

    ASSUMED_ALTITUDE: float = float(os.environ.get("ASSUMED_ALTITUDE", "2.0"))
    ALIGN_THRESHOLD: float = float(os.environ.get("ALIGN_THRESHOLD", "0.15"))
    TARGET_HOLD_S: float = float(os.environ.get("TARGET_HOLD_S", "0.25"))
    TARGET_HOLD_MAX_FRAMES: int = int(os.environ.get("TARGET_HOLD_MAX_FRAMES", "5"))
    EMA_ALPHA: float = float(os.environ.get("EMA_ALPHA", "0.65"))

    SEND_HZ: float = float(os.environ.get("SEND_HZ", "15"))
    HEARTBEAT_HZ: float = 1.0
    CONTROL_MODE: str = os.environ.get("CONTROL_MODE", "GUIDED_VELOCITY")
    LANDING_TARGET_POSITION_VALID: bool = os.environ.get("LANDING_TARGET_POSITION_VALID", "1") == "1"
    PRECISION_LAND_MODES: list = field(
        default_factory=lambda: os.environ.get("PRECISION_LAND_MODES", "LAND,QLAND").split(",")
    )
    ACTIVE_MODES: list = field(
        default_factory=lambda: os.environ.get(
            "ACTIVE_MODES", "GUIDED,LOITER,POSHOLD,BRAKE,LAND,QLAND"
        ).split(",")
    )
    VEL_KP_X: float = float(os.environ.get("VEL_KP_X", "0.45"))
    VEL_KP_Y: float = float(os.environ.get("VEL_KP_Y", "0.45"))
    VEL_MAX: float = float(os.environ.get("VEL_MAX", "0.6"))
    DEADBAND_M: float = float(os.environ.get("DEADBAND_M", "0.03"))

    STREAM_ENABLED: bool = os.environ.get("STREAM_ENABLED", "0") == "1"
    STREAM_PORT: int = int(os.environ.get("STREAM_PORT", "8090"))
    STREAM_QUALITY: int = int(os.environ.get("STREAM_QUALITY", "65"))
    LOG_ENABLED: bool = False
    LOG_DIR: str = os.environ.get("LOG_DIR", "logs")
    OPTICAL_LINK_STATUS_FILE: str = os.environ.get("OPTICAL_LINK_STATUS_FILE", DEFAULT_STATUS_FILE)
    OPTICAL_LINK_STATUS_HZ: float = float(os.environ.get("OPTICAL_LINK_STATUS_HZ", "30"))


class LandingState(Enum):
    IDLE = auto()
    SEARCH = auto()
    TRACK = auto()
    HOLD = auto()
    LOST = auto()


class EMAFilter:
    def __init__(self, alpha: float = 0.65):
        self.alpha = alpha
        self.tx = None
        self.ty = None
        self.tz = None

    def update(self, tx: float, ty: float, tz: float):
        if self.tx is None:
            self.tx, self.ty, self.tz = tx, ty, tz
            return
        a = self.alpha
        self.tx = a * tx + (1.0 - a) * self.tx
        self.ty = a * ty + (1.0 - a) * self.ty
        self.tz = a * tz + (1.0 - a) * self.tz

    def get(self) -> Tuple[float, float, float]:
        if self.tx is None:
            return 0.0, 0.0, 0.0
        return self.tx, self.ty, self.tz

    @property
    def initialized(self) -> bool:
        return self.tx is not None

    def reset(self):
        self.tx = self.ty = self.tz = None


class ArucoDetector:
    """Compatibility name. It now detects one bright IR beacon."""

    @dataclass
    class DetectionResult:
        marker_id: int
        tx: float
        ty: float
        tz: float
        rvec: np.ndarray
        center_px: Tuple[float, float]
        corners_px: np.ndarray
        used_roi: bool
        all_detected_ids: List[int]
        area: float = 0.0
        brightness: float = 0.0
        circularity: float = 0.0
        threshold: int = 0
        is_held: bool = False
        hold_age_s: float = 0.0

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_center = None
        self.roi_miss_count = 0

    def detect(self, gray: np.ndarray, current_alt: float = -1.0) -> Optional["ArucoDetector.DetectionResult"]:
        h, w = gray.shape[:2]
        result = None

        if self.last_center is not None:
            result = self._detect_in_roi(gray, w, h, current_alt, self.cfg.ROI_SIZE, False)
            self.roi_miss_count = 0 if result else self.roi_miss_count + 1

        if result is None and self.last_center is not None:
            result = self._detect_in_roi(gray, w, h, current_alt, self.cfg.ROI_REACQUIRE_SIZE, True)

        if result is None:
            result = self._detect_full(gray, w, h, current_alt)

        if result is None and self.roi_miss_count >= self.cfg.ROI_MAX_MISS:
            self.reset_tracking()

        if result is not None:
            self.last_center = result.center_px
        return result

    def _detect_full(self, gray, w, h, current_alt):
        return self._find_beacon(gray, current_alt, False, 0, 0)

    def _detect_in_roi(self, gray, w, h, current_alt, size, reacquire):
        if self.last_center is None:
            return None
        cx, cy = self.last_center
        half = max(40, int(size) // 2)
        rx = max(0, int(cx - half))
        ry = max(0, int(cy - half))
        rw = min(half * 2, w - rx)
        rh = min(half * 2, h - ry)
        if rw < 40 or rh < 40:
            return None
        roi = gray[ry:ry + rh, rx:rx + rw]
        return self._find_beacon(roi, current_alt, True, rx, ry)

    def _find_beacon(self, gray, current_alt, used_roi, ox, oy):
        work = gray
        blur = max(0, int(self.cfg.IR_BLUR))
        if blur > 1:
            if blur % 2 == 0:
                blur += 1
            work = cv2.GaussianBlur(work, (blur, blur), 0)

        threshold = self._threshold_for(work)
        _, mask = cv2.threshold(work, threshold, 255, cv2.THRESH_BINARY)

        kernel_size = max(0, int(self.cfg.IR_MORPH_KERNEL))
        if kernel_size > 1:
            kernel = np.ones((kernel_size, kernel_size), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        best_score = -1.0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self.cfg.IR_MIN_AREA or area > self.cfg.IR_MAX_AREA:
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            circularity = 4.0 * math.pi * area / (perimeter * perimeter)
            if circularity < self.cfg.IR_MIN_CIRCULARITY:
                continue

            moments = cv2.moments(contour)
            if moments["m00"] <= 0:
                continue
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]

            contour_mask = np.zeros(gray.shape[:2], dtype=np.uint8)
            cv2.drawContours(contour_mask, [contour], -1, 255, -1)
            brightness = float(cv2.mean(gray, mask=contour_mask)[0])
            if brightness < self.cfg.IR_MIN_BRIGHTNESS:
                continue

            x, y, bw, bh = cv2.boundingRect(contour)
            center_weight = 1.0
            if self.last_center is not None and used_roi:
                center_weight = 1.2
            score = area * (brightness / 255.0) * (0.5 + circularity) * center_weight
            if score > best_score:
                best_score = score
                best = (cx, cy, x, y, bw, bh, area, brightness, circularity)

        if best is None:
            return None

        cx, cy, x, y, bw, bh, area, brightness, circularity = best
        full_cx = cx + ox
        full_cy = cy + oy
        altitude = current_alt if current_alt and current_alt > 0.05 else self.cfg.ASSUMED_ALTITUDE
        tz = max(0.05, float(altitude))
        tx = (full_cx - self.cfg.CAMERA_CX) / self.cfg.CAMERA_FX * tz
        ty = (full_cy - self.cfg.CAMERA_CY) / self.cfg.CAMERA_FY * tz
        corners = np.array([
            [x + ox, y + oy],
            [x + bw + ox, y + oy],
            [x + bw + ox, y + bh + oy],
            [x + ox, y + bh + oy],
        ], dtype=np.float32)

        return ArucoDetector.DetectionResult(
            marker_id=1,
            tx=float(tx),
            ty=float(ty),
            tz=float(tz),
            rvec=np.zeros(3, dtype=np.float32),
            center_px=(float(full_cx), float(full_cy)),
            corners_px=corners,
            used_roi=used_roi,
            all_detected_ids=[1],
            area=float(area),
            brightness=float(brightness),
            circularity=float(circularity),
            threshold=int(threshold),
        )

    def _threshold_for(self, gray) -> int:
        if self.cfg.IR_AUTO_THRESHOLD:
            p = float(np.percentile(gray, self.cfg.IR_AUTO_PERCENTILE))
            return int(max(0, min(254, p - self.cfg.IR_AUTO_MARGIN)))
        return int(max(0, min(255, self.cfg.IR_THRESHOLD)))

    def reset_tracking(self):
        self.last_center = None
        self.roi_miss_count = 0

    def reset_all(self):
        self.reset_tracking()


class MJPEGServer:
    _frame_lock = threading.Lock()
    _latest_jpeg = None
    _latest_status = "{}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        with MJPEGServer._frame_lock:
                            jpeg = MJPEGServer._latest_jpeg
                        if jpeg:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpeg)}\r\n".encode())
                            self.wfile.write(b"\r\n")
                            self.wfile.write(jpeg)
                            self.wfile.write(b"\r\n")
                        time.sleep(0.033)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif self.path == "/status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(MJPEGServer._latest_status.encode())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b'<html><body><img src="/stream"></body></html>')

    def __init__(self, port: int):
        self.server = HTTPServer(("0.0.0.0", port), self.Handler)
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        log.info("MJPEG stream: http://0.0.0.0:%s/stream", self.server.server_port)

    def update_frame(self, jpeg_bytes: bytes):
        with self._frame_lock:
            MJPEGServer._latest_jpeg = jpeg_bytes

    def update_status(self, status_json: str):
        with self._frame_lock:
            MJPEGServer._latest_status = status_json

    def stop(self):
        self.server.shutdown()


class PrecisionLanding:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.detector = ArucoDetector(cfg)
        self.ema = EMAFilter(cfg.EMA_ALPHA)
        self.master = None
        self.cap = None
        self.running = False
        self.state = LandingState.IDLE
        self.current_mode = "UNKNOWN"
        self.current_altitude = -1.0
        self.altitude_from_vision = False
        self.vision_altitude = -1.0
        self.last_vision_alt_time = 0.0
        self.last_heartbeat_time = 0.0
        self.last_send_time = 0.0
        self.last_detection_time = 0.0
        self.last_real_detection = None
        self.last_real_detection_time = 0.0
        self.frame_count = 0
        self.detect_count = 0
        self.msg_sent_count = 0
        self.lost_frames_since_detect = 0
        self.fps_time = time.time()
        self.last_log_time = 0.0
        self.last_link_status_time = 0.0
        self.last_link_blocked = None
        self.stream_server = None
        self.logger = None

    @property
    def mavlink_enabled(self) -> bool:
        return self.master is not None

    def connect_mavlink(self) -> bool:
        if self.cfg.NO_MAVLINK:
            log.info("MAVLink disabled")
            return False
        if not HAS_MAVLINK:
            log.warning("pymavlink is not installed; detect-only mode")
            return False
        try:
            log.info("Connecting MAVLink: %s @ %s", self.cfg.DEV_PORT, self.cfg.BAUD_RATE)
            self.master = mavutil.mavlink_connection(
                self.cfg.DEV_PORT,
                baud=self.cfg.BAUD_RATE,
                source_system=self.cfg.SOURCE_SYSTEM,
                source_component=self.cfg.SOURCE_COMPONENT,
            )
            hb = self.master.wait_heartbeat(timeout=10)
            if hb is None:
                log.warning("No flight-controller heartbeat; detect-only mode")
                self.master = None
                return False
            log.info("Flight controller connected: system=%s", self.master.target_system)
            self._request_data_streams()
            return True
        except Exception as exc:
            log.warning("MAVLink connection failed: %s", exc)
            self.master = None
            return False

    def _request_data_streams(self):
        try:
            self.master.mav.request_data_stream_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_POSITION,
                10,
                1,
            )
        except Exception as exc:
            log.debug("Data stream request failed: %s", exc)

    def send_heartbeat(self):
        if not self.mavlink_enabled:
            return
        now = time.time()
        if now - self.last_heartbeat_time < 1.0 / self.cfg.HEARTBEAT_HZ:
            return
        try:
            self.master.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0,
                0,
                mavutil.mavlink.MAV_STATE_ACTIVE,
            )
            self.last_heartbeat_time = now
        except Exception as exc:
            log.warning("Heartbeat failed: %s", exc)

    def update_flight_mode(self):
        if not self.mavlink_enabled:
            return
        try:
            while True:
                msg = self.master.recv_match(type="HEARTBEAT", blocking=False)
                if not msg:
                    break
                if msg.get_srcComponent() == self.cfg.SOURCE_COMPONENT:
                    continue
                mode = self.master.flightmode
                if mode and mode != self.current_mode:
                    log.info("Flight mode: %s -> %s", self.current_mode, mode)
                    self.current_mode = mode
                    self.state = LandingState.SEARCH if self._mode_active() else LandingState.IDLE
        except Exception as exc:
            log.warning("Flight-mode read failed: %s", exc)

    def update_altitude(self):
        if not self.mavlink_enabled:
            return
        try:
            while True:
                msg = self.master.recv_match(
                    type=["GLOBAL_POSITION_INT", "LOCAL_POSITION_NED"],
                    blocking=False,
                )
                if not msg:
                    break
                if msg.get_type() == "GLOBAL_POSITION_INT":
                    self.current_altitude = msg.relative_alt / 1000.0
                elif msg.get_type() == "LOCAL_POSITION_NED":
                    self.current_altitude = -msg.z
        except Exception as exc:
            log.warning("Altitude read failed: %s", exc)

    def resolve_tracking_target(self, raw_detection):
        now = time.time()
        tracking = raw_detection
        angle_x = angle_y = 0.0

        if raw_detection is not None:
            self.detect_count += 1
            self.lost_frames_since_detect = 0
            self.last_real_detection = raw_detection
            self.last_real_detection_time = now
            self.ema.update(raw_detection.tx, raw_detection.ty, raw_detection.tz)
        else:
            self.lost_frames_since_detect += 1
            hold_age = now - self.last_real_detection_time
            if (
                self.last_real_detection is not None
                and self.ema.initialized
                and hold_age <= self.cfg.TARGET_HOLD_S
                and self.lost_frames_since_detect <= self.cfg.TARGET_HOLD_MAX_FRAMES
            ):
                tracking = self._make_held_detection(self.last_real_detection, hold_age)

        if tracking is not None and self.ema.initialized:
            ftx, fty, ftz = self.ema.get()
            angle_x, angle_y = self.camera_to_body_angles(ftx, fty, ftz)
            if self._mode_active() or not self.mavlink_enabled:
                self.send_control(ftx, fty, ftz, angle_x, angle_y)

        return tracking, angle_x, angle_y

    def _make_held_detection(self, det, hold_age_s: float):
        return ArucoDetector.DetectionResult(
            marker_id=det.marker_id,
            tx=det.tx,
            ty=det.ty,
            tz=det.tz,
            rvec=det.rvec.copy(),
            center_px=(float(det.center_px[0]), float(det.center_px[1])),
            corners_px=det.corners_px.copy(),
            used_roi=det.used_roi,
            all_detected_ids=list(det.all_detected_ids),
            area=det.area,
            brightness=det.brightness,
            circularity=det.circularity,
            threshold=det.threshold,
            is_held=True,
            hold_age_s=hold_age_s,
        )

    def send_control(self, tx: float, ty: float, tz: float, angle_x: float, angle_y: float):
        if not self.mavlink_enabled:
            return
        if time.time() - self.last_send_time < 1.0 / self.cfg.SEND_HZ:
            return
        mode = control_mode_for_flight_mode(
            self.cfg.CONTROL_MODE,
            self.current_mode,
            self.cfg.PRECISION_LAND_MODES,
        )
        if mode == "LANDING_TARGET":
            fwd, right = self.camera_to_body_offsets(tx, ty)
            self.send_landing_target(
                angle_x,
                angle_y,
                math.sqrt(tx * tx + ty * ty + tz * tz),
                fwd_m=fwd,
                right_m=right,
                down_m=max(0.05, tz),
            )
        else:
            fwd, right = self.camera_to_body_offsets(tx, ty)
            self.send_body_velocity(fwd, right)

    def send_landing_target(
        self,
        angle_x: float,
        angle_y: float,
        distance: float = 0.0,
        *,
        fwd_m: float = 0.0,
        right_m: float = 0.0,
        down_m: float = 0.0,
    ):
        try:
            now = time.time()
            target_type = getattr(mavutil.mavlink, "LANDING_TARGET_TYPE_LIGHT_BEACON", 2)
            if self.cfg.LANDING_TARGET_POSITION_VALID:
                try:
                    self.master.mav.landing_target_send(
                        int(now * 1e6),
                        0,
                        mavutil.mavlink.MAV_FRAME_BODY_FRD,
                        angle_x,
                        angle_y,
                        distance,
                        0.0,
                        0.0,
                        fwd_m,
                        right_m,
                        down_m if down_m > 0 else distance,
                        (1.0, 0.0, 0.0, 0.0),
                        target_type,
                        1,
                    )
                except TypeError:
                    self.master.mav.landing_target_send(
                        int(now * 1e6),
                        0,
                        mavutil.mavlink.MAV_FRAME_BODY_FRD,
                        angle_x,
                        angle_y,
                        distance,
                        0.0,
                        0.0,
                    )
            else:
                self.master.mav.landing_target_send(
                    int(now * 1e6),
                    0,
                    mavutil.mavlink.MAV_FRAME_BODY_FRD,
                    angle_x,
                    angle_y,
                    distance,
                    0.0,
                    0.0,
                )
            self.last_send_time = now
            self.msg_sent_count += 1
        except Exception as exc:
            log.warning("LANDING_TARGET send failed: %s", exc)

    def send_body_velocity(self, fwd_m: float, right_m: float):
        if abs(fwd_m) < self.cfg.DEADBAND_M:
            fwd_m = 0.0
        if abs(right_m) < self.cfg.DEADBAND_M:
            right_m = 0.0
        vx = max(-self.cfg.VEL_MAX, min(self.cfg.VEL_MAX, self.cfg.VEL_KP_X * fwd_m))
        vy = max(-self.cfg.VEL_MAX, min(self.cfg.VEL_MAX, self.cfg.VEL_KP_Y * right_m))
        vz = 0.0
        type_mask = (
            0b0000111111000111
        )  # ignore position, acceleration, yaw, yaw-rate; use velocity only
        try:
            now = time.time()
            self.master.mav.set_position_target_local_ned_send(
                0,
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_FRAME_BODY_NED,
                type_mask,
                0,
                0,
                0,
                vx,
                vy,
                vz,
                0,
                0,
                0,
                0,
                0,
            )
            self.last_send_time = now
            self.msg_sent_count += 1
        except Exception as exc:
            log.warning("Velocity command failed: %s", exc)

    def camera_to_body_offsets(self, tx: float, ty: float) -> Tuple[float, float]:
        return camera_to_body_offsets(
            tx,
            ty,
            self.cfg.CAM_ORIENTATION,
            self.cfg.CAM_OFFSET_X,
            self.cfg.CAM_OFFSET_Y,
        )

    def camera_to_body_angles(self, tx: float, ty: float, tz: float) -> Tuple[float, float]:
        if tz <= 0:
            return 0.0, 0.0
        fwd, right = self.camera_to_body_offsets(tx, ty)
        angle_x = math.atan2(right, tz)
        angle_y = math.atan2(-fwd, tz)
        return angle_x, angle_y

    def open_camera(self) -> bool:
        if not self.cfg.CAM_DEVICE:
            log.error("CAM_DEVICE is empty. Use /dev/v4l/by-id/...; index fallback is disabled.")
            return False
        log.info("Opening camera by-id: %s", self.cfg.CAM_DEVICE)
        self.cap = cv2.VideoCapture(self.cfg.CAM_DEVICE, cv2.CAP_V4L2)
        self._configure_camera()
        if self.cap.isOpened():
            log.info("Camera opened")
            return True
        self.cap.release()
        log.error("Camera open failed: %s", self.cfg.CAM_DEVICE)
        return False

    def _configure_camera(self):
        if not self.cap:
            return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.IMG_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.IMG_HEIGHT)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def update_state(self, detection):
        now = time.time()
        if self.state == LandingState.IDLE:
            return
        if detection is None:
            if now - self.last_detection_time > max(0.5, self.cfg.TARGET_HOLD_S * 3):
                self.state = LandingState.LOST
            return
        self.last_detection_time = now
        fwd, right = self.camera_to_body_offsets(detection.tx, detection.ty)
        err = math.sqrt(fwd * fwd + right * right)
        if self.state in (LandingState.SEARCH, LandingState.LOST):
            self.state = LandingState.TRACK
        elif err <= self.cfg.ALIGN_THRESHOLD:
            self.state = LandingState.HOLD
        else:
            self.state = LandingState.TRACK

    def publish_optical_link_state(self, raw_detection, _tracking_detection) -> None:
        now = time.time()
        interval = 1.0 / max(1.0, float(self.cfg.OPTICAL_LINK_STATUS_HZ))
        blocked = raw_detection is None
        if self.last_link_blocked == blocked and now - self.last_link_status_time < interval:
            return
        self.last_link_status_time = now
        self.last_link_blocked = blocked
        write_optical_link_state(
            self.cfg.OPTICAL_LINK_STATUS_FILE,
            blocked=blocked,
            state=self.state.name,
            detected=raw_detection is not None,
            lost_frames=self.lost_frames_since_detect,
        )

    @staticmethod
    def describe_direction(angle_x: float, angle_y: float) -> Tuple[str, str]:
        ax = math.degrees(angle_x)
        ay = math.degrees(angle_y)
        th = 2.0
        fb = "front" if ay < -th else "back" if ay > th else ""
        lr = "right" if ax > th else "left" if ax < -th else ""
        direction = (fb + " " + lr).strip() or "center"
        arrows = {
            "front": "^",
            "back": "v",
            "left": "<",
            "right": ">",
            "front left": "^<",
            "front right": "^>",
            "back left": "v<",
            "back right": "v>",
            "center": "o",
        }
        return direction, arrows.get(direction, "?")

    def draw_debug(self, frame, detection, angle_x, angle_y):
        h, w = frame.shape[:2]
        color = (0, 255, 0) if detection else (0, 0, 255)
        cv2.line(frame, (w // 2 - 25, h // 2), (w // 2 + 25, h // 2), (255, 0, 0), 2)
        cv2.line(frame, (w // 2, h // 2 - 25), (w // 2, h // 2 + 25), (255, 0, 0), 2)

        if detection is not None:
            cx, cy = detection.center_px
            pts = detection.corners_px.astype(int)
            cv2.polylines(frame, [pts], True, color, 2)
            cv2.circle(frame, (int(cx), int(cy)), 8, (0, 255, 255), 2)
            cv2.line(frame, (w // 2, h // 2), (int(cx), int(cy)), (0, 255, 255), 2)
            fwd, right = self.camera_to_body_offsets(detection.tx, detection.ty)
            err = math.sqrt(fwd * fwd + right * right)
            direction, arrow = self.describe_direction(angle_x, angle_y)
            cv2.putText(frame, f"IR {arrow} {direction} err={err:.2f}m",
                        (10, h - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            cv2.putText(frame, f"area={detection.area:.0f} bright={detection.brightness:.0f} thr={detection.threshold}",
                        (10, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)

        elapsed = time.time() - self.fps_time
        fps = self.frame_count / elapsed if elapsed > 0 else 0.0
        lines = [
            f"Mode: {self.current_mode if self.mavlink_enabled else 'DETECT_ONLY'} State: {self.state.name} FPS:{fps:.1f}",
            f"Control: {self.cfg.CONTROL_MODE} Sent:{self.msg_sent_count}",
            f"Alt: {self.current_altitude:.2f}m Assumed:{self.cfg.ASSUMED_ALTITUDE:.2f}m",
            f"Cam: {self.cfg.CAM_ORIENTATION} by-id only",
        ]
        y = 24
        for line in lines:
            cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1)
            y += 20
        return frame

    def _mode_active(self) -> bool:
        if not self.mavlink_enabled:
            return True
        active = [m.strip().upper() for m in self.cfg.ACTIVE_MODES]
        return self.current_mode.upper() in active

    def run(self):
        log.info("IR beacon tracker")
        log.info("Camera: %s", self.cfg.CAM_DEVICE)
        log.info(
            "Camera orientation: %s (image top=aircraft forward, image right=aircraft right)",
            self.cfg.CAM_ORIENTATION,
        )
        log.info("Control mode: %s", self.cfg.CONTROL_MODE)
        log.info("Precision landing modes: %s", ",".join(self.cfg.PRECISION_LAND_MODES))
        write_optical_link_state(
            self.cfg.OPTICAL_LINK_STATUS_FILE,
            blocked=True,
            state="STARTING",
            detected=False,
            lost_frames=0,
        )
        if not self.open_camera():
            write_optical_link_state(
                self.cfg.OPTICAL_LINK_STATUS_FILE,
                blocked=True,
                state="CAMERA_UNAVAILABLE",
                detected=False,
                lost_frames=0,
            )
            return 1
        self.connect_mavlink()
        self.state = LandingState.SEARCH if self._mode_active() else LandingState.IDLE
        self.running = True
        self.fps_time = time.time()

        if self.cfg.STREAM_ENABLED:
            self.stream_server = MJPEGServer(self.cfg.STREAM_PORT)
            self.stream_server.start()

        try:
            while self.running:
                self.send_heartbeat()
                self.update_flight_mode()
                self.update_altitude()
                ret, frame = self.cap.read()
                if not ret:
                    time.sleep(0.01)
                    continue
                self.frame_count += 1
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                raw = self.detector.detect(gray, self.current_altitude)
                detection, angle_x, angle_y = self.resolve_tracking_target(raw)
                self.update_state(detection)
                self.publish_optical_link_state(raw, detection)

                now = time.time()
                if now - self.last_log_time > 1.0:
                    if detection:
                        # 计算相对于机体重心的前后左右偏移距离 (米)[cite: 3]
                        fwd, right = self.camera_to_body_offsets(detection.tx, detection.ty)
                        
                        # 获取方向的英文描述(如 "front left")和箭头符号(如 "^<")[cite: 3]
                        direction, arrow = self.describe_direction(angle_x, angle_y)
                        
                        # 将英文方向映射为清晰的中文提示，用于终端显示[cite: 3]
                        dir_map = {
                            "front": "正前", "back": "正后", "left": "正左", "right": "正右",
                            "front left": "左前", "front right": "右前",
                            "back left": "左后", "back right": "右后", "center": "居中对准"
                        }
                        zh_dir = dir_map.get(direction, direction)
                        
                        # 格式化输出到终端[cite: 3]
                        log.info(
                            "[%s] 修正方向: [%s] (%s) | 距离偏移: 前后 %+.2f 米, 左右 %+.2f 米 %s",
                            self.state.name,
                            arrow,
                            zh_dir,
                            fwd,
                            right,
                            "(信号保持)" if detection.is_held else "",
                        )
                    else:
                        log.info("[%s] 目标丢失! (已丢失 %s 帧)", self.state.name, self.lost_frames_since_detect)
                    self.last_log_time = now

                if self.stream_server or not HEADLESS:
                    annotated = self.draw_debug(frame, detection, angle_x, angle_y)
                    if self.stream_server:
                        _, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.STREAM_QUALITY])
                        self.stream_server.update_frame(jpeg.tobytes())
                        self.stream_server.update_status(json.dumps(self.status_dict(detection, angle_x, angle_y)))
                    if not HEADLESS:
                        cv2.imshow("IR Beacon Tracker", annotated)
                        key = cv2.waitKey(1) & 0xFF
                        if key == ord("q"):
                            break
                        if key == ord("r"):
                            self.detector.reset_all()
                            self.ema.reset()
                else:
                    time.sleep(0.001)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()
        return 0

    def status_dict(self, detection, angle_x, angle_y):
        elapsed = time.time() - self.fps_time
        fps = self.frame_count / elapsed if elapsed > 0 else 0.0
        info = {
            "detected": detection is not None,
            "raw_detected": detection is not None and not detection.is_held,
            "held": bool(getattr(detection, "is_held", False)) if detection else False,
            "state": self.state.name,
            "mode": self.current_mode if self.mavlink_enabled else "DETECT_ONLY",
            "altitude": round(self.current_altitude, 2),
            "fps": round(fps, 1),
            "frame_count": self.frame_count,
            "detect_count": self.detect_count,
            "msg_sent": self.msg_sent_count,
            "lost_frames": self.lost_frames_since_detect,
            "mavlink": self.mavlink_enabled,
            "control_mode": self.cfg.CONTROL_MODE,
        }
        if detection:
            fwd, right = self.camera_to_body_offsets(detection.tx, detection.ty)
            err = math.sqrt(fwd * fwd + right * right)
            direction, arrow = self.describe_direction(angle_x, angle_y)
            info.update({
                "marker_id": 1,
                "tx": round(detection.tx, 4),
                "ty": round(detection.ty, 4),
                "tz": round(detection.tz, 4),
                "fwd_m": round(fwd, 4),
                "right_m": round(right, 4),
                "h_err": round(err, 3),
                "angle_x": round(math.degrees(angle_x), 1),
                "angle_y": round(math.degrees(angle_y), 1),
                "angle_x_deg": round(math.degrees(angle_x), 2),
                "angle_y_deg": round(math.degrees(angle_y), 2),
                "direction": direction,
                "arrow": arrow,
                "used_roi": detection.used_roi,
                "all_ids": detection.all_detected_ids,
                "area": round(detection.area, 1),
                "brightness": round(detection.brightness, 1),
                "circularity": round(detection.circularity, 3),
                "threshold": detection.threshold,
                "hold_age_ms": int(detection.hold_age_s * 1000),
            })
        return info

    def cleanup(self):
        self.running = False
        if self.stream_server:
            self.stream_server.stop()
        if self.cap:
            self.cap.release()
        write_optical_link_state(
            self.cfg.OPTICAL_LINK_STATUS_FILE,
            blocked=True,
            state="STOPPED",
            detected=False,
            lost_frames=self.lost_frames_since_detect,
        )
        if not HEADLESS:
            cv2.destroyAllWindows()


def print_usage():
    print(
        """
IR beacon tracker

Usage:
  python3 precision_land_v4.py --no-mavlink
  python3 precision_land_v4.py --stream

Important environment variables:
  CAM_DEVICE=/dev/v4l/by-id/...
  CAM_ORIENTATION=FORWARD|BACKWARD|LEFT|RIGHT
  CONTROL_MODE=GUIDED_VELOCITY|LANDING_TARGET
  IR_THRESHOLD=210
"""
    )


if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv:
        print_usage()
        sys.exit(0)
    cfg = Config()
    if "--no-mavlink" in sys.argv or os.environ.get("NO_MAVLINK", "0") == "1":
        cfg.NO_MAVLINK = True
    if "--stream" in sys.argv:
        cfg.STREAM_ENABLED = True
    app = PrecisionLanding(cfg)
    sys.exit(app.run())
