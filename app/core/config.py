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

    frame_sample_every_n: int = Field(default=5)
    detection_confidence: float = Field(default=0.35)
    detection_iou: float = Field(default=0.45)
    trigger_cooldown_seconds: int = Field(default=30)
    pre_event_buffer_seconds: int = Field(default=5)
    post_event_record_seconds: int = Field(default=5)
    clip_fps: int = Field(default=10)
    max_stream_reconnect_delay_seconds: int = Field(default=10)

    yolo_model_path: str = Field(default="yolov8n.pt")
    yolo_device: str = Field(default="cpu")
    yolo_classes: str = Field(default="car,bus,truck,motorcycle")


@lru_cache
def get_settings() -> Settings:
    return Settings()