from __future__ import annotations

import logging
import time
from typing import Iterator

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class RTSPReader:
    def __init__(self, rtsp_url: str, reconnect_delay_seconds: int = 5) -> None:
        self._rtsp_url = rtsp_url
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._cap: cv2.VideoCapture | None = None

    def _open(self) -> cv2.VideoCapture:
        logger.info("Opening RTSP stream: %s", self._rtsp_url)
        cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            raise RuntimeError(f"Unable to open RTSP stream: {self._rtsp_url}")
        return cap

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            try:
                if self._cap is None:
                    self._cap = self._open()

                ok, frame = self._cap.read()
                if not ok or frame is None:
                    raise RuntimeError("Failed to read frame from RTSP stream")
                yield frame
            except Exception:
                logger.exception("RTSP stream read failed, reconnecting: %s", self._rtsp_url)
                if self._cap is not None:
                    self._cap.release()
                    self._cap = None
                time.sleep(self._reconnect_delay_seconds)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None