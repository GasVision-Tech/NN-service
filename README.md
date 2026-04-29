# nn-service

`nn-service` — CV-сервис платформы **GasVision**. Читает RTSP с камер АЗС
через VPN, прогоняет кадры через YOLO + ByteTrack, проверяет сценарии по
зонам и шлёт события с snapshot/клипом в `event-service`.

Проект построен как production-ready каркас: инфраструктурная часть (VPN,
RTSP reconnect, snapshot/clip, upload, HTTP-клиент event-service)
отделена от CV-логики. CV-инженер меняет только детектор и сценарии и не
трогает остальное.

---

## Что делает сервис

На каждую камеру поднимается отдельный pipeline-поток:

1. `RTSPReader` держит RTSP-соединение, переподключается после обрывов.
2. Каждый кадр попадает в `FrameRingBuffer` (pre-event буфер, 5 сек).
3. Каждый N-й кадр (дефолт `N=5`) идёт в `YoloTrackingDetector` — YOLO + ByteTrack
   со своим tracker-state на камеру.
4. `ZoneScenarioEngine` прогоняет 4 сценария поверх детекций и зон.
5. При срабатывании сценария:
   - сохраняется аннотированный snapshot;
   - snapshot загружается в storage client (сейчас — stub в локальную папку);
   - `POST /v1/events` в event-service создаёт событие;
   - отдельный поток собирает post-event клип (ещё 5 сек после триггера),
     сшивает с pre-event буфером и прикрепляет к событию через
     `POST /v1/events/{id}/media`.

Параллельно каждый обработанный кадр (с bbox и track-id) пишется в
`storage/detections/<CAM>/latest.jpg` — это source для локального viewer-а
на ноуте.

---

## Сценарии

Реализованы 4 сценария, все с per-(event_type, track_id) кулдауном:

| Ключ                              | Условие                                                                | Таймер                   | Severity |
|-----------------------------------|------------------------------------------------------------------------|--------------------------|----------|
| `person_in_forbidden_zone`        | Человек в запретной зоне                                               | 0 сек (мгновенно)        | high     |
| `person_without_car_at_column`    | Человек у колонки дольше порога, при этом рядом нет машины             | `PERSON_WITHOUT_CAR_SEC` | med      |
| `person_too_long_at_station`      | Человек на территории АЗС дольше порога                                | `PERSON_TOO_LONG_*_SEC`  | med      |
| `car_too_long_at_station`          | Машина дольше порога на территории АЗС                                 | `CAR_TOO_LONG_SEC`       | low      |

Для `person_in_forbidden_zone` и `person_without_car_at_column` нужна зонная
разметка. Для `person_too_long_at_station` и `car_too_long_at_station` при
отсутствии JSON зон включается full-frame fallback (вся картинка как
station/column).

ReID (`REID_GRACE_SEC`, `REID_RADIUS_PX`) склеивает одного и того же трека,
когда YOLO на пару кадров теряет его и присваивает новый track_id.

---

## Структура проекта

```
nn-service/
├── .env                      # шаблон env'ов (реальные креды — в .env.local)
├── .gitignore                # игнорит venv/storage/__pycache__/заметки
├── .dockerignore
├── Dockerfile
├── docker-compose.yml        # vpn + nn-service (network_mode: service:vpn)
├── requirements.txt
├── README.md                 # этот файл
├── ARCHITECTURE.md           # поток данных + потоки + storage
├── RUNBOOK.md                # пошаговый запуск и диагностика
│
├── app/
│   ├── main.py               # entrypoint: config → logging → runner
│   ├── core/
│   │   ├── config.py         # Pydantic Settings из .env
│   │   └── logging.py
│   ├── domain/
│   │   └── models.py         # StreamConfig, Detection, ScenarioTrigger, …
│   ├── adapters/
│   │   ├── rtsp_reader.py    # VideoCapture + reconnect loop
│   │   ├── yolo_detector.py  # YoloVehicleDetector + YoloTrackingDetector
│   │   └── zone_manager.py   # Zone, load_zones, point_in_zone, draw_zones
│   ├── services/
│   │   ├── runner.py                # поднимает pipeline на каждую камеру
│   │   ├── pipeline.py              # orchestration одной камеры
│   │   ├── zone_scenario_engine.py  # evaluate() → [ScenarioTrigger]
│   │   ├── event_tracker.py         # ReID + стейт 4 сценариев
│   │   ├── frame_buffer.py          # ring-buffer pre-event кадров
│   │   ├── media_builder.py         # snapshot + clip на диск
│   │   ├── cooldown.py              # camera-level кулдаун
│   │   └── scenario_engine.py       # compat-shim → ZoneScenarioEngine
│   ├── clients/
│   │   ├── event_service.py  # httpx POST /v1/events /v1/events/{id}/media
│   │   └── media_storage.py  # LocalStubMediaStorage (замена на S3 позже)
│   ├── api/
│   │   ├── frame_store.py    # thread-safe {camera_code: jpeg}
│   │   └── viewer.py         # FastAPI MJPEG (опционально, off by default)
│   └── utils/
│       ├── config_loader.py  # streams.yaml → [StreamConfig]
│       └── draw.py           # bbox + label на кадр
│
├── config/
│   ├── streams.yaml          # список камер (station_code/camera_code/rtsp)
│   └── zones/
│       ├── _template.json    # шаблон, коммитится
│       ├── README.md         # как размечать зоны
│       └── CAM-XXX.json      # per-camera зоны (в .gitignore)
│
├── scripts/
│   ├── draw_zones.py         # интерактивная разметка (cv2.imshow на ноуте)
│   └── view_cameras.py       # локальный просмотр storage/detections/<CAM>/latest.jpg
│
├── models/
│   └── yolov8n.pt            # веса (nano, ~6 MB — коммитить дальше на усмотрение)
│
├── docker/
│   └── openvpn/
│       └── README.md
│
└── storage/                  # runtime-output, в .gitignore
    ├── detections/<CAM>/latest.jpg
    ├── snapshots/
    ├── clips/
    └── gasvision-media/<station>/<camera>/…
```

---

## Основные компоненты

### `app/adapters/yolo_detector.py` — где CV-инженер живёт, #1

Два детектора:
- `YoloVehicleDetector` — stateless, для простой детекции без трекинга.
- `YoloTrackingDetector` — YOLO + ByteTrack со стабильным `track_id`. **Важно:**
  tracker-state хранится внутри инстанса YOLO, поэтому на каждую камеру
  создаётся свой `YoloTrackingDetector` (это делает `runner.py`).

Контракт выхода — `DetectionBatch(detections=[Detection(label, bbox, confidence, track_id)])`.

Менять модель/постпроцессинг — здесь.

### `app/services/zone_scenario_engine.py` + `event_tracker.py` — CV-логика, #2

`ZoneScenarioEngine.evaluate(frame, batch)` прогоняет 4 сценария и
возвращает `list[ScenarioTrigger]`. Состояние (когда трек впервые попал
в зону, когда последний раз видели, когда последний раз алертили) лежит
в `EventTracker`. ReID — там же.

Менять правила/сценарии — здесь.

### `app/adapters/zone_manager.py`

`load_zones_or_fallback()` читает JSON с полигонами. Если файл отсутствует —
подставляет station/column на весь кадр (из `FALLBACK_FRAME_WIDTH/HEIGHT`),
чтобы таймерные сценарии работали до разметки.

### `app/services/pipeline.py` — orchestration

- Гоняет цикл по кадрам из `RTSPReader`.
- Публикует аннотированный кадр в `FrameStore` и на диск для локального viewer-а.
- При триггере проверяет camera-level `TriggerCooldown` (дополнительный слой
  поверх EventTracker), делает snapshot+event+клип.
- Один сломанный триггер не кладёт pipeline-поток (всё в `try/except`).

### `app/clients/event_service.py`

Синхронный httpx. Два эндпоинта:
- `POST /v1/events` — создание события, возвращает `event_id`;
- `POST /v1/events/{id}/media` — прикрепление URL клипа.

### `app/clients/media_storage.py`

`LocalStubMediaStorage` копирует файл в папку, отдаёт fake-URL.
Когда появится реальный S3/minio — заменить только этот класс, интерфейс
`MediaStorage.upload_file(...)` уже правильный.

---

## Запуск локально

Подробный runbook — в `RUNBOOK.md`. TL;DR:

```bash
# 1) event-service (Postgres + FastAPI на :8000) — из его репо:
cd ~/job/Event-service && docker compose up -d

# 2) положить VPN-конфиг
cp ~/Downloads/pfSenseAZSC-udp-1194-gasvision-config.ovpn \
   ~/job/NN-service/docker/openvpn/

# 3) nn-service
cd ~/job/NN-service
docker compose build nn-service
docker compose up -d
docker compose logs -f nn-service

# 4) локальный просмотр камер (на хосте, не в докере):
python scripts/view_cameras.py --storage ./storage
```

Web viewer (`app/api/viewer.py`, FastAPI/MJPEG) **отключён по умолчанию**
(`VIEWER_ENABLED=false`) — при `network_mode: "service:vpn"` без `ports` на
хост его всё равно не пробросить. Вместо него — `scripts/view_cameras.py`,
который читает те же аннотированные JPEG из `storage/detections/<CAM>/latest.jpg`.

---

## Конфиг

Все настройки — в `.env`. Ключевое:

```dotenv
EVENT_SERVICE_BASE_URL=http://host.docker.internal:8000

YOLO_MODEL_PATH=/app/models/yolov8n.pt
YOLO_DEVICE=cpu                 # cuda:0 при GPU
YOLO_TRACKER_CONFIG=bytetrack.yaml
YOLO_CLASSES=person,car,bus,truck,motorcycle

FRAME_SAMPLE_EVERY_N=5
DETECTION_CONFIDENCE=0.35

PERSON_WITHOUT_CAR_SEC=30
PERSON_TOO_LONG_AT_STATION_SEC=120
CAR_TOO_LONG_SEC=300
EVENT_COOLDOWN_SEC=60

REID_GRACE_SEC=8
REID_RADIUS_PX=150

PRE_EVENT_BUFFER_SECONDS=5
POST_EVENT_RECORD_SECONDS=5
CLIP_FPS=10

VIEWER_ENABLED=false
```

Полная таблица всех переменных — в `RUNBOOK.md §3`.

---

## Разметка зон

Запускается **локально на ноуте** (нужен GUI для `cv2.imshow`):

```bash
python scripts/draw_zones.py \
    --rtsp rtsp://admin:admin@172.20.36.167:554/live/sub \
    --camera-code CAM-167
```

Хоткеи: `LMB`/`RMB` — точка/закрыть полигон, `f`/`c`/`s` — тип зоны
(forbidden/column/station), `w` — сохранить JSON, `q` — выход.

Файлы `config/zones/CAM-XXX.json` попадают в контейнер через маунт
`./config:/app/config`, пересборка не нужна.

Подробности — в `config/zones/README.md`.

---

## Что сейчас заглушка

- **Media storage** — `LocalStubMediaStorage` копирует в папку и возвращает
  fake URL `http://localhost:9000/fake-s3/...`. Замена на S3 — в
  `app/clients/media_storage.py`.
- **Web viewer** — выключен, см. выше.

---

## Куда смотреть разным ролям

**CV engineer:**
- `app/adapters/yolo_detector.py` — модель и трекинг;
- `app/services/zone_scenario_engine.py` + `event_tracker.py` — сценарии;
- `app/adapters/zone_manager.py` — работа с полигонами;
- `app/domain/models.py` — контракты между слоями.

**Backend / integration engineer:**
- `app/clients/event_service.py`, `app/clients/media_storage.py`;
- `app/services/pipeline.py`;
- `docker-compose.yml`, `.env`.

**DevOps / infra:**
- `docker-compose.yml`, `Dockerfile`, `.env`;
- `docker/openvpn/`.
