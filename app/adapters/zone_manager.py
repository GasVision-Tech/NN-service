"""
Zone manager: загрузка конфигурации зон и геометрические утилиты.

Формат config/zones/<camera_code>.json:
{
  "forbidden_zones": [ {"id": "forbidden_1", "name": "...", "polygon": [[x,y],...]} ],
  "column_zones":    [ {"id": "column_1",    "name": "...", "polygon": [[x,y],...]} ],
  "station_zones":   [ {"id": "station",     "name": "...", "polygon": [[x,y],...]} ]
}

Если файл не задан для камеры — load_zones_or_fallback() отдаёт виртуальные
full-frame зоны типа station + column, чтобы таймерные сценарии всё равно работали.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ZoneType = Literal["forbidden", "column", "station"]

# Ключи в JSON -> тип зоны
_ZONE_SECTIONS: tuple[tuple[str, ZoneType], ...] = (
    ("forbidden_zones", "forbidden"),
    ("column_zones", "column"),
    ("station_zones", "station"),
)


@dataclass
class Zone:
    id: str
    name: str
    zone_type: ZoneType
    polygon: np.ndarray  # shape (N, 2), dtype int32


def load_zones(path: str | Path) -> List[Zone]:
    """Грузит зоны из JSON. Кидает исключение, если файл невалидный."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))

    zones: list[Zone] = []
    for json_key, ztype in _ZONE_SECTIONS:
        for z in data.get(json_key, []) or []:
            zones.append(
                Zone(
                    id=z["id"],
                    name=z.get("name", z["id"]),
                    zone_type=ztype,
                    polygon=np.array(z["polygon"], dtype=np.int32),
                )
            )
    return zones


def load_zones_or_fallback(
    path: str | None,
    *,
    fallback_width: int = 1920,
    fallback_height: int = 1080,
) -> List[Zone]:
    """
    Грузит зоны из path, либо возвращает виртуальные full-frame station+column,
    если путь не задан или файл отсутствует. Размеры fallback'а — это
    верхняя оценка; фактический чекап через cv2.pointPolygonTest всё равно
    даст True для любой точки в этом прямоугольнике.
    """
    if path:
        p = Path(path)
        if p.exists():
            zones = load_zones(p)
            logger.info("Loaded %s zones from %s", len(zones), p)
            return zones
        logger.warning("Zones config path %s does not exist — using full-frame fallback", p)
    else:
        logger.info("No zones config path provided — using full-frame fallback")

    big_poly = np.array(
        [
            [0, 0],
            [fallback_width, 0],
            [fallback_width, fallback_height],
            [0, fallback_height],
        ],
        dtype=np.int32,
    )
    return [
        Zone(id="station_fullframe", name="Full-frame station", zone_type="station", polygon=big_poly),
        Zone(id="column_fullframe", name="Full-frame column", zone_type="column", polygon=big_poly),
    ]


def point_in_zone(point: Tuple[int, int], zone: Zone) -> bool:
    """True, если точка внутри polygon'а или на границе."""
    return cv2.pointPolygonTest(
        zone.polygon,
        (float(point[0]), float(point[1])),
        False,
    ) >= 0


def bbox_bottom_center(bbox: Tuple[int, int, int, int]) -> Tuple[int, int]:
    """Нижний центр bbox — "ноги" объекта. Стабильнее всего для людей."""
    x1, y1, x2, y2 = map(int, bbox)
    return ((x1 + x2) // 2, y2)


def bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[int, int]:
    x1, y1, x2, y2 = map(int, bbox)
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def draw_zones(frame: np.ndarray, zones: List[Zone]) -> np.ndarray:
    """Рисует зоны на кадре (для дебага и для draw_zones.py)."""
    color_by_type = {
        "forbidden": (0, 0, 200),
        "column": (0, 200, 0),
        "station": (200, 200, 0),
    }
    for zone in zones:
        color = color_by_type.get(zone.zone_type, (200, 200, 200))
        cv2.polylines(frame, [zone.polygon], isClosed=True, color=color, thickness=2)
        cx = int(zone.polygon[:, 0].mean())
        cy = int(zone.polygon[:, 1].mean())
        cv2.putText(
            frame,
            zone.name,
            (cx - 40, cy),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
        )
    return frame
