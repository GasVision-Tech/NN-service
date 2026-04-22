from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from app.adapters.rtsp_reader import RTSPReader
from app.adapters.yolo_detector import Detector
from app.clients.event_service import EventServiceClient
from app.clients.media_storage import MediaStorage, build_object_key
from app.domain.models import ScenarioTrigger, StreamConfig
from app.services.cooldown import TriggerCooldown
from app.services.frame_buffer import FrameRingBuffer, TimedFrame
from app.services.media_builder import MediaBuilder
from app.services.scenario_engine import ScenarioEngine

logger = logging.getLogger(__name__)


class StreamPipeline:
    def __init__(
        self,
        *,
        stream: StreamConfig,
        detector: Detector,
        scenario_engine: ScenarioEngine,
        event_client: EventServiceClient,
        media_storage: MediaStorage,
        media_builder: MediaBuilder,
        frame_sample_every_n: int,
        buffer_frame_count: int,
        post_event_record_seconds: int,
        clip_fps: int,
        reconnect_delay_seconds: int,
        cooldown: TriggerCooldown,
        media_bucket: str,
    ) -> None:
        self._stream = stream
        self._detector = detector
        self._scenario_engine = scenario_engine
        self._event_client = event_client
        self._media_storage = media_storage
        self._media_builder = media_builder
        self._frame_sample_every_n = max(frame_sample_every_n, 1)
        self._post_event_record_seconds = post_event_record_seconds
        self._clip_fps = clip_fps
        self._cooldown = cooldown
        self._media_bucket = media_bucket
        self._reader = RTSPReader(stream.rtsp_url, reconnect_delay_seconds=reconnect_delay_seconds)
        self._buffer = FrameRingBuffer(maxlen=buffer_frame_count)
        self._frame_counter = 0

    def run_forever(self) -> None:
        logger.info(
            "Starting stream pipeline: station=%s camera=%s",
            self._stream.station_code,
            self._stream.camera_code,
        )
        for frame in self._reader.frames():
            self._buffer.append(frame)
            self._frame_counter += 1
            if self._frame_counter % self._frame_sample_every_n != 0:
                continue

            batch = self._detector.detect(frame)
            trigger = self._scenario_engine.evaluate(
                station_code=self._stream.station_code,
                camera_code=self._stream.camera_code,
                frame=frame,
                batch=batch,
            )
            if trigger is None:
                continue

            cooldown_key = f"{self._stream.station_code}:{self._stream.camera_code}:{trigger.scenario_key}"
            if not self._cooldown.allow(cooldown_key):
                logger.info("Trigger suppressed by cooldown: %s", cooldown_key)
                continue

            self._handle_trigger(trigger)

    def _handle_trigger(self, trigger: ScenarioTrigger) -> None:
        snapshot_name = self._build_filename(trigger, suffix="jpg")
        snapshot_path = self._media_builder.save_snapshot(snapshot_name, trigger.snapshot_frame)
        image_key = build_object_key(
            trigger.station_code,
            trigger.camera_code,
            trigger.scenario_key,
            suffix="jpg",
        )
        image_url = self._media_storage.upload_file(
            local_path=snapshot_path,
            bucket=self._media_bucket,
            object_key=image_key,
        )
        created = self._event_client.create_event(trigger, image_url)

        thread = threading.Thread(
            target=self._finalize_clip,
            args=(created.event_id, trigger),
            daemon=True,
        )
        thread.start()

    def _finalize_clip(self, event_id: int, trigger: ScenarioTrigger) -> None:
        pre_frames = self._buffer.snapshot()
        logger.info("Collecting post-event frames for event=%s", event_id)
        deadline = time.time() + self._post_event_record_seconds
        post_frames: list[TimedFrame] = []

        clip_reader = RTSPReader(self._stream.rtsp_url, reconnect_delay_seconds=3)
        try:
            for frame in clip_reader.frames():
                timed = TimedFrame(frame=frame.copy(), ts=datetime.now(timezone.utc))
                post_frames.append(timed)
                if time.time() >= deadline:
                    break
        finally:
            clip_reader.close()

        clip_frames = pre_frames + post_frames
        clip_name = self._build_filename(trigger, suffix="mp4")
        clip_path = self._media_builder.save_clip(clip_name, clip_frames, fps=self._clip_fps)
        clip_key = build_object_key(
            trigger.station_code,
            trigger.camera_code,
            trigger.scenario_key,
            suffix="mp4",
        )
        clip_url = self._media_storage.upload_file(
            local_path=clip_path,
            bucket=self._media_bucket,
            object_key=clip_key,
        )
        self._event_client.attach_clip(event_id=event_id, clip_url=clip_url)
        logger.info("Clip attached to event=%s", event_id)

    @staticmethod
    def _build_filename(trigger: ScenarioTrigger, suffix: str) -> str:
        ts = trigger.triggered_at.strftime("%Y%m%dT%H%M%S%f")
        return f"{trigger.station_code}_{trigger.camera_code}_{trigger.scenario_key}_{ts}.{suffix}"