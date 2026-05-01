"""
Интерактивная разметка зон под конкретную камеру.

ВНИМАНИЕ: запускается НЕ внутри docker (нужен cv2.imshow → GUI). Локально,
с хоста, у которого есть доступ к RTSP или к mp4-файлу. Пишет результат
в config/zones/<camera_code>.json в корне репозитория.

Использование:
    python scripts/draw_zones.py --rtsp rtsp://admin:admin@172.20.36.167/live/sub \
                                 --camera-code CAM-167

--20 sec forbidden zone--

    # Или из снимка:
    python scripts/draw_zones.py --image /path/to/frame.jpg --camera-code CAM-167

Hotkeys:
    LMB    — добавить точку
    RMB    — закрыть полигон (min 3 точки)
    f      — режим FORBIDDEN
    c      — режим COLUMN
    s      — режим STATION
    w      — записать в config/zones/<camera>.json
    Esc    — сбросить текущий полигон
    q      — выход
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ZONE_COLORS = {
    "forbidden": (0, 0, 220),
    "column": (0, 200, 0),
    "station": (200, 200, 0),
}
ZONE_JSON_KEY = {
    "forbidden": "forbidden_zones",
    "column": "column_zones",
    "station": "station_zones",
}


def _grab_frame(*, rtsp: str | None, image: str | None) -> np.ndarray:
    if image:
        frame = cv2.imread(image)
        if frame is None:
            raise RuntimeError(f"Cannot read image: {image}")
        return frame
    if rtsp:
        cap = cv2.VideoCapture(rtsp)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open RTSP: {rtsp}")
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            raise RuntimeError("Failed to grab frame from RTSP")
        return frame
    raise RuntimeError("Either --rtsp or --image must be provided")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rtsp", help="RTSP URL")
    parser.add_argument("--image", help="Путь до JPEG/PNG стопкадра")
    parser.add_argument("--camera-code", required=True, help="Camera code из streams.yaml (e.g. CAM-167)")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "config" / "zones"),
        help="Куда писать JSON (default: config/zones/ в корне репо)",
    )
    args = parser.parse_args()

    frame = _grab_frame(rtsp=args.rtsp, image=args.image)

    zones_data: dict[str, list] = {"forbidden_zones": [], "column_zones": [], "station_zones": []}
    current_polygon: list[list[int]] = []
    current_type = "forbidden"
    counters = {"forbidden": 0, "column": 0, "station": 0}

    def on_mouse(event: int, x: int, y: int, flags: int, param) -> None:  # noqa: ANN001
        nonlocal current_polygon
        if event == cv2.EVENT_LBUTTONDOWN:
            current_polygon.append([x, y])
        elif event == cv2.EVENT_RBUTTONDOWN and len(current_polygon) >= 3:
            counters[current_type] += 1
            zid = f"{current_type}_{counters[current_type]}"
            entry = {"id": zid, "name": f"Zone {zid}", "polygon": list(current_polygon)}
            zones_data[ZONE_JSON_KEY[current_type]].append(entry)
            print(f"[+] Added {zid} ({len(current_polygon)} points)")
            current_polygon = []

    window = f"GasVision zones — {args.camera_code}"
    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)

    print("Keys: f=forbidden  c=column  s=station  w=save  Esc=reset  q=quit")
    print(f"Current mode: {current_type.upper()}")

    while True:
        display = frame.copy()

        for zkey, color_key in (("forbidden_zones", "forbidden"), ("column_zones", "column"), ("station_zones", "station")):
            for z in zones_data[zkey]:
                pts = np.array(z["polygon"], dtype=np.int32)
                cv2.polylines(display, [pts], True, ZONE_COLORS[color_key], 2)

        if len(current_polygon) > 1:
            pts = np.array(current_polygon, dtype=np.int32)
            cv2.polylines(display, [pts], False, ZONE_COLORS[current_type], 1)
        for pt in current_polygon:
            cv2.circle(display, tuple(pt), 4, ZONE_COLORS[current_type], -1)

        cv2.putText(
            display, f"mode={current_type}", (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, ZONE_COLORS[current_type], 2,
        )
        cv2.imshow(window, display)
        key = cv2.waitKey(30) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("f"):
            current_type = "forbidden"
            print(">>> mode = FORBIDDEN")
        elif key == ord("c"):
            current_type = "column"
            print(">>> mode = COLUMN")
        elif key == ord("s"):
            current_type = "station"
            print(">>> mode = STATION")
        elif key == ord("w"):
            out_dir = Path(args.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{args.camera_code}.json"
            out_path.write_text(json.dumps(zones_data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[v] saved {out_path}")
        elif key == 27:
            current_polygon = []
            print("[!] current polygon cleared")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
