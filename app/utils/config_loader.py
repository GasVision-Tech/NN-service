from __future__ import annotations

from pathlib import Path
import yaml

from app.domain.models import StreamConfig


def load_streams_config(path: str) -> list[StreamConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    streams = []
    for item in data.get("streams", []):
        streams.append(
            StreamConfig(
                station_code=item["station_code"],
                camera_code=item["camera_code"],
                rtsp_url=item["rtsp_url"],
                enabled=bool(item.get("enabled", True)),
            )
        )
    return [stream for stream in streams if stream.enabled]