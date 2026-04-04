import logging
from dataclasses import dataclass

from ultralytics import YOLO

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    has_target: bool
    target_count: int
    max_confidence: float


class YoloDetector:
    def __init__(self) -> None:
        logger.info(
            "Loading YOLO model=%s device=%s classes=%s",
            settings.yolo_model,
            settings.yolo_device,
            settings.yolo_class_ids,
        )
        self.model = YOLO(settings.yolo_model)

    def detect(self, frame) -> DetectionResult:
        results = self.model.predict(
            source=frame,
            conf=settings.yolo_confidence,
            iou=settings.yolo_iou,
            imgsz=settings.yolo_image_size,
            device=settings.yolo_device,
            classes=settings.yolo_class_ids,
            verbose=False,
        )

        result = results[0]
        boxes = result.boxes

        if boxes is None or len(boxes) == 0:
            return DetectionResult(has_target=False, target_count=0, max_confidence=0.0)

        confidences = [float(conf) for conf in boxes.conf.tolist()]
        return DetectionResult(
            has_target=True,
            target_count=len(confidences),
            max_confidence=max(confidences),
        )
