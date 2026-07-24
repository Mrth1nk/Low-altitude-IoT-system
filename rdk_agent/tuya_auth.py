from __future__ import annotations

import hashlib
import hmac
import time


def build_tuya_credentials(device_id: str, device_secret: str, timestamp: int | None = None) -> dict:
    ts = int(timestamp or time.time())
    content = f"deviceId={device_id},timestamp={ts},secureMode=1,accessType=1"
    password = hmac.new(device_secret.encode(), content.encode(), hashlib.sha256).hexdigest()
    username = f"{device_id}|signMethod=hmacSha256,timestamp={ts},secureMode=1,accessType=1"
    return {"client_id": device_id, "username": username, "password": password}


def make_topic(device_id: str, suffix: str) -> str:
    return f"tylink/{device_id}/{suffix}"
