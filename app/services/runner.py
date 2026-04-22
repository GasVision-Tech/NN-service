from __future__ import annotations

import logging
import threading

from app.adapters.yolo_detector import YoloVehicleDetector
from app.clients.event_service import EventServiceClient
from app.clients.media_storage import LocalStubMediaStorage
from app.core.config import Settings
from app.services.cooldown import TriggerCooldown
from app.services.media_builder import MediaBuilder
from app.services.pipeline import StreamPipeline
from app.services.scenario_engine import ScenarioEngine
from app.utils.config_loader import load_streams_config

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        streams = load_streams_config(self._settings.streams_config_path)
        if not streams:
            raise RuntimeError("No enabled streams found in streams config")

        detector = YoloVehicleDetector(
            model_path=self._settings.yolo_model_path,
            device=self._settings.yolo_device,
            confidence=self._settings.detection_confidence,
            iou=self._settings.detection_iou,
            allowed_labels={item.strip() for item in self._settings.yolo_classes.split(",") if item.strip()},
        )
        scenario_engine = ScenarioEngine()
        event_client = EventServiceClient(
            base_url=self._settings.event_service_base_url,
            source=self._settings.event_source,
            default_status=self._settings.event_default_status,
            default_severity=self._settings.event_default_severity,
        )
        media_storage = LocalStubMediaStorage(
            base_dir=self._settings.media_base_dir,
            public_base_url=self._settings.media_public_base_url,
        )
        media_builder = MediaBuilder(base_dir=self._settings.media_base_dir)

        buffer_frame_count = max(
            self._settings.pre_event_buffer_seconds * self._settings.clip_fps,
            self._settings.clip_fps,
        )

        for stream in streams:
            cooldown = TriggerCooldown(self._settings.trigger_cooldown_seconds)
            pipeline = StreamPipeline(
                stream=stream,
                detector=detector,
                scenario_engine=scenario_engine,
                event_client=event_client,
                media_storage=media_storage,
                media_builder=media_builder,
                frame_sample_every_n=self._settings.frame_sample_every_n,
                buffer_frame_count=buffer_frame_count,
                post_event_record_seconds=self._settings.post_event_record_seconds,
                clip_fps=self._settings.clip_fps,
                reconnect_delay_seconds=self._settings.max_stream_reconnect_delay_seconds,
                cooldown=cooldown,
                media_bucket=self._settings.media_bucket,
            )
            thread = threading.Thread(target=pipeline.run_forever, daemon=True, name=f"pipeline-{stream.camera_code}")
            thread.start()
            self._threads.append(thread)
            logger.info("Started pipeline thread for station=%s camera=%s", stream.station_code, stream.camera_code)

    def join(self) -> None:
        for thread in self._threads:
            thread.join()