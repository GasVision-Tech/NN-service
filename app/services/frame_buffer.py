from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np


@dataclass(slots=True)
class TimedFrame:
    frame: np.ndarray
    ts: datetime


class FrameRingBuffer:
    def __init__(self, maxlen: int) -> None:
        self._buffer: deque[TimedFrame] = deque(maxlen=maxlen)

    def append(self, frame: np.ndarray) -> None:
        self._buffer.append(TimedFrame(frame=frame.copy(), ts=datetime.now(timezone.utc)))

    def snapshot(self) -> list[TimedFrame]:
        return list(self._buffer)

    def __len__(self) -> int:
        return len(self._buffer)