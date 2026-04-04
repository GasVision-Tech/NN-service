from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 8081
    log_level: str = "INFO"

    event_service_base_url: str = "http://host.docker.internal:8000"
    event_service_timeout_seconds: int = 10
    event_source: str = "cv"
    event_title: str = "vehicle detected"
    event_severity: str = "low"
    event_status: str = "open"
    event_cooldown_seconds: int = 60

    yolo_model: str = "yolov8n.pt"
    yolo_confidence: float = 0.35
    yolo_iou: float = 0.45
    yolo_device: str = "cpu"
    yolo_image_size: int = 640
    yolo_classes: str = Field(default="2")

    cameras_config_path: str = "/app/config/cameras.yaml"
    rtsp_open_timeout_seconds: int = 20
    rtsp_read_timeout_seconds: int = 20
    frame_skip: int = 5
    reconnect_delay_seconds: int = 5
    max_empty_frames: int = 50

    @property
    def yolo_class_ids(self) -> list[int]:
        return [int(item.strip()) for item in self.yolo_classes.split(",") if item.strip()]


settings = Settings()
