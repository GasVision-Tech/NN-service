from __future__ import annotations

import logging
from typing import Protocol

import numpy as np
from ultralytics import YOLO

from app.domain.models import Detection, DetectionBatch

logger = logging.getLogger(__name__)


class Detector(Protocol):
    def detect(self, frame: np.ndarray) -> DetectionBatch:
        ...


class YoloVehicleDetector:
    """
    Default detector adapter.

    CV engineer can safely replace this entire class with their own model wrapper,
    while keeping the returned `DetectionBatch` contract unchanged.
    """

    def __init__(
        self,
        model_path: str,
        device: str,
        confidence: float,
        iou: float,
        allowed_labels: set[str],
    ) -> None:
        self._model = YOLO(model_path)
        self._device = device
        self._confidence = confidence
        self._iou = iou
        self._allowed_labels = allowed_labels

    def detect(self, frame: np.ndarray) -> DetectionBatch:
        results = self._model.predict(
            source=frame,
            conf=self._confidence,
            iou=self._iou,
            device=self._device,
            verbose=False,
        )
        result = results[0]
        names = result.names

        detections: list[Detection] = []
        if result.boxes is not None:
            for box in result.boxes:
                cls_id = int(box.cls.item())
                label = str(names[cls_id])
                if self._allowed_labels and label not in self._allowed_labels:
                    continue
                conf = float(box.conf.item())
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                detections.append(
                    Detection(
                        label=label,
                        confidence=conf,
                        bbox_xyxy=(x1, y1, x2, y2),
                        metadata={"class_id": cls_id},
                    )
                )

        return DetectionBatch(
            detections=detections,
            model_name=getattr(self._model, "ckpt_path", "yolo"),
            raw=result,
        )