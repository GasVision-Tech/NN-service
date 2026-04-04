import logging
import threading
from pathlib import Path

import yaml

from app.clients.event_client import EventClient
from app.models.camera import CameraConfig
from app.services.detector import YoloDetector
from app.services.rtsp_worker import RtspWorker

logger = logging.getLogger(__name__)


class PipelineService:
    def __init__(self, cameras_config_path: str) -> None:
        self.cameras_config_path = Path(cameras_config_path)
        self.detector = YoloDetector()
        self.event_client = EventClient()
        self.workers: list[RtspWorker] = []
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        cameras = self._load_cameras()
        enabled_cameras = [camera for camera in cameras if camera.enabled]
        logger.info("Loaded cameras total=%s enabled=%s", len(cameras), len(enabled_cameras))

        for camera in enabled_cameras:
            worker = RtspWorker(camera=camera, detector=self.detector, event_client=self.event_client)
            thread = threading.Thread(target=worker.run_forever, daemon=True, name=f"camera-{camera.camera_code}")
            self.workers.append(worker)
            self.threads.append(thread)
            thread.start()
            logger.info("Started worker camera=%s", camera.camera_code)

    def stop(self) -> None:
        for worker in self.workers:
            worker.stop()

    def status(self) -> dict:
        return {
            "workers": [
                {
                    "camera_code": worker.state.camera_code,
                    "stream_opened": worker.state.stream_opened,
                    "object_present": worker.state.object_present,
                    "last_target_count": worker.state.last_target_count,
                    "sent_events": worker.state.sent_events,
                    "read_errors": worker.state.read_errors,
                }
                for worker in self.workers
            ]
        }

    def _load_cameras(self) -> list[CameraConfig]:
        with self.cameras_config_path.open("r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file) or {}

        cameras_raw = raw_data.get("cameras", [])
        return [CameraConfig.model_validate(item) for item in cameras_raw]
