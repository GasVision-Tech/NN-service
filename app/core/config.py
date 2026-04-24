from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="nn-service")
    log_level: str = Field(default="INFO")

    streams_config_path: str = Field(default="/app/config/streams.yaml")

    event_service_base_url: str = Field(default="http://event-service:8000")
    event_source: str = Field(default="cv")
    event_default_severity: str = Field(default="med")
    event_default_status: str = Field(default="open")

    media_bucket: str = Field(default="gasvision-media")
    media_base_dir: str = Field(default="/tmp/nn-service-media")
    media_public_base_url: str = Field(default="http://localhost:9000/fake-s3")

    # --- Потоковая обработка ---
    frame_sample_every_n: int = Field(default=5)
    detection_confidence: float = Field(default=0.35)
    detection_iou: float = Field(default=0.45)
    trigger_cooldown_seconds: int = Field(default=30)
    pre_event_buffer_seconds: int = Field(default=5)
    post_event_record_seconds: int = Field(default=5)
    clip_fps: int = Field(default=10)
    max_stream_reconnect_delay_seconds: int = Field(default=10)

    # --- YOLO ---
    yolo_model_path: str = Field(default="/app/models/yolov8n.pt")
    yolo_device: str = Field(default="cpu")
    # Человек + машины. Оставляем строкой, чтобы легко переопределять из .env.
    yolo_classes: str = Field(default="person,car,bus,truck,motorcycle")
    # Подмножества, по которым ZoneScenarioEngine отличает людей от транспорта:
    yolo_person_labels: str = Field(default="person")
    yolo_vehicle_labels: str = Field(default="car,bus,truck,motorcycle")
    # Трекер ultralytics: bytetrack.yaml (дефолт) или botsort.yaml.
    yolo_tracker_config: str = Field(default="bytetrack.yaml")

    # --- Сценарии / таймеры (сек) ---
    person_without_car_sec: float = Field(default=30.0)
    person_too_long_at_station_sec: float = Field(default=120.0)
    car_too_long_sec: float = Field(default=300.0)
    event_cooldown_sec: float = Field(default=60.0)

    # --- ReID ---
    reid_grace_sec: float = Field(default=8.0)
    reid_radius_px: int = Field(default=150)
    person_car_proximity_px: int = Field(default=200)

    # --- Fallback-зоны (когда для камеры не задан zones_config_path) ---
    fallback_frame_width: int = Field(default=1920)
    fallback_frame_height: int = Field(default=1080)

    # --- Viewer (MJPEG web-viewer) ---
    viewer_enabled: bool = Field(default=True)
    viewer_host: str = Field(default="0.0.0.0")
    viewer_port: int = Field(default=8090)


@lru_cache
def get_settings() -> Settings:
    return Settings()
