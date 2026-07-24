"""Optical-link acquisition and immediate-loss state."""

from dataclasses import dataclass
from typing import Optional

from .config import OpticalConfig
from .detector import Detection


@dataclass(frozen=True)
class OpticalSnapshot:
    locked: bool
    blocked: bool
    confidence: float
    area: float
    acquire_count: int
    loss_count: int
    timestamp: float


class OpticalStateMachine:
    def __init__(self, config: OpticalConfig):
        self.config = config
        self._acquire_count = 0
        self._loss_count = 0
        self._locked = False

    def update(
        self, detection: Optional[Detection], *, timestamp: float
    ) -> OpticalSnapshot:
        if detection is None:
            self._loss_count += 1
            self._acquire_count = 0
            self._locked = False
            return OpticalSnapshot(
                locked=False,
                blocked=True,
                confidence=0.0,
                area=0.0,
                acquire_count=0,
                loss_count=self._loss_count,
                timestamp=float(timestamp),
            )

        self._loss_count = 0
        self._acquire_count += 1
        self._locked = self._acquire_count >= self.config.acquire_count
        return OpticalSnapshot(
            locked=self._locked,
            blocked=not self._locked,
            confidence=detection.confidence,
            area=detection.area,
            acquire_count=self._acquire_count,
            loss_count=0,
            timestamp=detection.timestamp,
        )
