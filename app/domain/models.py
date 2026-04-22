from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import numpy as np


@dataclass(slots=True)
class StreamConfig:
    station_code: str
    camera_code: str
    rtsp_url: str
    enabled: bool = True


@dataclass(slots=True)
class Detection:
    label: str
    confidence: float
    bbox_xyxy: tuple[int, int, int, int]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DetectionBatch:
    detections: list[Detection]
    model_name: str
    raw: Any | None = None


@dataclass(slots=True)
class ScenarioTrigger:
    scenario_key: str
    title: str
    severity: str
    station_code: str
    camera_code: str
    triggered_at: datetime
    snapshot_frame: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventCreated:
    event_id: int
    station_code: str
    camera_code: str
    title: str