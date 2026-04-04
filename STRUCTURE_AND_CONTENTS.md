# Repository structure and file contents

## .dockerignore

```
__pycache__/
*.pyc
*.pyo
*.pyd
*.swp
.env
.venv/
.git/
.gitignore
*.log
```

## .env

```
# HTTP
APP_HOST=0.0.0.0
APP_PORT=8081
LOG_LEVEL=INFO

# Event service
EVENT_SERVICE_BASE_URL=http://host.docker.internal:8000
EVENT_SERVICE_TIMEOUT_SECONDS=10
EVENT_SOURCE=cv
EVENT_TITLE=vehicle detected
EVENT_SEVERITY=low
EVENT_STATUS=open
EVENT_COOLDOWN_SECONDS=60

# YOLO
YOLO_MODEL=yolov8n.pt
YOLO_CONFIDENCE=0.35
YOLO_IOU=0.45
YOLO_DEVICE=cpu
YOLO_IMAGE_SIZE=640
YOLO_CLASSES=2

# RTSP / stream processing
CAMERAS_CONFIG_PATH=/app/config/cameras.yaml
RTSP_OPEN_TIMEOUT_SECONDS=20
RTSP_READ_TIMEOUT_SECONDS=20
FRAME_SKIP=5
RECONNECT_DELAY_SECONDS=5
MAX_EMPTY_FRAMES=50

# Optional auth for OpenVPN setups that use user/pass.
OPENVPN_USERNAME=
OPENVPN_PASSWORD=
```

## .env.example

```
# HTTP
APP_HOST=0.0.0.0
APP_PORT=8081
LOG_LEVEL=INFO

# Event service
EVENT_SERVICE_BASE_URL=http://host.docker.internal:8000
EVENT_SERVICE_TIMEOUT_SECONDS=10
EVENT_SOURCE=cv
EVENT_TITLE=vehicle detected
EVENT_SEVERITY=low
EVENT_STATUS=open
EVENT_COOLDOWN_SECONDS=60

# YOLO
YOLO_MODEL=yolov8n.pt
YOLO_CONFIDENCE=0.35
YOLO_IOU=0.45
YOLO_DEVICE=cpu
YOLO_IMAGE_SIZE=640
YOLO_CLASSES=2

# RTSP / stream processing
CAMERAS_CONFIG_PATH=/app/config/cameras.yaml
RTSP_OPEN_TIMEOUT_SECONDS=20
RTSP_READ_TIMEOUT_SECONDS=20
FRAME_SKIP=5
RECONNECT_DELAY_SECONDS=5
MAX_EMPTY_FRAMES=50

# Optional auth for OpenVPN setups that use user/pass.
OPENVPN_USERNAME=
OPENVPN_PASSWORD=
```

## Dockerfile

```
FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    iproute2 \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/scripts/wait_for_vpn.sh

CMD ["python", "run.py"]
```

## README.md

```
# GasVision NN Service

NN-сервис для обработки RTSP-потоков через YOLO и отправки событий в `event-service`.

## Что делает сервис

1. Поднимает OpenVPN-клиент в Docker.
2. Подключается к RTSP-камерам через VPN.
3. Читает кадры из RTSP.
4. Прогоняет кадры через YOLO.
5. По умолчанию детектирует только `car`.
6. При срабатывании отправляет HTTP-событие в `event-service`.

## Почему так устроено

По sequence-диаграмме `seq_diag_cv.drawio.xml` базовый пайплайн такой:

`Camera -> NN Service -> Event Service -> далее Web/BFF/Telegram`

Поэтому этот репозиторий делает только свою часть:

- читает RTSP;
- делает CV-детекцию;
- отправляет событие в event-service.

## Изученные ограничения из вашего event-service

`event-service` сейчас принимает событие через:

- `POST /v1/events`

Минимально нужные поля:

- `source`
- `title`
- `station_code`
- `camera_code`
- `severity`
- `status`
- `media`

В MVP ниже мы отправляем событие **без S3-медиа**, потому что в вашем event-service поле `media` может быть пустым списком.
Это самый простой и корректный старт. Позже можно расширить сервис сохранением annotated image / clip и последующим `POST /v1/events/{id}/media`.

## Структура

```text
nn-service-repo/
├── app/
│   ├── api/
│   │   └── routes.py
│   ├── clients/
│   │   └── event_client.py
│   ├── core/
│   │   ├── config.py
│   │   └── logging.py
│   ├── models/
│   │   └── camera.py
│   ├── services/
│   │   ├── detector.py
│   │   ├── pipeline.py
│   │   └── rtsp_worker.py
│   ├── utils/
│   │   └── time.py
│   └── main.py
├── config/
│   └── cameras.yaml
├── docker/
│   └── openvpn/
│       ├── client.ovpn
│       └── credentials.txt
├── scripts/
│   └── wait_for_vpn.sh
├── .dockerignore
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── run.py
```

## Запуск

### 1. Убедиться, что event-service уже поднят

Если ваш `event-service` запущен на хосте через его compose, то по умолчанию он доступен как:

- `http://host.docker.internal:8000`

### 2. Проверить камеры

Файл `config/cameras.yaml` уже заполнен вашими RTSP URL.

### 3. Проверить OpenVPN-конфиг

Файл `docker/openvpn/client.ovpn` уже добавлен в репозиторий на основе вашего `.ovpn`.
Если захотите не хранить его в git, оставьте его локально и добавьте в `.gitignore`.

### 4. Запуск

```bash
docker compose up --build
```

### 5. Проверка

```bash
curl http://localhost:8081/health
curl http://localhost:8081/status
```

## Как работает дедупликация

Чтобы сервис не заспамил `event-service` событиями на каждом кадре, для каждой камеры введена логика:

- если машина появилась после отсутствия — создаём событие;
- если машина всё ещё есть — повторное событие отправляем не чаще, чем раз в `EVENT_COOLDOWN_SECONDS`;
- если машина пропала — состояние камеры сбрасывается.

## Принятые решения

### 1. Детектируем только машины

Используется класс COCO `car`.
По умолчанию это `class id = 2`.

### 2. OpenVPN вынесен в отдельный контейнер

Так проще и надёжнее:

- контейнер `openvpn-client` поднимает VPN;
- контейнер `nn-service` использует network namespace этого контейнера через:
  `network_mode: "service:openvpn-client"`

То есть RTSP идёт через VPN.

### 3. Event-service оставлен внешним

Это сделано специально, чтобы не ломать ваш текущий репозиторий event-service.
NN-сервис просто ходит в него по HTTP.

## Что легко добавить следующим шагом

- сохранение annotated frame на диск или в S3;
- сбор видеоклипа по событию;
- отправку `POST /v1/events/{id}/media`;
- несколько сценариев вместо одной детекции машин;
- polygon ROI по каждой камере;
- трекинг объектов вместо простой кадровой детекции.
```

## app/__init__.py

```

```

## app/api/__init__.py

```

```

## app/api/routes.py

```
from fastapi import APIRouter

from app.services.runtime import pipeline_service

router = APIRouter()


@router.get('/health')
def health() -> dict:
    return {'status': 'ok'}


@router.get('/status')
def status() -> dict:
    return pipeline_service.status()
```

## app/clients/__init__.py

```

```

## app/clients/event_client.py

```
import logging
from typing import Any

import requests

from app.core.config import settings
from app.models.camera import CameraConfig
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


class EventClient:
    def __init__(self) -> None:
        self.base_url = settings.event_service_base_url.rstrip("/")
        self.timeout = settings.event_service_timeout_seconds

    def create_vehicle_detected_event(
        self,
        camera: CameraConfig,
        detections_count: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "source": settings.event_source,
            "title": settings.event_title,
            "station_code": camera.station_code,
            "camera_code": camera.camera_code,
            "severity": settings.event_severity,
            "status": settings.event_status,
            "created_at": utc_now_iso(),
            "media": [],
        }

        if extra:
            payload["title"] = f"{payload['title']} | cars={detections_count} | {extra}"

        url = f"{self.base_url}/v1/events"
        logger.info("Sending event to %s for camera=%s", url, camera.camera_code)

        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
```

## app/core/__init__.py

```

```

## app/core/config.py

```
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
```

## app/core/logging.py

```
import logging

from app.core.config import settings


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=LOG_FORMAT,
    )
```

## app/main.py

```
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.logging import setup_logging
from app.services.runtime import pipeline_service

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    pipeline_service.start()
    yield
    pipeline_service.stop()


app = FastAPI(title='gasvision-nn-service', lifespan=lifespan)
app.include_router(router)
```

## app/models/__init__.py

```

```

## app/models/camera.py

```
from pydantic import BaseModel, Field


class CameraConfig(BaseModel):
    station_code: str = Field(..., min_length=1)
    camera_code: str = Field(..., min_length=1)
    rtsp_url: str = Field(..., min_length=1)
    enabled: bool = True
```

## app/services/__init__.py

```

```

## app/services/detector.py

```
import logging
from dataclasses import dataclass

from ultralytics import YOLO

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    has_target: bool
    target_count: int
    max_confidence: float


class YoloDetector:
    def __init__(self) -> None:
        logger.info(
            "Loading YOLO model=%s device=%s classes=%s",
            settings.yolo_model,
            settings.yolo_device,
            settings.yolo_class_ids,
        )
        self.model = YOLO(settings.yolo_model)

    def detect(self, frame) -> DetectionResult:
        results = self.model.predict(
            source=frame,
            conf=settings.yolo_confidence,
            iou=settings.yolo_iou,
            imgsz=settings.yolo_image_size,
            device=settings.yolo_device,
            classes=settings.yolo_class_ids,
            verbose=False,
        )

        result = results[0]
        boxes = result.boxes

        if boxes is None or len(boxes) == 0:
            return DetectionResult(has_target=False, target_count=0, max_confidence=0.0)

        confidences = [float(conf) for conf in boxes.conf.tolist()]
        return DetectionResult(
            has_target=True,
            target_count=len(confidences),
            max_confidence=max(confidences),
        )
```

## app/services/pipeline.py

```
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
```

## app/services/rtsp_worker.py

```
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
```

## app/services/runtime.py

```
from app.core.config import settings
from app.services.pipeline import PipelineService

pipeline_service = PipelineService(cameras_config_path=settings.cameras_config_path)
```

## app/utils/__init__.py

```

```

## app/utils/time.py

```
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

## config/cameras.yaml

```
cameras:
  - station_code: "station_001"
    camera_code: "cam_01"
    rtsp_url: "rtsp://admin:12345@172.20.21.174/cam/realmonitor?channel=1&subtype=1&unicast=true&proto=Onvif"
    enabled: true

  - station_code: "station_001"
    camera_code: "cam_02"
    rtsp_url: "rtsp://admin:123qweASD@172.20.21.172/Streaming/Channels/101?transportmode=unicast&profile=Profile_1"
    enabled: true
```

## docker/openvpn/client.ovpn

```
dev tun
persist-tun
persist-key
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305:AES-256-CBC
data-ciphers-fallback AES-256-CBC
auth SHA256
tls-client
client
resolv-retry infinite
remote vpn.tn-azs.ru 1194 udp
nobind
verify-x509-name "AZSVPNCL" name
remote-cert-tls server
explicit-exit-notify

<ca>
-----BEGIN CERTIFICATE-----
MIIDwDCCAqigAwIBAgIIdmaUlni8YZIwDQYJKoZIhvcNAQELBQAwRTEZMBcGA1UE
AxMQaW50ZXJuYWwtY2EtYXpzYzELMAkGA1UEBhMCUlUxDTALBgNVBAoTBEFaU0Mx
DDAKBgNVBAsTA09JVDAeFw0yNDA2MTcwODQ1MjlaFw0zNDA2MTUwODQ1MjlaMEUx
GTAXBgNVBAMTEGludGVybmFsLWNhLWF6c2MxCzAJBgNVBAYTAlJVMQ0wCwYDVQQK
EwRBWlNDMQwwCgYDVQQLEwNPSVQwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK
AoIBAQDJivDWFQnUSzk/VV4dL1ciUwkUTUrVimKlY/x2SrQF1n7RvRE813pE/Ay5
ieO0oJefB99plntBHjdncNtsLJeXTcbDJ1Dtnb/l6NmPL9jxI0g5zRyHcgNeLlwM
3rB3zzm1QEtUn0mEbRW585UUkkLNRwzVuEjL+WaSBDoqFd7s8+Wnli2IDaiBbvIR
WIGedQdQUgk9rwZGH67DanO2WTUTNEy2+kca9XOZIR3oEAPyJGP30LlyvFYjfio9
a82uGgtLAnhOupnU3lWedff4jvevQYNTom9f56yUFWctzgGSv1Fd1jY+iAcjBGiu
cuUBa8+CrlCPfkscse4RAid6jd8/AgMBAAGjgbMwgbAwHQYDVR0OBBYEFKQv4N5/
I0ek5B5fZqioqappNi3iMHQGA1UdIwRtMGuAFKQv4N5/I0ek5B5fZqioqappNi3i
oUmkRzBFMRkwFwYDVQQDExBpbnRlcm5hbC1jYS1henNjMQswCQYDVQQGEwJSVTEN
MAsGA1UEChMEQVpTQzEMMAoGA1UECxMDT0lUggh2ZpSWeLxhkjAMBgNVHRMEBTAD
AQH/MAsGA1UdDwQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAQEAqFyRPJzlxKdvmMsq
qqp9Qz/8TbccL+zG1ezE6ZJ6VGYRrnzAjxbPtoYl/K4emMTyRNhfCYZ2AoBxAc6P
lcM7jRfHmYuEhYV5b9LTSEM2uSiKcV5J+Zx41v20HGT7tp4MhahE8QvvRNhXAtbj
PRtNSd4xiudkRCi+qcdhvITqqXhNkbcifPgc+cGFgjI5DAEEupdp/MDZMD7tkPaf
izdbUWDhZzzILZv3CCOGxUFxGOgJyA/uY+J4/i1QK8WPPNFxQM4c+0FWaBH0TU7+
VNZdlIm/dkui6T0U21EJ1U8wnOqLSJwjIhp4YCjLWAtkSA12EoBpKqYgbs6olFyK
Fwtjag==
-----END CERTIFICATE-----
</ca>
<cert>
-----BEGIN CERTIFICATE-----
MIIEDzCCAvegAwIBAgIBHTANBgkqhkiG9w0BAQsFADBFMRkwFwYDVQQDExBpbnRl
cm5hbC1jYS1henNjMQswCQYDVQQGEwJSVTENMAsGA1UEChMEQVpTQzEMMAoGA1UE
CxMDT0lUMB4XDTI2MDIwNjE0MDgyN1oXDTM2MDIwNDE0MDgyN1owPjESMBAGA1UE
AxMJZ2FzdmlzaW9uMQswCQYDVQQGEwJSVTENMAsGA1UEChMEQVpTQzEMMAoGA1UE
CxMDT0lUMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyRFqy8Bo6TVg
yVo23n6eKViRiPRm1PS+6zdSpVZz6KK4sCcGsmosCMNhZ/xDAbuGX5DkoWLzqdfU
iNxMA+ryVSpQGXo0Q130qgpKB5T1wIwXc3emk18zxJRs/1caQf1sL2cDZRJPwmgW
0EpR2FlCrm6/zXvHRSUgDH2mF2UbphOBWIObmZlfisCioYlEpkbR+axSt77ATLVL
FlEJExwcBYs0GWmBMyE6vERJklTVYlN+B8SskKoaBt1crqNstxg8q4RgDnalwvFU
kxU9w8qoa/762aQ8g7kmEU/QXremk9MVD3tDrDcupjVvX4qPKGoaPROM8nBAeI7n
0AtuYg1TEwIDAQABo4IBDzCCAQswCQYDVR0TBAIwADALBgNVHQ8EBAMCBeAwMQYJ
YIZIAYb4QgENBCQWIk9wZW5TU0wgR2VuZXJhdGVkIFVzZXIgQ2VydGlmaWNhdGUw
HQYDVR0OBBYEFKb9rgHsVk4pzILGtzD0i/NRHixXMHQGA1UdIwRtMGuAFKQv4N5/
I0ek5B5fZqioqappNi3ioUmkRzBFMRkwFwYDVQQDExBpbnRlcm5hbC1jYS1henNj
MQswCQYDVQQGEwJSVTENMAsGA1UEChMEQVpTQzEMMAoGA1UECxMDT0lUggh2ZpSW
eLxhkjATBgNVHSUEDDAKBggrBgEFBQcDAjAUBgNVHREEDTALgglnYXN2aXNpb24w
DQYJKoZIhvcNAQELBQADggEBAMAzLHylxSIQaEVOji97KHJqe+PrtzPKsbHopXYP
UsBZTw9UKgUza/OwUMc85v+nSLJrFNxj++nWzhW/LtLZT8ds+BowxT7sR4ab2H4B
vjTmKW0EsgkrURz61fJYlJl6/iSLWbx+NRiOZcQ9RF33h+Uh09VYyKWmkIhssk0r
ZXNWYrHqmQnN/9F8aIEEs/OVgWC6dixkhj3j4aPzEZklie27NeGM5f4cG6XwrKux
fojXY+qdq5qIJmiNAbWXE9xQlCHfj4P7WIa/44X5uzRkZ8Sv8IrxWlXfOaUSunqL
dOlSVXNPMuEZ+Nn3OAAgZKVcTc6kjnhr5hfUUFGz46Y5XG4=
-----END CERTIFICATE-----
</cert>
<key>
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDJEWrLwGjpNWDJ
Wjbefp4pWJGI9GbU9L7rN1KlVnPooriwJwayaiwIw2Fn/EMBu4ZfkOShYvOp19SI
3EwD6vJVKlAZejRDXfSqCkoHlPXAjBdzd6aTXzPElGz/VxpB/WwvZwNlEk/CaBbQ
SlHYWUKubr/Ne8dFJSAMfaYXZRumE4FYg5uZmV+KwKKhiUSmRtH5rFK3vsBMtUsW
UQkTHBwFizQZaYEzITq8REmSVNViU34HxKyQqhoG3Vyuo2y3GDyrhGAOdqXC8VST
FT3Dyqhr/vrZpDyDuSYRT9Bet6aT0xUPe0OsNy6mNW9fio8oaho9E4zycEB4jufQ
C25iDVMTAgMBAAECggEAH+MHO3De3RJ+BZtXqvQMQ8+r+jroOUrnIYkQg5qw9Gp5
56sIwNqy87YoiG5/zJKKE4si9wUo/KfMfjfm8OgdrlabhVmAAv8E121HoumDX8SC
eOliHWpdB1vn5/FO/2V1TBSGJqKUy5E+V6IHAMyHFVVdovTHprY7MuqU9eOEUv3p
932koCRmtfBHHo3YAXkFCzeg3d99HmZXjtOyXN+3nD0HUamS5LJ+cEsr4QX+qV0c
nSA9Ll3aqfuvqsJARgYqHZzl9dTHsVQ+K2JQ9iKYX1nJ8yvNPKwcCyK9KUATBRD+
kGMhmehFZOvZElUrE7kju83NSsZ3AkZnPi2H/MeBIQKBgQD40rwZB2HsUY5pwxYo
+LSFTdD3g6ur1tNKqowvhGuSfhuMmvy1at84EDXLpZ1Du+3X5O6It0+4nAJGuL0x
V9LfBvNWFl7pYq5vQ8go9rN73sE6B2Cf0d2irLy4XsXZmr7SIc1BPQBPLhcv7gmv
sXcT8GnXYbkrmNFCfGwt/9bPzwKBgQDO3hEruhgEIOKvyMAfzM+3iCyjFucICF6W
UF1WNbx6yMZuvjbS2lorlvcrjaCe1dNsJgBI3Ox3n0/DmW+ZZAni4vT8gKVo36fD
uNeIZG90wVcj+q6Iv41VK6hE7WQ2yxFY/yzkp4Y8QAbqAGhWn+4lBBP9ii+IhhTV
6vhDtZw1fQKBgBGzO/oz0j1zV8QiQpNLjdvluzXAQhgJQiKPm0VOEDDtk5we8lcd
cR+V153S/CrzVWoEOQu0rpEdV/Qb9Clsa+29mtXG5Z2IjYwQsE5prj7ji69LEw6L
aU7HJu2eoEhof6aHDQjVBA6d4rxgHIRJBYV/lqfhpcB6MHigTTnAd1F1AoGAPW9X
vBu1HCzEBZ5h6E/D9GZ9kyWvEQSjSpKtVXf59KEBxUu6Ll5oXv+jggy6gdFQy5Jx
jTIHC/OFFbrQmMZL5VSvmvl9piqdwRN49umU7CCrB2VgRf5VM9EWVPoHQ/qEuWB2
7aUOyX/eTco3Mlqmt9mBMk/ClBH1yB3TAxDBRmUCgYEAjc9e1UE5PaHaskDtZ0Sn
iwR8L9oH2KlMIvT38LZtam2bExoeF41HKoqdMQtAe/wBdOUbLaVYNzkWoh65tvKc
+yFIlQGuoDIcTlyGZz1xgZTBss0Y1IlX1d6pXwEMkQIZ8Vx1tSud9mVFL2mxhMZG
VQMHOHZr2ioLMIJdyITAp28=
-----END PRIVATE KEY-----
</key>
<tls-crypt>
#
# 2048 bit OpenVPN static key
#
-----BEGIN OpenVPN Static key V1-----
e89e1b9767bc4ec597ae6f6385b4716f
4bb08e94aa7f0d78983ae672f34062c1
9c7fb17c68e7a11709705acc17319459
5b1427e95532f84efc46cb5db2537562
7e49df3fa01a21af66b533c231037cbd
3a7241438a92edbff05152280dbeefd7
2507179b085367d6c0da9d59298bb773
7bf35a60de7646df5bad96046da294fb
785f61043a714998b252f46208ea0bfc
b3e0833dd1490a8b7fb704cac1866516
8a69270802e39b228f1646015e4c4228
bdd878068a4e3731c3cc3d8eb176f60d
a715b22c08ba57de9225e0d1edee0fd0
240ea009d78234484b29deab0fcb661a
1f5eaa2e297409cede40d840a34c7cf3
7a237858d2dd72280409d4592259aa2b
-----END OpenVPN Static key V1-----
</tls-crypt>
```

## docker/openvpn/credentials.txt

```
# Only if your VPN server requires username/password.
# Keep this file empty if cert-only auth is used.
# Example:
# my_user
# my_password
```

## docker-compose.yml

```
services:
  openvpn-client:
    image: dperson/openvpn-client:latest
    container_name: gasvision_openvpn_client
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    security_opt:
      - label:disable
    volumes:
      - ./docker/openvpn/client.ovpn:/vpn/client.ovpn:ro
      - ./docker/openvpn/credentials.txt:/vpn/credentials.txt:ro
    command: >
      -f ""
      -r 172.20.0.0/16
      -r 172.21.0.0/16
      -r 172.22.0.0/16
      -r 192.168.0.0/16
      -v /vpn/client.ovpn
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"

  nn-service:
    build: .
    container_name: gasvision_nn_service
    env_file:
      - .env
    depends_on:
      - openvpn-client
    network_mode: "service:openvpn-client"
    restart: unless-stopped
    volumes:
      - ./config:/app/config:ro
      - ./scripts:/app/scripts:ro
      - ./artifacts:/app/artifacts
    command: ["bash", "/app/scripts/wait_for_vpn.sh"]
```

## requirements.txt

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
pydantic==2.9.2
pydantic-settings==2.5.2
requests==2.32.3
PyYAML==6.0.2
opencv-python-headless==4.10.0.84
numpy==2.1.1
ultralytics==8.3.10
```

## run.py

```
import uvicorn

from app.core.config import settings


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )
```

## scripts/wait_for_vpn.sh

```
#!/usr/bin/env bash
set -e

TARGET_IP="172.20.21.174"
MAX_ATTEMPTS=30
SLEEP_SECONDS=2

attempt=1
while [ $attempt -le $MAX_ATTEMPTS ]; do
  echo "[wait_for_vpn] checking route to ${TARGET_IP}, attempt ${attempt}/${MAX_ATTEMPTS}"
  if ip route get "$TARGET_IP" >/dev/null 2>&1; then
    echo "[wait_for_vpn] route is available, starting app"
    exec python /app/run.py
  fi

  sleep "$SLEEP_SECONDS"
  attempt=$((attempt + 1))
done

echo "[wait_for_vpn] VPN route did not appear in time" >&2
exit 1
```

