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
