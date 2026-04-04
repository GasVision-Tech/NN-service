import logging
from typing import Any

import requests

from app.core.config import settings
from app.models.camera import CameraConfig
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


class EventClient:
    def __init__(self) -> None:
        self.base_url = settings.event_service_base_url.rstrip("/")
        self.timeout = settings.event_service_timeout_seconds

    def create_vehicle_detected_event(
        self,
        camera: CameraConfig,
        detections_count: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "source": settings.event_source,
            "title": settings.event_title,
            "station_code": camera.station_code,
            "camera_code": camera.camera_code,
            "severity": settings.event_severity,
            "status": settings.event_status,
            "created_at": utc_now_iso(),
            "media": [],
        }

        if extra:
            payload["title"] = f"{payload['title']} | cars={detections_count} | {extra}"

        url = f"{self.base_url}/v1/events"
        logger.info("Sending event to %s for camera=%s", url, camera.camera_code)

        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
