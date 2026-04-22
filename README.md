# nn-service

Production-oriented skeleton for GasVision CV ingestion service.

## What it does
- Reads multiple RTSP streams with OpenCV.
- Runs detector adapter on sampled frames.
- Triggers scenario engine.
- Saves snapshot locally.
- Uploads snapshot to S3 through pluggable client (currently stub/local).
- Creates event in `event-srv`.
- Records short video clip around the event (pre/post buffer).
- Uploads clip through the same pluggable media client.
- Attaches clip to the existing event in `event-srv`.

## Why this repo is structured this way
The CV engineer should be able to replace only detector/scenario logic without touching:
- RTSP ingestion
- buffering
- event-srv integration
- media upload flow
- cooldown / dedup logic
- multi-stream orchestration

## Quick start
1. Copy `.env.example` to `.env` and fill values.
2. Copy `config/streams.example.yaml` to `config/streams.yaml` and edit station/camera/rtsp list.
3. Put your OpenVPN config into `docker/openvpn/client.ovpn`.
4. Run:
   ```bash
   docker compose up --build
   
Important notes
docker-compose.yml uses an OpenVPN sidecar container. The app shares its network namespace via network_mode: "service:vpn".
S3 is currently implemented as a stub local uploader returning fake URLs. Replace LocalStubMediaStorage with a real S3 implementation later.
The default detector is an adapter around Ultralytics YOLO. It currently triggers a test scenario when a vehicle is detected.
Event API contract matches your current event-srv:
POST /v1/events
POST /v1/events/{id}/media
Safe customization points

CV engineer will usually touch only:

app/adapters/yolo_detector.py
app/services/scenario_engine.py
optionally app/domain/models.py if more metadata is needed

Everything else is infrastructure around the model.