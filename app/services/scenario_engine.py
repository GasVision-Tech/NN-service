from __future__ import annotations

from datetime import datetime, timezone
import logging
import numpy as np

from app.domain.models import DetectionBatch, ScenarioTrigger

logger = logging.getLogger(__name__)


class ScenarioEngine:
    """
    Business rules on top of raw detector output.

    Replace this logic later with real station-specific scenarios.
    For now it triggers an event if at least one vehicle was detected.
    """

    def evaluate(
        self,
        *,
        station_code: str,
        camera_code: str,
        frame: np.ndarray,
        batch: DetectionBatch,
    ) -> ScenarioTrigger | None:
        if not batch.detections:
            return None

        vehicle_count = len(batch.detections)
        top_confidence = max(d.confidence for d in batch.detections)
        title = f"Vehicle detected ({vehicle_count})"

        logger.info(
            "Scenario matched for station=%s camera=%s vehicle_count=%s top_confidence=%.3f",
            station_code,
            camera_code,
            vehicle_count,
            top_confidence,
        )

        return ScenarioTrigger(
            scenario_key="vehicle_detected",
            title=title,
            severity="med",
            station_code=station_code,
            camera_code=camera_code,
            triggered_at=datetime.now(timezone.utc),
            snapshot_frame=frame.copy(),
            metadata={
                "vehicle_count": vehicle_count,
                "top_confidence": top_confidence,
                "detections": [
                    {
                        "label": det.label,
                        "confidence": det.confidence,
                        "bbox_xyxy": det.bbox_xyxy,
                    }
                    for det in batch.detections
                ],
                "model_name": batch.model_name,
            },
        )