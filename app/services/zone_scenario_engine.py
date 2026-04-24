"""
ZoneScenarioEngine — бизнес-правила поверх Detection+track_id и Zone.

Один экземпляр — одна камера. Хранит EventTracker (таймеры/ReID/cooldown)
и набор Zone (forbidden/column/station), прочитанный из config/zones/*.json.

На вход: DetectionBatch с track_id (от YoloTrackingDetector).
На выход: list[ScenarioTrigger] — 0..N новых сработок за этот кадр.

В отличие от старого ScenarioEngine может вернуть несколько триггеров
за кадр (например: один человек в запрещённой зоне + одна машина стоит
у колонки дольше порога).
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Tuple

import numpy as np

from app.adapters.zone_manager import (
    Zone,
    bbox_bottom_center,
    bbox_center,
    point_in_zone,
)
from app.domain.models import Detection, DetectionBatch, ScenarioTrigger
from app.services.event_tracker import EventTracker

logger = logging.getLogger(__name__)


class ZoneScenarioEngine:
    def __init__(
        self,
        *,
        zones: List[Zone],
        event_tracker: EventTracker,
        person_labels: Iterable[str] = ("person",),
        vehicle_labels: Iterable[str] = ("car", "bus", "truck", "motorcycle"),
        person_car_proximity_px: int = 200,
    ) -> None:
        self._zones = zones
        self._tracker = event_tracker
        self._person_labels = set(person_labels)
        self._vehicle_labels = set(vehicle_labels)
        self._person_car_proximity_px = int(person_car_proximity_px)

        self._forbidden = [z for z in zones if z.zone_type == "forbidden"]
        self._columns = [z for z in zones if z.zone_type == "column"]
        self._stations = [z for z in zones if z.zone_type == "station"]

        logger.info(
            "ZoneScenarioEngine ready: forbidden=%s columns=%s stations=%s",
            len(self._forbidden), len(self._columns), len(self._stations),
        )

    # ------------------------------------------------------------------
    # Публичное API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        frame: np.ndarray,
        batch: DetectionBatch,
    ) -> list[ScenarioTrigger]:
        """
        Прогоняет все 4 сценария по кадру и возвращает сработавшие триггеры.
        """
        persons, cars = self._split_by_kind(batch.detections)

        # Для has_car_near нужен список всех bbox'ов машин (без track_id)
        car_bboxes = [d.bbox_xyxy for d in cars]

        # --- 1. Person in forbidden
        persons_in_forbidden = self._persons_in_zones(
            persons, self._forbidden, use_bottom_center=True,
        )

        # --- 2. Person at column without car nearby
        persons_at_column: Dict[int, Tuple[str, Tuple[int, int]]] = {}
        for det in persons:
            if det.track_id is None:
                continue
            pt = bbox_bottom_center(det.bbox_xyxy)
            for zone in self._columns:
                if point_in_zone(pt, zone) and not self._has_car_near(det.bbox_xyxy, car_bboxes):
                    persons_at_column[det.track_id] = (zone.id, pt)
                    break

        # --- 3. Person too long at station (срабатывает независимо от 1 и 2)
        persons_at_station = self._persons_in_zones(
            persons, self._stations, use_bottom_center=True,
        )

        # --- 4. Car too long at column
        cars_at_column: Dict[int, Tuple[str, Tuple[int, int]]] = {}
        for det in cars:
            if det.track_id is None:
                continue
            pt = bbox_center(det.bbox_xyxy)
            for zone in self._columns:
                if point_in_zone(pt, zone):
                    cars_at_column[det.track_id] = (zone.id, pt)
                    break

        triggers: list[ScenarioTrigger] = []
        triggers.extend(self._tracker.update_persons_in_forbidden(persons_in_forbidden, frame))
        triggers.extend(self._tracker.update_persons_at_column(persons_at_column, frame))
        triggers.extend(self._tracker.update_persons_at_station(persons_at_station, frame))
        triggers.extend(self._tracker.update_cars_at_column(cars_at_column, frame))
        return triggers

    # ------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------

    def _split_by_kind(
        self,
        detections: list[Detection],
    ) -> Tuple[list[Detection], list[Detection]]:
        persons: list[Detection] = []
        cars: list[Detection] = []
        for d in detections:
            if d.label in self._person_labels:
                persons.append(d)
            elif d.label in self._vehicle_labels:
                cars.append(d)
        return persons, cars

    def _persons_in_zones(
        self,
        persons: list[Detection],
        zones: list[Zone],
        *,
        use_bottom_center: bool,
    ) -> Dict[int, Tuple[str, Tuple[int, int]]]:
        out: Dict[int, Tuple[str, Tuple[int, int]]] = {}
        if not zones:
            return out
        for det in persons:
            if det.track_id is None:
                continue
            pt = (
                bbox_bottom_center(det.bbox_xyxy)
                if use_bottom_center
                else bbox_center(det.bbox_xyxy)
            )
            for zone in zones:
                if point_in_zone(pt, zone):
                    out[det.track_id] = (zone.id, pt)
                    break
        return out

    def _has_car_near(
        self,
        person_bbox: Tuple[int, int, int, int],
        car_bboxes: list[Tuple[int, int, int, int]],
    ) -> bool:
        px, py = bbox_center(person_bbox)
        thr = self._person_car_proximity_px
        for cb in car_bboxes:
            cx, cy = bbox_center(cb)
            if abs(px - cx) < thr and abs(py - cy) < thr:
                return True
        return False
