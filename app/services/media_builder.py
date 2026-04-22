from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from app.services.frame_buffer import TimedFrame

logger = logging.getLogger(__name__)


class MediaBuilder:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)
        self._snapshots_dir = self._base_dir / "snapshots"
        self._clips_dir = self._base_dir / "clips"
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._clips_dir.mkdir(parents=True, exist_ok=True)

    def save_snapshot(self, filename: str, frame: np.ndarray) -> Path:
        path = self._snapshots_dir / filename
        ok = cv2.imwrite(str(path), frame)
        if not ok:
            raise RuntimeError(f"Failed to write snapshot to {path}")
        return path

    def save_clip(self, filename: str, frames: list[TimedFrame], fps: int) -> Path:
        if not frames:
            raise ValueError("No frames to write into clip")

        first_frame = frames[0].frame
        height, width = first_frame.shape[:2]
        path = self._clips_dir / filename
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))

        if not writer.isOpened():
            raise RuntimeError(f"Failed to open video writer for {path}")

        try:
            for item in frames:
                writer.write(item.frame)
        finally:
            writer.release()

        logger.info("Clip written: %s", path)
        return path