from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from app.adapters.rtsp_reader import RTSPReader
from app.adapters.yolo_detector import Detector
from app.api.frame_store import FrameStore
from app.clients.event_service import EventServiceClient
from app.clients.media_storage import MediaStorage, build_object_key
from app.domain.models import ScenarioTrigger, StreamConfig
from app.services.cooldown import TriggerCooldown
from app.services.frame_buffer import FrameRingBuffer, TimedFrame
from app.services.media_builder import MediaBuilder
from app.services.zone_scenario_engine import ZoneScenarioEngine
from app.utils.draw import draw_detections

logger = logging.getLogger(__name__)


class StreamPipeline:
    """
    Один экземпляр — одна камера. Владеет:
      - RTSPReader (выделенный поток на камеру)
      - Detector (обычно YoloTrackingDetector со своим tracker-state)
      - ZoneScenarioEngine (обычно с собственным EventTracker)
      - FrameRingBuffer для pre-event клипа
      - TriggerCooldown как дополнительный rate-limiter на уровне камеры
        (основной anti-dup уже в EventTracker per-(event_type, track_id))
    """

    def __init__(
        self,
        *,
        stream: StreamConfig,
        detector: Detector,
        scenario_engine: ZoneScenarioEngine,
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
        frame_store: FrameStore | None = None,
        detection_save_dir: Path | None = None,
        enabled_checks: frozenset[str] | None = None,
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
        self._frame_store = frame_store
        self._detection_save_dir = detection_save_dir
        # Фильтр сценариев: None — пропускаем все триггеры, frozenset —
        # только те, чей scenario_key есть в множестве. EventTracker всё ещё
        # ведёт state по всем сценариям, отрезаем именно отправку события.
        self._enabled_checks = enabled_checks
        if detection_save_dir is not None:
            detection_save_dir.mkdir(parents=True, exist_ok=True)
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
            self._publish_detections(frame, batch)

            triggers = self._scenario_engine.evaluate(frame=frame, batch=batch)
            if not triggers:
                continue

            for trigger in triggers:
                # Per-camera проверка из config/checks.yaml. Если для камеры
                # сценарий выключен — тихо отбрасываем триггер. EventTracker
                # внутреннее состояние не сбрасывает, чтобы при повторном
                # включении проверки не было всплеска повторных событий.
                if self._enabled_checks is not None and trigger.scenario_key not in self._enabled_checks:
                    continue
                # Extra-cautious camera-level cooldown: по ключу
                # (scenario_key, track_id). Основной антидабл уже в EventTracker.
                cooldown_key = (
                    f"{self._stream.station_code}:{self._stream.camera_code}:"
                    f"{trigger.scenario_key}:{trigger.track_id}"
                )
                if not self._cooldown.allow(cooldown_key):
                    logger.info("Trigger suppressed by pipeline cooldown: %s", cooldown_key)
                    continue
                # Изоляция падений HTTP/IO: один упавший триггер не должен
                # убивать поток pipeline — иначе камера замолкает до рестарта.
                try:
                    self._handle_trigger(trigger)
                except Exception:
                    logger.exception(
                        "Trigger handling failed for camera=%s scenario=%s track=%s — camera continues",
                        self._stream.camera_code, trigger.scenario_key, trigger.track_id,
                    )

    # ------------------------------------------------------------------
    # Detection preview (viewer + disk)
    # ------------------------------------------------------------------

    def _publish_detections(self, frame, batch) -> None:
        if self._frame_store is None and self._detection_save_dir is None:
            return
        annotated = draw_detections(frame, batch)
        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        jpeg = buf.tobytes()
        if self._frame_store is not None:
            self._frame_store.update(self._stream.camera_code, jpeg)
        if self._detection_save_dir is not None:
            dst = self._detection_save_dir / "latest.jpg"
            dst.write_bytes(jpeg)

    # ------------------------------------------------------------------
    # Event creation + clip attachment
    # ------------------------------------------------------------------

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
            name=f"clip-{trigger.camera_code}-{created.event_id}",
        )
        thread.start()

    def _finalize_clip(self, event_id: int, trigger: ScenarioTrigger) -> None:
        try:
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
        except Exception:
            # Не роняем daemon-поток молча: логируем, чтоб было видно
            # почему у события нет клипа.
            logger.exception("Failed to finalize clip for event=%s camera=%s", event_id, trigger.camera_code)

    @staticmethod
    def _build_filename(trigger: ScenarioTrigger, suffix: str) -> str:
        ts = trigger.triggered_at.strftime("%Y%m%dT%H%M%S%f")
        tid = trigger.track_id if trigger.track_id is not None else "x"
        return (
            f"{trigger.station_code}_{trigger.camera_code}_"
            f"{trigger.scenario_key}_t{tid}_{ts}.{suffix}"
        )
