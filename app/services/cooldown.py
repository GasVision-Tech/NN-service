from __future__ import annotations

from datetime import datetime, timedelta, timezone


class TriggerCooldown:
    def __init__(self, cooldown_seconds: int) -> None:
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._last_seen: dict[str, datetime] = {}

    def allow(self, key: str) -> bool:
        now = datetime.now(timezone.utc)
        previous = self._last_seen.get(key)
        if previous is None or now - previous >= self._cooldown:
            self._last_seen[key] = now
            return True
        return False