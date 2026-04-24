#!/usr/bin/env python3
"""
Локальный viewer кадров nn-service в отдельном cv2-окне.

Читает аннотированные JPEG, которые пишет сам pipeline в
    storage/detections/<CAMERA>/latest.jpg
(см. StreamPipeline._publish_detections). Ни сеть, ни FastAPI, ни
docker-compose правки не нужны — файлы уже лежат на хосте через
volume `./storage:/tmp/nn-service-media` в docker-compose.yml.

Запуск (из корня репы):

    python scripts/view_cameras.py
    python scripts/view_cameras.py --storage ./storage
    python scripts/view_cameras.py --camera CAM-167       # сразу одна
    python scripts/view_cameras.py --fps 15

Горячие клавиши в окне:

    1..9       выбрать N-ую камеру из списка
    a / d      ← / → по списку
    r          пересканировать storage/detections/
    q / Esc    выход
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np


WINDOW = "gasvision-viewer"
HINT = "1..9 select | a/d switch | r rescan | q quit"


def discover_cameras(storage: Path) -> list[str]:
    detections_dir = storage / "detections"
    if not detections_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in detections_dir.iterdir()
        if p.is_dir() and (p / "latest.jpg").exists()
    )


def load_frame(storage: Path, camera: str) -> np.ndarray | None:
    path = storage / "detections" / camera / "latest.jpg"
    if not path.exists():
        return None
    try:
        return cv2.imread(str(path))
    except Exception:
        return None


def placeholder(text: str, w: int = 960, h: int = 540) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.putText(
        img, text, (30, h // 2),
        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2,
    )
    return img


def overlay_label(frame: np.ndarray, text: str) -> np.ndarray:
    h, w = frame.shape[:2]
    bar_h = 34
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, bar_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    cv2.putText(
        frame, text, (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA,
    )
    return frame


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storage", default="./storage",
                    help="путь до корня storage (default: ./storage)")
    ap.add_argument("--camera", default=None,
                    help="показать только эту камеру (CAM-167, ...)")
    ap.add_argument("--fps", type=int, default=10,
                    help="частота опроса latest.jpg, fps (default: 10)")
    args = ap.parse_args()

    storage = Path(args.storage).resolve()
    if not storage.is_dir():
        raise SystemExit(f"[error] storage not found: {storage}")

    if args.camera:
        cameras: list[str] = [args.camera]
    else:
        cameras = discover_cameras(storage)

    idx = 0
    period = 1.0 / max(args.fps, 1)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)

    print(f"[info] storage = {storage}")
    print(f"[info] cameras = {cameras or '(пусто — подожди, nn-service ещё не писал кадры)'}")
    print(f"[hint] {HINT}")

    while True:
        t0 = time.time()

        if not cameras:
            img = placeholder("No cameras. Press 'r' to rescan, 'q' to quit.")
        else:
            cam = cameras[idx % len(cameras)]
            frame = load_frame(storage, cam)
            if frame is None:
                img = placeholder(f"{cam}: no frame yet")
            else:
                img = overlay_label(
                    frame,
                    f"{cam}  [{(idx % len(cameras)) + 1}/{len(cameras)}]   {HINT}",
                )

        cv2.imshow(WINDOW, img)

        # Выход через крестик окна
        try:
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break

        dt = time.time() - t0
        wait_ms = max(1, int((period - dt) * 1000))
        key = cv2.waitKey(wait_ms) & 0xFF

        if key in (ord("q"), 27):  # q / Esc
            break
        if key == ord("r"):
            cameras = discover_cameras(storage) if not args.camera else cameras
            idx = 0
            print(f"[info] rescan -> {cameras}")
            continue
        if key == ord("a"):
            if cameras:
                idx = (idx - 1) % len(cameras)
            continue
        if key == ord("d"):
            if cameras:
                idx = (idx + 1) % len(cameras)
            continue
        if ord("1") <= key <= ord("9"):
            n = key - ord("1")
            if cameras and n < len(cameras):
                idx = n

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
