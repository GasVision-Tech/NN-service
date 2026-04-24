"""
DEPRECATED: старая тривиальная логика «любая машина = событие» заменена
на ZoneScenarioEngine (app.services.zone_scenario_engine).

Файл оставлен для обратной совместимости импортов; новая реализация
ожидает DetectionBatch с track_id и Zone-конфиг на камеру.
"""
from __future__ import annotations

from app.services.zone_scenario_engine import ZoneScenarioEngine

# Алиас для старого имени
ScenarioEngine = ZoneScenarioEngine

__all__ = ["ScenarioEngine", "ZoneScenarioEngine"]
