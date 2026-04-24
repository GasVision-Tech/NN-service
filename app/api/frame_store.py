from __future__ import annotations

import threading


class FrameStore:
    """Thread-safe latest annotated JPEG per camera, updated by pipeline threads."""

    def __init__(self) -> None:
        self._frames: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def update(self, camera_code: str, jpeg_bytes: bytes) -> None:
        with self._lock:
            self._frames[camera_code] = jpeg_bytes

    def get(self, camera_code: str) -> bytes | None:
        with self._lock:
            return self._frames.get(camera_code)

    def cameras(self) -> list[str]:
        with self._lock:
            return sorted(self._frames.keys())
