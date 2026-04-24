from __future__ import annotations

import cv2
import numpy as np

from app.domain.models import DetectionBatch

_COLORS: dict[str, tuple[int, int, int]] = {
    "person":     (255,  80,   0),
    "car":        (  0, 200,  50),
    "bus":        (  0, 200, 200),
    "truck":      (  0, 140, 255),
    "motorcycle": (200,   0, 200),
}
_DEFAULT_COLOR = (180, 180, 180)


def draw_detections(frame: np.ndarray, batch: DetectionBatch) -> np.ndarray:
    out = frame.copy()
    for det in batch.detections:
        x1, y1, x2, y2 = det.bbox_xyxy
        color = _COLORS.get(det.label, _DEFAULT_COLOR)

        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

        if det.track_id is not None:
            label = f"#{det.track_id} {det.label} {det.confidence:.2f}"
        else:
            label = f"{det.label} {det.confidence:.2f}"

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        bg_y1 = max(y1 - th - 6, 0)
        cv2.rectangle(out, (x1, bg_y1), (x1 + tw + 4, y1), color, -1)
        cv2.putText(out, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    return out
