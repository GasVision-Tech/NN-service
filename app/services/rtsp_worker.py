import logging
import time
from dataclasses import dataclass

import cv2

from app.clients.event_client import EventClient
from app.core.config import settings
from app.models.camera import CameraConfig
from app.services.detector import DetectionResult, YoloDetector

logger = logging.getLogger(__name__)


@dataclass
class CameraRuntimeState:
    camera_code: str
    stream_opened: bool = False
    last_detection_at_monotonic: float | None = None
    object_present: bool = False
    sent_events: int = 0
    read_errors: int = 0
    last_target_count: int = 0


class RtspWorker:
    def __init__(self, camera: CameraConfig, detector: YoloDetector, event_client: EventClient) -> None:
        self.camera = camera
        self.detector = detector
        self.event_client = event_client
        self.state = CameraRuntimeState(camera_code=camera.camera_code)
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def run_forever(self) -> None:
        while not self._stopped:
            try:
                self._run_stream_loop()
            except Exception:
                logger.exception("Unhandled error in camera worker camera=%s", self.camera.camera_code)
                time.sleep(settings.reconnect_delay_seconds)

    def _run_stream_loop(self) -> None:
        logger.info("Opening RTSP stream camera=%s", self.camera.camera_code)
        cap = cv2.VideoCapture(self.camera.rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, settings.rtsp_open_timeout_seconds * 1000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, settings.rtsp_read_timeout_seconds * 1000)

        if not cap.isOpened():
            self.state.stream_opened = False
            logger.warning("Cannot open stream camera=%s", self.camera.camera_code)
            time.sleep(settings.reconnect_delay_seconds)
            return

        self.state.stream_opened = True
        empty_frames = 0
        frame_index = 0

        try:
            while not self._stopped:
                ok, frame = cap.read()
                if not ok or frame is None:
                    empty_frames += 1
                    self.state.read_errors += 1
                    if empty_frames >= settings.max_empty_frames:
                        logger.warning(
                            "Too many empty frames camera=%s, reconnecting",
                            self.camera.camera_code,
                        )
                        break
                    continue

                empty_frames = 0
                frame_index += 1

                if frame_index % max(settings.frame_skip, 1) != 0:
                    continue

                detection = self.detector.detect(frame)
                self._handle_detection(detection)
        finally:
            cap.release()
            self.state.stream_opened = False
            logger.info("Stream released camera=%s", self.camera.camera_code)

    def _handle_detection(self, detection: DetectionResult) -> None:
        now = time.monotonic()
        self.state.last_target_count = detection.target_count

        if not detection.has_target:
            if self.state.object_present:
                logger.info("Object disappeared camera=%s", self.camera.camera_code)
            self.state.object_present = False
            return

        should_send = False
        if not self.state.object_present:
            should_send = True
        elif self.state.last_detection_at_monotonic is None:
            should_send = True
        else:
            elapsed = now - self.state.last_detection_at_monotonic
            should_send = elapsed >= settings.event_cooldown_seconds

        self.state.object_present = True

        if not should_send:
            return

        self.event_client.create_vehicle_detected_event(
            camera=self.camera,
            detections_count=detection.target_count,
            extra={
                "max_confidence": round(detection.max_confidence, 3),
            },
        )
        self.state.last_detection_at_monotonic = now
        self.state.sent_events += 1
        logger.info(
            "Event sent camera=%s target_count=%s total_sent=%s",
            self.camera.camera_code,
            detection.target_count,
            self.state.sent_events,
        )
