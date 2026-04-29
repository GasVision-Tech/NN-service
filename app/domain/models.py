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
    # Опциональный путь до JSON с зонами (forbidden/column/station).
    # Если None — камера работает без зон, стреляют только таймерные сценарии
    # (person_too_long_at_station / car_too_long_at_station) на всё изображение.
    zones_config_path: str | None = None
    # Какие проверки активны для этой камеры. Если None — фильтра нет, движок
    # отдаёт все триггеры наверх. Заполняется из config/checks.yaml в runner.
    enabled_checks: frozenset[str] | None = None


@dataclass(slots=True)
class Detection:
    label: str
    confidence: float
    bbox_xyxy: tuple[int, int, int, int]
    # Стабильный ID от трекера (ByteTrack/BoT-SORT внутри ultralytics).
    # None, если детектор не в tracking-режиме или трекер пока не назначил ID.
    track_id: int | None = None
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
    # track_id объекта, из-за которого сработал сценарий (person/car).
    # None для сценариев, не связанных с конкретным треком.
    track_id: int | None = None
    # Сколько объект пробыл в зоне/кадре до срабатывания (сек).
    duration_sec: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventCreated:
    event_id: int
    station_code: str
    camera_code: str
    title: str
