# Runbook — запуск связки NN-service + Event-service

Пошаговый запуск, диагностика и обычный апдейт-цикл. Про архитектуру
(поток данных, сценарии, ReID, storage) — в `ARCHITECTURE.md`. Про общий
обзор сервиса — в `README.md`.

---

## 0. Предусловия

- Docker + docker-compose (v2, команда `docker compose ...`).
- Репо рядом (не важно где, запускаются независимо):
  - `~/job/NN-service`
  - `~/job/Event-service`
- Локальный Python 3.11 с `opencv-python`, `numpy` на ноуте — для разметки зон и просмотра камер.
- VPN-конфиг `docker/openvpn/pfSenseAZSC-udp-1194-gasvision-config.ovpn` на месте
  (без него `nn-service` не увидит камеры).

---

## 1. Первый запуск

### 1.1. Положить веса YOLO

```bash
cd ~/job/NN-service
ls -lh models/
# должен быть yolov8n.pt (~6 МБ, уже в репо).
# Если нужен yolov8s или yolov8m — скопируй и поменяй YOLO_MODEL_PATH в .env.
```

### 1.2. (Опционально) Разметить зоны

Без разметки камеры запустятся: `person_too_long_at_station` и
`car_too_long_at_column` работают на full-frame. `person_in_forbidden_zone`
и `person_without_car_at_column` начинают стрелять только после появления
соответствующего JSON.

Скрипт разметки запускается **локально на ноуте**, не внутри докера (нужен
GUI для `cv2.imshow`, ноуту нужен доступ к RTSP через VPN):

```bash
cd ~/job/NN-service
pip install opencv-python numpy   # один раз, в свой venv

# С живой камеры:
python scripts/draw_zones.py \
    --rtsp rtsp://admin:admin@172.20.36.167:554/live/sub \
    --camera-code CAM-167

# Либо с сохранённого стопкадра (`ffmpeg -i rtsp://... -frames:v 1 cam167.jpg`):
python scripts/draw_zones.py --image cam167.jpg --camera-code CAM-167
```

Хоткеи:

| Клавиша | Действие                             |
|---------|--------------------------------------|
| LMB     | добавить точку                       |
| RMB     | закрыть полигон (≥ 3 точки)          |
| `f`     | режим **forbidden**                  |
| `c`     | режим **column**                     |
| `s`     | режим **station**                    |
| `w`     | записать `config/zones/<CAM>.json`   |
| Esc     | сбросить текущий полигон             |
| `q`     | выход                                |

Повторить на каждую камеру. Файлы `config/zones/CAM-XXX.json` попадают в
контейнер через маунт `./config:/app/config` — пересборка образа не нужна.

### 1.3. Запустить Event-service (и БД)

Отдельный docker-compose: Postgres + FastAPI на `:8000`.

```bash
cd ~/job/Event-service
docker compose up -d --build
docker compose ps                       # db healthy, api up
curl -s http://localhost:8000/health    # {"status":"ok"}
```

### 1.4. Запустить NN-service

```bash
cd ~/job/NN-service
docker compose build nn-service         # после изменений в app/ обязательно
docker compose up -d                    # vpn + nn-service
docker compose logs -f nn-service
```

На старте для каждой камеры в логах:

```
Started pipeline thread for station=AZS-001 camera=CAM-167 zones=3
Opening RTSP stream: rtsp://admin:admin@172.20.36.167:554/live/sub
ZoneScenarioEngine ready: forbidden=1 columns=1 stations=1
```

`forbidden=0 columns=1 stations=1` на камере без JSON зон — ожидаемо,
это full-frame fallback.

---

## 2. Как проверить, что всё работает

### 2.1. Локальный просмотр камер

Web viewer **выключен** (`VIEWER_ENABLED=false`) — см. `ARCHITECTURE.md §Viewer`.
Для визуального контроля запускается отдельный python-скрипт на хосте:

```bash
cd ~/job/NN-service
python scripts/view_cameras.py --storage ./storage
```

Он читает `storage/detections/<CAM>/latest.jpg` и показывает аннотированные
кадры с bbox/track_id в одном `cv2` окне. Хоткеи:

| Клавиша       | Действие                                  |
|---------------|-------------------------------------------|
| `1`..`9`      | переключиться на N-ую камеру              |
| `a` / `d`     | ← / → по списку камер                     |
| `r`           | пересканировать `storage/detections/`     |
| `q` / Esc     | выход                                     |

Одну конкретную камеру:

```bash
python scripts/view_cameras.py --camera CAM-167 --fps 15
```

Отдельный кадр без скрипта — просто открой последний JPEG:

```bash
open storage/detections/CAM-167/latest.jpg
```

### 2.2. Healthcheck event-api

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}

curl -s 'http://localhost:8000/v1/events?limit=5' | jq
# [] сразу после запуска, потом массив событий
```

### 2.3. События в логах

`docker compose logs -f nn-service` — при срабатывании увидишь строки вида:

```
[EVENT] station=AZS-001 cam=CAM-167 Человек #7 в запрещённой зоне «forbidden_1»
Creating event in event-srv: {...}
Collecting post-event frames for event=42
Clip attached to event=42
```

### 2.4. Медиа (snapshot + клип + детекции)

Всё пишется в `./storage/` (в контейнере — `/tmp/nn-service-media`):

```bash
# последний аннотированный кадр (перезаписывается каждые N кадров)
ls -lh ~/job/NN-service/storage/detections/CAM-167/

# snapshot и клипы по событиям
ls -lh ~/job/NN-service/storage/snapshots/ | tail
ls -lh ~/job/NN-service/storage/clips/     | tail
ls -lh ~/job/NN-service/storage/gasvision-media/AZS-001/CAM-167/
```

У каждого `EventMedia` в Event-service поле `s3_url` = сейчас stub
`http://localhost:9000/fake-s3/...`. Реальный S3 прикручивается в
`app/clients/media_storage.py` (`LocalStubMediaStorage` → `boto3`/minio).

### 2.5. Список событий по API

```bash
curl -s 'http://localhost:8000/v1/events?station_code=AZS-001&limit=20' \
    | jq '.[].title'
```

---

## 3. Конфиг, что крутить

Все настройки — в `NN-service/.env` (подхватывается через `env_file` в
docker-compose). После правки `.env` пересборка не нужна, достаточно
`docker compose up -d`.

| Переменная                             | По умолчанию   | Что делает                                               |
|----------------------------------------|----------------|----------------------------------------------------------|
| `EVENT_SERVICE_BASE_URL`               | http://host.docker.internal:8000 | base URL event-service                 |
| `EVENT_SOURCE`                         | cv             | поле source у всех событий                               |
| `EVENT_DEFAULT_SEVERITY`               | med            | default severity, сценарий может переопределить          |
| `EVENT_DEFAULT_STATUS`                 | open           | default status нового события                            |
| `MEDIA_BUCKET`                         | gasvision-media | имя "bucket" для snapshot/clip в stub storage           |
| `MEDIA_BASE_DIR`                       | /tmp/nn-service-media | куда stub кладёт файлы (внутри контейнера)         |
| `YOLO_MODEL_PATH`                      | /app/models/yolov8n.pt | путь до весов                                    |
| `YOLO_DEVICE`                          | cpu            | `cuda:0` при наличии GPU                                 |
| `YOLO_CLASSES`                         | person,car,bus,truck,motorcycle | какие классы трекать                    |
| `YOLO_PERSON_LABELS`                   | person         | какие метки считать "человеком"                          |
| `YOLO_VEHICLE_LABELS`                  | car,bus,truck,motorcycle | какие метки считать "машиной"                  |
| `YOLO_TRACKER_CONFIG`                  | bytetrack.yaml | `botsort.yaml` — лучше по reid, дороже по CPU            |
| `DETECTION_CONFIDENCE`                 | 0.35           | порог уверенности YOLO                                   |
| `DETECTION_IOU`                        | 0.45           | IoU threshold для NMS                                    |
| `FRAME_SAMPLE_EVERY_N`                 | 5              | каждый N-й кадр идёт в детектор                          |
| `PERSON_WITHOUT_CAR_SEC`               | 30             | человек у колонки без авто дольше → событие              |
| `PERSON_TOO_LONG_AT_STATION_SEC`       | 120            | человек на АЗС дольше → событие                          |
| `CAR_TOO_LONG_SEC`                     | 300            | машина у колонки дольше → событие                        |
| `EVENT_COOLDOWN_SEC`                   | 60             | per-(event_type, track_id) кулдаун в EventTracker        |
| `TRIGGER_COOLDOWN_SECONDS`             | 30             | дополнительный camera-level кулдаун в pipeline           |
| `REID_GRACE_SEC`                       | 8              | сколько секунд держим исчезнувший трек для reid          |
| `REID_RADIUS_PX`                       | 150            | радиус (px) для reid-матча                               |
| `PERSON_CAR_PROXIMITY_PX`              | 200            | радиус «машина рядом с человеком» для `person_without_car` |
| `PRE_EVENT_BUFFER_SECONDS`             | 5              | длина клипа ДО события                                   |
| `POST_EVENT_RECORD_SECONDS`            | 5              | длина клипа ПОСЛЕ события                                |
| `CLIP_FPS`                             | 10             | fps сохраняемого MP4                                     |
| `MAX_STREAM_RECONNECT_DELAY_SECONDS`   | 10             | задержка между переподключениями RTSP                    |
| `FALLBACK_FRAME_WIDTH/HEIGHT`          | 1920 / 1080    | размер виртуальной full-frame зоны                       |
| `VIEWER_ENABLED`                       | false          | включать ли web viewer (FastAPI/MJPEG)                   |

Камеры и пути к зонам — в `NN-service/config/streams.yaml`:

```yaml
streams:
  - station_code: AZS-001
    camera_code: CAM-167
    rtsp_url: rtsp://admin:admin@172.20.36.167:554/live/sub
    enabled: true
    zones_config_path: /app/config/zones/CAM-167.json   # может отсутствовать
```

---

## 4. Типичные ошибки и что с ними делать

### «RTSP stream read failed, reconnecting»

VPN не поднят или камера недоступна. Проверь:

```bash
docker compose logs vpn | tail -20
docker compose exec nn-service sh -c "ping -c 2 172.20.36.167 || true"
```

### «No enabled streams found in streams config»

В `config/streams.yaml` все `enabled: false` или YAML битый. Проверь
прямо в контейнере:

```bash
docker compose exec nn-service cat /app/config/streams.yaml
```

### «None of class_labels=... were found in model classes»

В `.env` `YOLO_CLASSES` содержит метку, которой нет в модели. Посмотри
классы модели:

```bash
docker compose exec nn-service python - <<'PY'
from ultralytics import YOLO
print(YOLO('/app/models/yolov8n.pt').names)
PY
```

### События приходят дубликатом

Два слоя антидупа: `EventTracker._cooldowns` (per event_type+track_id) и
`TriggerCooldown` в pipeline. Если трекер теряет ID — подкрути
`REID_GRACE_SEC` вверх (12–15) и `REID_RADIUS_PX` под разрешение картинки.

### `person_in_forbidden_zone` не стреляет

Проверь, что в JSON есть `forbidden_zones` с валидными полигонами и что
путь в `streams.yaml → zones_config_path` существует внутри контейнера:

```bash
docker compose exec nn-service ls /app/config/zones/
docker compose exec nn-service cat /app/config/zones/CAM-167.json | head -30
```

### Event-service отвечает `RemoteProtocolError`

Старая версия event-service роняла keep-alive соединения. Начиная с
текущей версии pipeline, один упавший триггер больше не кладёт весь
поток камеры — `_handle_trigger` и `_finalize_clip` обёрнуты в try/except.
В логе увидишь stack trace, камера продолжит работать.

### Нагрузка на CPU высокая

`FRAME_SAMPLE_EVERY_N` увеличить (5 → 8 → 10). Либо уменьшить модель до
`yolov8n.pt`. Либо вывести YOLO на GPU (`YOLO_DEVICE=cuda:0`).

### Локальный view_cameras.py пишет "No cameras"

Значит nn-service ещё не успел записать ни один кадр в `storage/detections/`.
Подожди ~10 сек после старта контейнера и нажми `r` в окне.

---

## 5. Обычный апдейт-цикл

```bash
cd ~/job/NN-service
git pull                              # или свои правки
docker compose build nn-service       # если менялись .py / Dockerfile / requirements.txt
docker compose up -d                  # если менялся только .env / config/ — достаточно up -d
docker compose logs -f nn-service
```

Только зоны/streams поменялись — `docker compose restart nn-service` (volume
уже содержит новый конфиг).

---

## 6. Полный локальный чек за 5 минут (без реальных камер)

Если камер под рукой нет, можно запустить с mp4-файлом в качестве источника
(`rtsp_url: /app/config/test.mp4` + положить `test.mp4` в `config/`).
OpenCV умеет открывать файлы через тот же `VideoCapture`. Event-service и
детекция отработают полностью, VPN не нужен — выключи зависимость в
`docker-compose.yml` на время теста или подними только `nn-service` без `vpn`.
