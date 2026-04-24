"""
EventTracker — ReID + таймеры + стабильные триггеры.

Портировано из CV-module/event_manager.py с одним ключевым отличием:
НЕ шлёт HTTP webhook сам — только возвращает список ScenarioTrigger
в StreamPipeline, который уже занимается отправкой в event-service.

Контракт update_*():
    input: active = {track_id: (zone_id, (x, y))}   — объекты в зоне на этом кадре
    output: list[ScenarioTrigger]                    — новые сработки (обычно 0-1)

Per-(event_type, track_id) cooldown гарантирует, что один и тот же
сценарий для одного и того же объекта не стрельнет чаще чем раз в cooldown_sec.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple

import numpy as np

from app.domain.models import ScenarioTrigger

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    PERSON_IN_FORBIDDEN = "person_in_forbidden_zone"
    PERSON_WITHOUT_CAR = "person_without_car_at_column"
    PERSON_TOO_LONG = "person_too_long_at_station"
    CAR_TOO_LONG = "car_too_long_at_column"


# Severity mapping для отправки в event-service (low|med|high)
SEVERITY_BY_EVENT: Dict[EventType, str] = {
    EventType.PERSON_IN_FORBIDDEN: "high",
    EventType.PERSON_WITHOUT_CAR: "med",
    EventType.PERSON_TOO_LONG: "med",
    EventType.CAR_TOO_LONG: "low",
}


def _title_for(event_type: EventType, track_id: int, zone_id: str, elapsed: float) -> str:
    if event_type == EventType.PERSON_IN_FORBIDDEN:
        return f"Человек #{track_id} в запрещённой зоне «{zone_id}»"
    if event_type == EventType.PERSON_WITHOUT_CAR:
        return f"Человек #{track_id} у колонки «{zone_id}» без авто {elapsed:.0f}с"
    if event_type == EventType.PERSON_TOO_LONG:
        return f"Человек #{track_id} на АЗС уже {elapsed:.0f}с"
    if event_type == EventType.CAR_TOO_LONG:
        return f"Машина #{track_id} у колонки «{zone_id}» {elapsed:.0f}с"
    return f"{event_type.value} track={track_id}"


@dataclass
class TrackedObject:
    track_id: int
    zone_id: str
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_position: Tuple[int, int] = (0, 0)
    fired_events: set[EventType] = field(default_factory=set)

    def elapsed(self) -> float:
        return self.last_seen - self.first_seen

    def mark_fired(self, event_type: EventType) -> None:
        self.fired_events.add(event_type)

    def already_fired(self, event_type: EventType) -> bool:
        return event_type in self.fired_events


@dataclass
class GhostTrack:
    """Трек, пропавший из детектора — хранится GRACE_SEC секунд на случай возврата."""
    track_id: int
    zone_id: str
    last_position: Tuple[int, int]
    disappeared_at: float = field(default_factory=time.time)
    accumulated_sec: float = 0.0


class ReIdMatcher:
    """
    Сопоставляет новые track_id с пропавшими объектами по близости в пикселях.

    Нужен потому, что встроенный трекер ultralytics иногда теряет ID при
    коротком пропадании бокса и после реаппира выдаёт НОВЫЙ id. Без ReID
    это сбрасывает таймер person_without_car.

    Риск: если два разных человека стоят рядом в пределах RADIUS_PX,
    новый может унаследовать таймер от первого. На практике допустимо —
    severity этих событий не делает ложное срабатывание критичным.
    """

    def __init__(self, grace_sec: float = 8.0, radius_px: int = 150) -> None:
        self._grace_sec = grace_sec
        self._radius_px = radius_px
        self._ghosts: Dict[int, GhostTrack] = {}
        self._lock = threading.Lock()

    def add_ghost(self, obj: TrackedObject) -> None:
        with self._lock:
            self._ghosts[obj.track_id] = GhostTrack(
                track_id=obj.track_id,
                zone_id=obj.zone_id,
                last_position=obj.last_position,
                accumulated_sec=obj.elapsed(),
            )
            logger.debug(
                "[REID] Ghost created: track=%s zone=%s elapsed=%.1fs pos=%s",
                obj.track_id, obj.zone_id, obj.elapsed(), obj.last_position,
            )

    def find_match(
        self,
        new_track_id: int,
        position: Tuple[int, int],
        zone_id: str,
    ) -> Optional[GhostTrack]:
        now = time.time()
        with self._lock:
            self._cleanup(now)

            best: Optional[GhostTrack] = None
            best_dist = float("inf")

            for ghost in self._ghosts.values():
                if ghost.zone_id != zone_id:
                    continue
                dx = position[0] - ghost.last_position[0]
                dy = position[1] - ghost.last_position[1]
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < self._radius_px and dist < best_dist:
                    best_dist = dist
                    best = ghost

            if best is not None:
                logger.info(
                    "[REID] matched new=%s <- ghost=%s dist=%.0f age=%.1fs accumulated=%.1fs",
                    new_track_id, best.track_id, best_dist,
                    now - best.disappeared_at, best.accumulated_sec,
                )
                del self._ghosts[best.track_id]

            return best

    def _cleanup(self, now: float) -> None:
        expired = [tid for tid, g in self._ghosts.items() if now - g.disappeared_at > self._grace_sec]
        for tid in expired:
            logger.debug("[REID] Ghost expired: track=%s", tid)
            del self._ghosts[tid]


class EventTracker:
    """
    Хранит состояние всех отслеживаемых объектов по 4-м сценариям.
    Один экземпляр — одна камера. НЕ thread-safe между камерами
    (используй отдельный EventTracker на поток/камеру).
    """

    def __init__(
        self,
        *,
        station_code: str,
        camera_code: str,
        person_without_car_sec: float,
        person_too_long_at_station_sec: float,
        car_too_long_sec: float,
        event_cooldown_sec: float,
        reid_grace_sec: float = 8.0,
        reid_radius_px: int = 150,
    ) -> None:
        self._station_code = station_code
        self._camera_code = camera_code
        self._threshold_by_event: Dict[EventType, float] = {
            EventType.PERSON_IN_FORBIDDEN: 0.0,  # стреляет сразу
            EventType.PERSON_WITHOUT_CAR: float(person_without_car_sec),
            EventType.PERSON_TOO_LONG: float(person_too_long_at_station_sec),
            EventType.CAR_TOO_LONG: float(car_too_long_sec),
        }
        self._event_cooldown_sec = float(event_cooldown_sec)

        # Кулдаун per-(event_type, track_id): разные события одного объекта друг друга не блокируют
        self._cooldowns: Dict[Tuple[str, int], float] = {}
        self._cooldowns_lock = threading.Lock()

        # Состояния по типам зон
        self._store_forbidden: Dict[int, TrackedObject] = {}
        self._store_column: Dict[int, TrackedObject] = {}
        self._store_station: Dict[int, TrackedObject] = {}
        self._store_car_column: Dict[int, TrackedObject] = {}

        # Отдельные ReID-матчеры (не путаем person с car)
        self._reid_forbidden = ReIdMatcher(reid_grace_sec, reid_radius_px)
        self._reid_column = ReIdMatcher(reid_grace_sec, reid_radius_px)
        self._reid_station = ReIdMatcher(reid_grace_sec, reid_radius_px)
        self._reid_car = ReIdMatcher(reid_grace_sec, reid_radius_px)

    # ------------------------------------------------------------------
    # Публичные update_* — вызываются из ZoneScenarioEngine.
    # frame нужен только для прикрепления snapshot_frame в триггер.
    # ------------------------------------------------------------------

    def update_persons_in_forbidden(
        self,
        active: Dict[int, Tuple[str, Tuple[int, int]]],
        frame: np.ndarray,
    ) -> list[ScenarioTrigger]:
        return self._update(
            self._store_forbidden,
            self._reid_forbidden,
            active,
            EventType.PERSON_IN_FORBIDDEN,
            frame,
        )

    def update_persons_at_column(
        self,
        active: Dict[int, Tuple[str, Tuple[int, int]]],
        frame: np.ndarray,
    ) -> list[ScenarioTrigger]:
        return self._update(
            self._store_column,
            self._reid_column,
            active,
            EventType.PERSON_WITHOUT_CAR,
            frame,
        )

    def update_persons_at_station(
        self,
        active: Dict[int, Tuple[str, Tuple[int, int]]],
        frame: np.ndarray,
    ) -> list[ScenarioTrigger]:
        return self._update(
            self._store_station,
            self._reid_station,
            active,
            EventType.PERSON_TOO_LONG,
            frame,
        )

    def update_cars_at_column(
        self,
        active: Dict[int, Tuple[str, Tuple[int, int]]],
        frame: np.ndarray,
    ) -> list[ScenarioTrigger]:
        return self._update(
            self._store_car_column,
            self._reid_car,
            active,
            EventType.CAR_TOO_LONG,
            frame,
        )

    # ------------------------------------------------------------------
    # Внутренняя логика
    # ------------------------------------------------------------------

    def _update(
        self,
        store: Dict[int, TrackedObject],
        reid: ReIdMatcher,
        active: Dict[int, Tuple[str, Tuple[int, int]]],
        event_type: EventType,
        frame: np.ndarray,
    ) -> list[ScenarioTrigger]:
        now = time.time()
        threshold_sec = self._threshold_by_event[event_type]
        triggers: list[ScenarioTrigger] = []

        # 1) Объекты, вышедшие из зоны, -> ghosts (для ReID)
        gone_ids = set(store.keys()) - set(active.keys())
        for tid in gone_ids:
            obj = store.pop(tid)
            reid.add_ghost(obj)

        # 2) Обработка текущих активных
        for tid, (zone_id, position) in active.items():
            if tid not in store:
                ghost = reid.find_match(tid, position, zone_id)
                obj = TrackedObject(track_id=tid, zone_id=zone_id)
                if ghost is not None:
                    obj.first_seen = now - ghost.accumulated_sec
                else:
                    obj.first_seen = now
                store[tid] = obj

            obj = store[tid]
            obj.last_seen = now
            obj.last_position = position

            elapsed = obj.elapsed()

            if elapsed >= threshold_sec and not obj.already_fired(event_type):
                obj.mark_fired(event_type)
                trigger = self._build_trigger(event_type, zone_id, tid, elapsed, frame)
                if trigger is not None:
                    triggers.append(trigger)

        return triggers

    def _build_trigger(
        self,
        event_type: EventType,
        zone_id: str,
        track_id: int,
        elapsed: float,
        frame: np.ndarray,
    ) -> Optional[ScenarioTrigger]:
        key = (event_type.value, track_id)
        now = time.time()
        with self._cooldowns_lock:
            if now - self._cooldowns.get(key, 0.0) < self._event_cooldown_sec:
                logger.debug("[DEDUP] cooldown active: %s track=%s", event_type.value, track_id)
                return None
            self._cooldowns[key] = now

        title = _title_for(event_type, track_id, zone_id, elapsed)
        severity = SEVERITY_BY_EVENT[event_type]

        logger.warning(
            "[EVENT] station=%s cam=%s %s",
            self._station_code, self._camera_code, title,
        )

        return ScenarioTrigger(
            scenario_key=event_type.value,
            title=title,
            severity=severity,
            station_code=self._station_code,
            camera_code=self._camera_code,
            triggered_at=datetime.now(timezone.utc),
            snapshot_frame=frame.copy(),
            track_id=track_id,
            duration_sec=round(elapsed, 2),
            metadata={
                "event_type": event_type.value,
                "zone_id": zone_id,
                "track_id": track_id,
                "duration_sec": round(elapsed, 2),
            },
        )
