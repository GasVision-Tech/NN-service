from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.domain.models import StreamConfig

logger = logging.getLogger(__name__)


def load_streams_config(path: str) -> list[StreamConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    streams: list[StreamConfig] = []
    for item in data.get("streams", []):
        zones_cfg = item.get("zones_config_path")
        streams.append(
            StreamConfig(
                station_code=item["station_code"],
                camera_code=item["camera_code"],
                rtsp_url=item["rtsp_url"],
                enabled=bool(item.get("enabled", True)),
                zones_config_path=str(zones_cfg) if zones_cfg else None,
            )
        )
    return [stream for stream in streams if stream.enabled]


@dataclass(frozen=True, slots=True)
class ChecksConfig:
    """
    Карта `какие сценарии активны на какой камере`.

    `defaults` — fallback для камер, не перечисленных в `per_camera`.
    `per_camera` — точечные оверрайды (camera_code → frozenset проверок).

    Если файл не найден или пуст — возвращается `ChecksConfig(None, {})` и
    `for_camera()` отдаёт `None`. Это сигнал движку: фильтрации нет, гоним
    все триггеры. Так сохраняется обратная совместимость до появления конфига.
    """

    defaults: frozenset[str] | None
    per_camera: dict[str, frozenset[str]]

    def for_camera(self, camera_code: str) -> frozenset[str] | None:
        if camera_code in self.per_camera:
            return self.per_camera[camera_code]
        return self.defaults


def load_checks_config(path: str) -> ChecksConfig:
    """
    Читает config/checks.yaml. Игнорирует разделы `implemented_checks` и
    `roadmap` — они только документация. Если файл не существует, валидно
    отдаёт пустой ChecksConfig (фильтрация отключена).
    """
    file = Path(path)
    if not file.is_file():
        logger.info("checks config not found at %s — per-camera filtering disabled", path)
        return ChecksConfig(defaults=None, per_camera={})

    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}

    defaults_block = data.get("defaults") or {}
    defaults_list = defaults_block.get("enabled_checks")
    defaults: frozenset[str] | None = (
        frozenset(defaults_list) if isinstance(defaults_list, list) else None
    )

    per_camera: dict[str, frozenset[str]] = {}
    for camera_code, camera_block in (data.get("cameras") or {}).items():
        if not isinstance(camera_block, dict):
            continue
        checks = camera_block.get("enabled_checks")
        if isinstance(checks, list):
            per_camera[str(camera_code)] = frozenset(checks)

    logger.info(
        "Loaded checks config: defaults=%s overrides=%d cameras",
        sorted(defaults) if defaults else "<none>",
        len(per_camera),
    )
    return ChecksConfig(defaults=defaults, per_camera=per_camera)
