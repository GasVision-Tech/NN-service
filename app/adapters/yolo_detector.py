from __future__ import annotations

import logging
from typing import Iterable, Protocol

import numpy as np
from ultralytics import YOLO

from app.domain.models import Detection, DetectionBatch

logger = logging.getLogger(__name__)


class Detector(Protocol):
    def detect(self, frame: np.ndarray) -> DetectionBatch:
        ...


class YoloVehicleDetector:
    """
    Простой детектор без трекинга. Оставлен для совместимости и для
    сценариев, где track_id не нужен.
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
                        track_id=None,
                        metadata={"class_id": cls_id},
                    )
                )

        return DetectionBatch(
            detections=detections,
            model_name=getattr(self._model, "ckpt_path", "yolo"),
            raw=result,
        )


class YoloTrackingDetector:
    """
    Детектор + встроенный трекер ultralytics (ByteTrack по умолчанию).

    ВАЖНО: tracker-state живёт ВНУТРИ экземпляра YOLO — поэтому
    ОДИН YoloTrackingDetector должен обслуживать ровно ОДНУ камеру.
    Шэринг между камерами смешает track_id между потоками.

    Возвращаемый DetectionBatch содержит Detection.track_id, когда
    трекер его выдал; иначе None (обычно первые 1-2 кадра после reset).
    """

    def __init__(
        self,
        *,
        model_path: str,
        device: str,
        confidence: float,
        iou: float,
        class_labels: Iterable[str],
        tracker_config: str = "bytetrack.yaml",
    ) -> None:
        self._model = YOLO(model_path)
        self._device = device
        self._confidence = confidence
        self._iou = iou
        self._tracker_config = tracker_config

        wanted = {label.strip() for label in class_labels if label.strip()}
        names_map = getattr(self._model, "names", {}) or {}
        # names_map: {class_id: label}
        self._class_ids = [cid for cid, lbl in names_map.items() if lbl in wanted]
        unknown = wanted - set(names_map.values())
        if unknown:
            logger.warning("YOLO model has no classes for labels: %s", unknown)
        if not self._class_ids:
            raise ValueError(
                f"None of class_labels={wanted} were found in model classes: "
                f"{sorted(names_map.values())}"
            )

    def detect(self, frame: np.ndarray) -> DetectionBatch:
        results = self._model.track(
            source=frame,
            persist=True,
            classes=self._class_ids,
            conf=self._confidence,
            iou=self._iou,
            device=self._device,
            tracker=self._tracker_config,
            verbose=False,
        )
        result = results[0]
        names = result.names

        detections: list[Detection] = []
        boxes = result.boxes

        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            cls_arr = boxes.cls.cpu().numpy().astype(int)
            conf_arr = boxes.conf.cpu().numpy()
            if boxes.id is not None:
                ids_arr = boxes.id.cpu().numpy().astype(int)
            else:
                ids_arr = np.full(len(boxes), -1, dtype=int)

            for bbox, cls_id, conf, tid in zip(xyxy, cls_arr, conf_arr, ids_arr):
                x1, y1, x2, y2 = (int(v) for v in bbox)
                detections.append(
                    Detection(
                        label=str(names[int(cls_id)]),
                        confidence=float(conf),
                        bbox_xyxy=(x1, y1, x2, y2),
                        track_id=int(tid) if int(tid) >= 0 else None,
                        metadata={"class_id": int(cls_id)},
                    )
                )

        return DetectionBatch(
            detections=detections,
            model_name=getattr(self._model, "ckpt_path", "yolo"),
            raw=result,
        )
