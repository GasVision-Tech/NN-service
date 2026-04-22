from __future__ import annotations

import logging

import httpx

from app.domain.models import EventCreated, ScenarioTrigger

logger = logging.getLogger(__name__)


class EventServiceClient:
    def __init__(self, base_url: str, source: str, default_status: str, default_severity: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._source = source
        self._default_status = default_status
        self._default_severity = default_severity

    def create_event(self, trigger: ScenarioTrigger, image_url: str) -> EventCreated:
        payload = {
            "source": self._source,
            "title": trigger.title,
            "station_code": trigger.station_code,
            "camera_code": trigger.camera_code,
            "severity": trigger.severity or self._default_severity,
            "status": self._default_status,
            "created_at": trigger.triggered_at.isoformat(),
            "media": [
                {
                    "kind": "image",
                    "s3_url": image_url,
                }
            ],
        }
        logger.info("Creating event in event-srv: %s", payload)
        response = httpx.post(f"{self._base_url}/v1/events", json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        return EventCreated(
            event_id=data["id"],
            station_code=data["station_code"],
            camera_code=data.get("camera_code") or "",
            title=data["title"],
        )

    def attach_clip(self, event_id: int, clip_url: str) -> dict:
        payload = {"kind": "clip", "s3_url": clip_url}
        logger.info("Attaching clip to event %s: %s", event_id, payload)
        response = httpx.post(f"{self._base_url}/v1/events/{event_id}/media", json=payload, timeout=30.0)
        response.raise_for_status()
        return response.json()

    def healthcheck(self) -> bool:
        try:
            response = httpx.get(f"{self._base_url}/health", timeout=5.0)
            response.raise_for_status()
            return True
        except Exception:
            logger.exception("event-srv healthcheck failed")
            return False