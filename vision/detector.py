"""Bright infrared spot detector based on the proven v4 contour pipeline."""

from dataclasses import dataclass
import math
from typing import Optional

import numpy as np

from .config import DetectorConfig

try:
    import cv2
except ImportError:
    cv2 = None


@dataclass(frozen=True)
class Detection:
    center_x: float
    center_y: float
    area: float
    brightness: float
    circularity: float
    confidence: float
    threshold: int
    timestamp: float


class BrightSpotDetector:
    def __init__(self, config: DetectorConfig):
        self.config = config

    @property
    def backend(self) -> str:
        return "opencv" if cv2 is not None else "numpy"

    def detect(self, gray: np.ndarray, *, timestamp: float) -> Optional[Detection]:
        if not isinstance(gray, np.ndarray) or gray.ndim != 2:
            raise ValueError("detector requires a grayscale frame")
        if gray.dtype != np.uint8:
            gray = np.clip(gray, 0, 255).astype(np.uint8)

        if cv2 is None:
            return self._detect_numpy(gray, timestamp=float(timestamp))

        work = gray
        blur = int(self.config.blur_size)
        if blur > 1:
            blur += 1 if blur % 2 == 0 else 0
            work = cv2.GaussianBlur(work, (blur, blur), 0)

        threshold = self._threshold_for(work)
        _, mask = cv2.threshold(work, threshold, 255, cv2.THRESH_BINARY)
        kernel_size = int(self.config.morph_kernel)
        if kernel_size > 1:
            kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        best = None
        best_score = -1.0
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if not self.config.min_area <= area <= self.config.max_area:
                continue
            perimeter = float(cv2.arcLength(contour, True))
            if perimeter <= 0:
                continue
            circularity = 4.0 * math.pi * area / (perimeter * perimeter)
            if circularity < self.config.min_circularity:
                continue
            moments = cv2.moments(contour)
            if moments["m00"] <= 0:
                continue
            contour_mask = np.zeros(gray.shape, dtype=np.uint8)
            cv2.drawContours(contour_mask, [contour], -1, 255, -1)
            brightness = float(cv2.mean(gray, mask=contour_mask)[0])
            if brightness < self.config.min_brightness:
                continue
            center_x = float(moments["m10"] / moments["m00"])
            center_y = float(moments["m01"] / moments["m00"])
            score = area * (brightness / 255.0) * (0.5 + circularity)
            if score > best_score:
                best_score = score
                best = (center_x, center_y, area, brightness, circularity)

        if best is None:
            return None
        center_x, center_y, area, brightness, circularity = best
        confidence = self._confidence(area, brightness, circularity)
        return Detection(
            center_x=center_x,
            center_y=center_y,
            area=area,
            brightness=brightness,
            circularity=circularity,
            confidence=confidence,
            threshold=threshold,
            timestamp=float(timestamp),
        )

    def _detect_numpy(
        self, gray: np.ndarray, *, timestamp: float
    ) -> Optional[Detection]:
        threshold = self._threshold_for(gray)
        mask = gray > threshold
        visited = np.zeros(mask.shape, dtype=bool)
        height, width = mask.shape
        best = None
        best_score = -1.0

        for start_y, start_x in np.argwhere(mask):
            if visited[start_y, start_x]:
                continue
            stack = [(int(start_y), int(start_x))]
            visited[start_y, start_x] = True
            pixels = []
            perimeter = 0
            while stack:
                y, x = stack.pop()
                pixels.append((y, x))
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = y + dy, x + dx
                    if ny < 0 or nx < 0 or ny >= height or nx >= width:
                        perimeter += 1
                    elif not mask[ny, nx]:
                        perimeter += 1
                    elif not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))

            area = float(len(pixels))
            if not self.config.min_area <= area <= self.config.max_area:
                continue
            circularity = (
                4.0 * math.pi * area / float(perimeter * perimeter)
                if perimeter
                else 0.0
            )
            if circularity < self.config.min_circularity:
                continue
            ys = np.fromiter((p[0] for p in pixels), dtype=float)
            xs = np.fromiter((p[1] for p in pixels), dtype=float)
            brightness = float(gray[ys.astype(int), xs.astype(int)].mean())
            if brightness < self.config.min_brightness:
                continue
            score = area * (brightness / 255.0) * (0.5 + circularity)
            if score > best_score:
                best_score = score
                best = (
                    float(xs.mean()),
                    float(ys.mean()),
                    area,
                    brightness,
                    circularity,
                )

        if best is None:
            return None
        center_x, center_y, area, brightness, circularity = best
        return Detection(
            center_x=center_x,
            center_y=center_y,
            area=area,
            brightness=brightness,
            circularity=circularity,
            confidence=self._confidence(area, brightness, circularity),
            threshold=threshold,
            timestamp=timestamp,
        )

    def _threshold_for(self, gray: np.ndarray) -> int:
        if self.config.auto_threshold:
            percentile = float(np.percentile(gray, self.config.auto_percentile))
            return int(max(0, min(254, percentile - self.config.auto_margin)))
        return int(self.config.threshold)

    def _confidence(
        self, area: float, brightness: float, circularity: float
    ) -> float:
        area_quality = min(1.0, area / (self.config.min_area * 4.0))
        brightness_quality = max(
            0.0,
            min(
                1.0,
                (brightness - self.config.min_brightness)
                / max(1.0, 255.0 - self.config.min_brightness),
            ),
        )
        circularity_quality = max(
            0.0,
            min(
                1.0,
                (circularity - self.config.min_circularity)
                / max(1e-6, 1.0 - self.config.min_circularity),
            ),
        )
        return (area_quality + brightness_quality + circularity_quality) / 3.0
