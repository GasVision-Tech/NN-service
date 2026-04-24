from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.adapters.yolo_detector import YoloTrackingDetector
from app.adapters.zone_manager import load_zones_or_fallback
from app.api.frame_store import FrameStore
from app.clients.event_service import EventServiceClient
from app.clients.media_storage import LocalStubMediaStorage
from app.core.config import Settings
from app.domain.models import StreamConfig
from app.services.cooldown import TriggerCooldown
from app.services.event_tracker import EventTracker
from app.services.media_builder import MediaBuilder
from app.services.pipeline import StreamPipeline
from app.services.zone_scenario_engine import ZoneScenarioEngine
from app.utils.config_loader import load_streams_config

logger = logging.getLogger(__name__)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class PipelineRunner:
    """
    Запускает по одному StreamPipeline (= отдельный поток) на каждую камеру.

    Ключевой инвариант: у каждой камеры СВОЙ YoloTrackingDetector (и, значит,
    свой tracker-state) и свой EventTracker (таймеры/ReID/cooldown).

    Одни на всех: EventServiceClient, MediaStorage, MediaBuilder — они
    stateless-клиенты (httpx сам потокобезопасен через requests-level API).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        streams = load_streams_config(self._settings.streams_config_path)
        if not streams:
            raise RuntimeError("No enabled streams found in streams config")

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

        yolo_classes = _split_csv(self._settings.yolo_classes)
        person_labels = _split_csv(self._settings.yolo_person_labels)
        vehicle_labels = _split_csv(self._settings.yolo_vehicle_labels)

        frame_store = FrameStore()
        if self._settings.viewer_enabled:
            self._start_viewer(frame_store)

        for stream in streams:
            self._launch_pipeline(
                stream=stream,
                yolo_classes=yolo_classes,
                person_labels=person_labels,
                vehicle_labels=vehicle_labels,
                event_client=event_client,
                media_storage=media_storage,
                media_builder=media_builder,
                buffer_frame_count=buffer_frame_count,
                frame_store=frame_store,
            )

    def join(self) -> None:
        for thread in self._threads:
            thread.join()

    # ------------------------------------------------------------------

    def _start_viewer(self, store: FrameStore) -> None:
        import uvicorn
        from app.api.viewer import create_app

        app = create_app(store)
        config = uvicorn.Config(
            app,
            host=self._settings.viewer_host,
            port=self._settings.viewer_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # main thread owns signals

        thread = threading.Thread(target=server.run, daemon=True, name="viewer-server")
        thread.start()
        logger.info("Camera viewer started at http://%s:%s", self._settings.viewer_host, self._settings.viewer_port)

    def _launch_pipeline(
        self,
        *,
        stream: StreamConfig,
        yolo_classes: list[str],
        person_labels: list[str],
        vehicle_labels: list[str],
        event_client: EventServiceClient,
        media_storage: LocalStubMediaStorage,
        media_builder: MediaBuilder,
        buffer_frame_count: int,
        frame_store: FrameStore,
    ) -> None:
        settings = self._settings

        # 1) Per-camera YOLO (tracker state живёт внутри — шэрить нельзя)
        detector = YoloTrackingDetector(
            model_path=settings.yolo_model_path,
            device=settings.yolo_device,
            confidence=settings.detection_confidence,
            iou=settings.detection_iou,
            class_labels=yolo_classes,
            tracker_config=settings.yolo_tracker_config,
        )

        # 2) Per-camera zones (или full-frame fallback)
        zones = load_zones_or_fallback(
            stream.zones_config_path,
            fallback_width=settings.fallback_frame_width,
            fallback_height=settings.fallback_frame_height,
        )

        # 3) Per-camera EventTracker (таймеры привязаны к камере)
        event_tracker = EventTracker(
            station_code=stream.station_code,
            camera_code=stream.camera_code,
            person_without_car_sec=settings.person_without_car_sec,
            person_too_long_at_station_sec=settings.person_too_long_at_station_sec,
            car_too_long_sec=settings.car_too_long_sec,
            event_cooldown_sec=settings.event_cooldown_sec,
            reid_grace_sec=settings.reid_grace_sec,
            reid_radius_px=settings.reid_radius_px,
        )

        # 4) Per-camera ZoneScenarioEngine
        scenario_engine = ZoneScenarioEngine(
            zones=zones,
            event_tracker=event_tracker,
            person_labels=person_labels,
            vehicle_labels=vehicle_labels,
            person_car_proximity_px=settings.person_car_proximity_px,
        )

        detection_save_dir = Path(settings.media_base_dir) / "detections" / stream.camera_code

        cooldown = TriggerCooldown(settings.trigger_cooldown_seconds)
        pipeline = StreamPipeline(
            stream=stream,
            detector=detector,
            scenario_engine=scenario_engine,
            event_client=event_client,
            media_storage=media_storage,
            media_builder=media_builder,
            frame_sample_every_n=settings.frame_sample_every_n,
            buffer_frame_count=buffer_frame_count,
            post_event_record_seconds=settings.post_event_record_seconds,
            clip_fps=settings.clip_fps,
            reconnect_delay_seconds=settings.max_stream_reconnect_delay_seconds,
            cooldown=cooldown,
            media_bucket=settings.media_bucket,
            frame_store=frame_store,
            detection_save_dir=detection_save_dir,
        )

        thread = threading.Thread(
            target=pipeline.run_forever,
            daemon=True,
            name=f"pipeline-{stream.camera_code}",
        )
        thread.start()
        self._threads.append(thread)
        logger.info(
            "Started pipeline thread for station=%s camera=%s zones=%s",
            stream.station_code, stream.camera_code, len(zones),
        )
