# NN-Service — архитектура

## Поток данных (на одну камеру)

```
RTSP
 │
 ▼ каждый кадр
FrameRingBuffer ──── хранит последние 5 сек (pre-event буфер)
 │
 ▼ каждый N-й кадр (default N=5)
YoloTrackingDetector  →  DetectionBatch { label, bbox, confidence, track_id }
 │
 ├─► draw_detections()
 │       ├─► FrameStore.update(camera_code, jpeg)       (опц. — если viewer включён)
 │       └─► storage/detections/<CAM>/latest.jpg        (для scripts/view_cameras.py)
 │
 └─► ZoneScenarioEngine.evaluate()
          │  проверяет 4 сценария через EventTracker
          ▼
     list[ScenarioTrigger]
          │
          ├─► TriggerCooldown (per scenario_key + track_id, 30 сек)
          └─► pipeline._handle_trigger()
                  ├── snapshot → storage/snapshots/
                  ├── media_storage.upload_file(snapshot)       → image_url
                  ├── event_client.create_event(...)            → event_id
                  └── daemon thread clip-<CAM>-<event_id>:
                         pre_frames + post_frames (+5 сек с того же RTSP)
                         → media_builder.save_clip(mp4v)
                         → media_storage.upload_file(clip)       → clip_url
                         → event_client.attach_clip(event_id, clip_url)
```

Дубликаты давятся на двух уровнях:
1. `EventTracker._cooldowns` — per-(event_type, track_id), `EVENT_COOLDOWN_SEC=60`.
2. `TriggerCooldown` в pipeline — per-(station, camera, scenario, track_id),
   `TRIGGER_COOLDOWN_SECONDS=30`.

---

## Сценарии

Все регистрируются внутри `EventTracker` и вызываются из `ZoneScenarioEngine.evaluate()`.

| Ключ                            | Условие                                              | Таймер                         | Severity |
|---------------------------------|------------------------------------------------------|--------------------------------|----------|
| `person_in_forbidden_zone`      | Человек в запретной зоне                             | 0 сек                          | high     |
| `person_without_car_at_column`  | Человек у колонки без машины в радиусе               | `PERSON_WITHOUT_CAR_SEC`       | med      |
| `person_too_long_at_station`    | Человек долго на территории                          | `PERSON_TOO_LONG_AT_STATION_SEC` | med    |
| `car_too_long_at_column`        | Машина долго у колонки                               | `CAR_TOO_LONG_SEC`             | low      |

Первые два требуют зон. Последние два работают на full-frame при отсутствии
`config/zones/<CAM>.json` (`load_zones_or_fallback` синтезирует station/column
на весь кадр из `FALLBACK_FRAME_WIDTH/HEIGHT`).

---

## ReID

Между кадрами YOLO может терять трек и присваивать новый `track_id`.
`EventTracker` держит grace-буфер последних исчезнувших треков и
проверяет новые треки по:

- времени с момента исчезновения ≤ `REID_GRACE_SEC`;
- евклидову расстоянию между центрами bbox ≤ `REID_RADIUS_PX`.

Если оба условия выполняются, новый track_id «склеивается» со старым, и
состояние сценариев продолжает копиться непрерывно.

---

## Viewer

Web-viewer (`app/api/viewer.py`, FastAPI + MJPEG) **выключен по умолчанию**
(`VIEWER_ENABLED=false`). При `network_mode: "service:vpn"` контейнер
`nn-service` не может пробросить порты на хост (порты открывает только
контейнер VPN), поэтому MJPEG без отдельного хоста не доступен.

Вместо него — локальный python-скрипт на стороне пользователя:

```bash
python scripts/view_cameras.py --storage ./storage
```

Он читает `storage/detections/<CAM>/latest.jpg` (которые пишет сам pipeline
через `_publish_detections()`) и отрисовывает в `cv2.imshow`. Никакого
HTTP, никаких портов, ничего в docker-compose менять не надо — volume
`./storage:/tmp/nn-service-media` уже есть.

---

## Потоки (threads)

| Имя                 | Что делает                                                        |
|---------------------|-------------------------------------------------------------------|
| `pipeline-CAM-XXX`  | RTSP → YOLO → сценарии → создание событий (один на камеру)        |
| `clip-<CAM>-<eid>`  | сбор post-event кадров + загрузка клипа (daemon, один на событие) |

Главный поток `runner.py` поднимает по одному pipeline-треду на каждую
камеру из `streams.yaml` и просто ждёт `thread.join()`. Один сломанный
триггер (HTTP/IO-ошибка) теперь не убивает pipeline — обёрнут в `try/except`.

---

## Storage

```
storage/
├── detections/<CAM>/latest.jpg   ← последний аннотированный кадр
│                                   (перезаписывается каждые N кадров)
├── snapshots/                    ← JPEG в момент триггера
├── clips/                        ← MP4: pre + post event
└── gasvision-media/<station>/<camera>/
                                  ← fake-S3 зеркало (LocalStubMediaStorage)
```

`storage/` исключена из git (`.gitignore`) и из docker build context (`.dockerignore`).
Внутри контейнера это `/tmp/nn-service-media` (mount в `docker-compose.yml`).

---

## Ключевые файлы по слоям

**Domain:**
- `app/domain/models.py` — `StreamConfig`, `Detection`, `DetectionBatch`,
  `ScenarioTrigger`, `EventCreated` (все `@dataclass(slots=True)`).

**Adapters (I/O + тяжёлые библиотеки):**
- `app/adapters/rtsp_reader.py` — `cv2.VideoCapture` + reconnect loop.
- `app/adapters/yolo_detector.py` — `YoloTrackingDetector` (per-camera).
- `app/adapters/zone_manager.py` — Zone, load/point_in_zone/draw.

**Services (оркестрация и бизнес-логика):**
- `services/runner.py` — подъём pipeline на каждую камеру, общий `event_client`
  и `media_storage`.
- `services/pipeline.py` — главный цикл одной камеры.
- `services/zone_scenario_engine.py` — фасад над `EventTracker`, возвращает
  список триггеров.
- `services/event_tracker.py` — ReID + per-scenario state + cooldowns.
- `services/frame_buffer.py` — pre-event ring buffer.
- `services/media_builder.py` — запись snapshot/clip на диск.
- `services/cooldown.py` — `TriggerCooldown` (per-key, utc).

**Clients (внешние интеграции):**
- `clients/event_service.py` — httpx к event-service.
- `clients/media_storage.py` — stub S3; точка расширения под реальный S3.

**API / utils:**
- `api/frame_store.py` — thread-safe dict для MJPEG (используется только
  когда viewer включён).
- `api/viewer.py` — FastAPI приложение (опционально).
- `utils/config_loader.py` — `streams.yaml` → `list[StreamConfig]`.
- `utils/draw.py` — bbox и подписи на кадр.
