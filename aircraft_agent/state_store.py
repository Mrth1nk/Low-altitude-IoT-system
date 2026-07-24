from __future__ import annotations

import json
import os
from pathlib import Path


class AtomicJsonStore:
    def __init__(self, path, *, max_bytes=1024 * 1024, event_hook=None):
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.event_hook = event_hook
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")

    def load(self, default=None):
        if not self.path.exists():
            return {} if default is None else default
        raw = self.path.read_bytes()
        if len(raw) > self.max_bytes:
            raise ValueError("persisted state exceeds state size limit")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("persisted state must be an object")
        return value

    def save(self, value):
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(raw) > self.max_bytes:
            raise ValueError("state size exceeds limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self._event("file_fsync")
        os.replace(temporary, self.path)
        self._event("replace")
        directory_fd = os.open(str(self.path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self._event("directory_fsync")

    def _event(self, name):
        if self.event_hook is not None:
            self.event_hook(name)
