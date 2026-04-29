#!/usr/bin/env python3
"""
GasVision — комбинированный live-viewer + редактор checks.yaml.

Запускается ЛОКАЛЬНО на ноуте, не в докере.

    pip install opencv-python numpy pyyaml      # один раз, в свой venv
    python scripts/view_cameras.py
    python scripts/view_cameras.py --storage ./storage
    python scripts/view_cameras.py --camera CAM-167 --fps 15

Что показывает:
* cv2-окно — аннотированный JPEG последнего кадра с выбранной камеры
  (storage/detections/<CAM>/latest.jpg, который пишет StreamPipeline).
* Терминал — таблица камер и проверок, принимает команды.

Два независимых способа управления — выбирай удобный.

ТЕРМИНАЛ (печатать в окне терминала и жать Enter):

    1..N        переключиться на N-ую камеру
    c1..c4      toggle проверку на текущей камере (c1, c2, c3, c4)
    l           перепечатать меню (если scrollback забит)
    s           сохранить config/checks.yaml
    r           перечитать config/checks.yaml с диска
    q           выход

CV2-ОКНО (одиночные клавиши, когда окно с видео в фокусе):

    1..9        переключить камеру по индексу
    a / d       prev / next камера
    z x c v     toggle проверки 1 / 2 / 3 / 4 на текущей камере
    R           rescan storage/detections/
    s           сохранить
    q / Esc     выход

Сохранение хирургическое: переписывается только блок `cameras:` в
config/checks.yaml, остальное (header, defaults, implemented_checks,
roadmap, комментарии) остаётся без изменений.

После save применить на сервисе:

    docker compose restart nn-service
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import yaml


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

WINDOW = "gasvision-viewer"

# Имена обязаны совпадать с EventType из app/services/event_tracker.py.
ALL_CHECKS: list[str] = [
    "person_too_long_at_station",
    "car_too_long_at_station",
    "person_in_forbidden_zone",
    "person_without_car_at_column",
]

CHECK_TITLES: dict[str, str] = {
    "person_too_long_at_station":
        "Долгое нахождение человека на станции",
    "car_too_long_at_station":
        "Долгое нахождение машины на станции",
    "person_in_forbidden_zone":
        "Пересечение запретной зоны",
    "person_without_car_at_column":
        "Человек у колонки без машины рядом",
}


# ---------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------

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
        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2,
    )
    return img


def overlay_camera_name(frame: np.ndarray, cam_code: str) -> np.ndarray:
    """Тонкая подпись с именем камеры в углу — чтобы не путать камеры
    в cv2 окне. Никаких подсказок про хоткеи (всё в терминале)."""
    h, w = frame.shape[:2]
    bar_h = 34
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, bar_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    cv2.putText(
        frame, cam_code, (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA,
    )
    return frame


# ---------------------------------------------------------------------
# Checks editor (surgical YAML save)
# ---------------------------------------------------------------------

class ChecksEditor:
    """
    In-memory модель `cameras:` блока + хирургическая запись на диск.
    `defaults`, `implemented_checks`, `roadmap` и все комментарии
    переживают save без изменений.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.dirty = False
        self.defaults: set[str] = set()
        # cam_code → {"station_code": str, "enabled": set[str]}
        self.cameras: dict[str, dict] = {}
        self._load()

    # ---- IO --------------------------------------------------------

    def _load(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        self.defaults = set((data.get("defaults") or {}).get("enabled_checks") or [])
        self.cameras.clear()
        for cam_code, block in (data.get("cameras") or {}).items():
            block = block or {}
            self.cameras[str(cam_code)] = {
                "station_code": block.get("station_code", ""),
                "enabled": set(block.get("enabled_checks") or []),
            }
        self.dirty = False

    def reload(self) -> None:
        self._load()
        print("✓ Reloaded from disk.")

    def save(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)

        start = None
        for i, line in enumerate(lines):
            if line.rstrip("\n").rstrip() == "cameras:":
                start = i + 1
                break
        if start is None:
            raise RuntimeError(f"`cameras:` block not found in {self.path}")

        end = len(lines)
        for j in range(start, len(lines)):
            s = lines[j].rstrip("\n")
            if s == "":
                continue
            if s.startswith(" ") or s.startswith("\t"):
                continue
            end = j
            break

        body_lines: list[str] = []
        for cam_code in sorted(self.cameras):
            cam = self.cameras[cam_code]
            body_lines.append(f"  {cam_code}:")
            if cam["station_code"]:
                body_lines.append(f"    station_code: {cam['station_code']}")
            body_lines.append("    enabled_checks:")
            for check in sorted(cam["enabled"]):
                body_lines.append(f"      - {check}")
            body_lines.append("")
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        body_lines.append("")
        body_lines.append("")
        new_body = "\n".join(body_lines)
        if not new_body.endswith("\n"):
            new_body += "\n"

        new_text = "".join(lines[:start]) + new_body + "".join(lines[end:])
        self.path.write_text(new_text, encoding="utf-8")
        self.dirty = False
        print(f"\n✓ Saved to {self.path}")
        print("  Apply: docker compose restart nn-service")

    # ---- mutations -------------------------------------------------

    def toggle(self, cam_code: str, check_key: str) -> None:
        cam = self.cameras[cam_code]
        if check_key in cam["enabled"]:
            cam["enabled"].discard(check_key)
            state = "OFF"
        else:
            cam["enabled"].add(check_key)
            state = "ON"
        self.dirty = True
        print(f"  {cam_code}: {check_key} → {state}    [unsaved]")


# ---------------------------------------------------------------------
# Dashboard (cv2 в main thread, REPL в worker thread)
# ---------------------------------------------------------------------

class Dashboard:
    def __init__(
        self,
        *,
        storage: Path,
        cameras_filter: str | None,
        fps: int,
        editor: ChecksEditor | None,
    ) -> None:
        self.storage = storage
        self.fps = fps
        self.editor = editor

        if cameras_filter:
            self.cameras = [cameras_filter]
        else:
            self.cameras = discover_cameras(storage)

        self._lock = threading.Lock()
        self._current = self.cameras[0] if self.cameras else ""
        self._exit = False

    # ---- shared state accessors -----------------------------------

    @property
    def current_camera(self) -> str:
        with self._lock:
            return self._current

    def set_camera(self, cam_code: str, *, source: str = "repl") -> None:
        with self._lock:
            if cam_code == self._current:
                return
            self._current = cam_code
        prefix = "[repl]" if source == "repl" else "\n[hotkey]"
        print(f"{prefix} switched to {cam_code}")

    def cycle_camera(self, delta: int) -> None:
        if not self.cameras:
            return
        with self._lock:
            try:
                i = self.cameras.index(self._current)
            except ValueError:
                i = 0
            new_i = (i + delta) % len(self.cameras)
            new_cam = self.cameras[new_i]
            if new_cam == self._current:
                return
            self._current = new_cam
        print(f"\n[hotkey] switched to {new_cam}")

    def request_exit(self) -> None:
        with self._lock:
            self._exit = True

    @property
    def exit_requested(self) -> bool:
        with self._lock:
            return self._exit

    # ---- lifecycle ------------------------------------------------

    def run(self) -> None:
        print(f"[info] storage = {self.storage}")
        print(f"[info] cameras = {self.cameras or '(пусто — подожди, nn-service ещё не писал кадры)'}")
        if self.editor:
            print(f"[info] checks  = {self.editor.path}")
        else:
            print("[info] checks editor выключен (config/checks.yaml не найден)")
        print()

        repl = threading.Thread(target=self._repl_loop, daemon=True, name="dashboard-repl")
        repl.start()
        try:
            self._video_loop()
        finally:
            self.request_exit()
        # REPL — daemon-поток, умрёт вместе с main.

    # ---- video loop (main thread) ---------------------------------

    def _video_loop(self) -> None:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        period = 1.0 / max(self.fps, 1)

        while not self.exit_requested:
            t0 = time.time()
            cam = self.current_camera

            if not cam:
                img = placeholder("No cameras. Press 'r' to rescan or wait.")
            else:
                frame = load_frame(self.storage, cam)
                if frame is None:
                    img = placeholder(f"{cam}: no frame yet (waiting for nn-service)")
                else:
                    img = overlay_camera_name(frame, cam)

            cv2.imshow(WINDOW, img)

            try:
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break

            dt = time.time() - t0
            wait_ms = max(1, int((period - dt) * 1000))
            key = cv2.waitKey(wait_ms) & 0xFF

            if key == 0xFF:
                continue
            if key in (ord("q"), 27):
                self.request_exit()
                break
            if key == ord("R"):
                self.cameras = discover_cameras(self.storage) or self.cameras
                print(f"\n[hotkey] rescan → {self.cameras}")
                continue
            if key == ord("a"):
                self.cycle_camera(-1)
                continue
            if key == ord("d"):
                self.cycle_camera(+1)
                continue
            if key == ord("s"):
                self._handle_save()
                continue
            # z / x / c / v — toggle check 1 / 2 / 3 / 4 на текущей камере.
            # Дублирует REPL-команды c1..c4, но работает прямо из cv2-окна.
            toggle_keys = {ord("z"): 0, ord("x"): 1, ord("c"): 2, ord("v"): 3}
            if key in toggle_keys:
                self._handle_toggle(str(toggle_keys[key] + 1))
                continue
            if ord("1") <= key <= ord("9"):
                n = key - ord("1")
                if n < len(self.cameras):
                    self.set_camera(self.cameras[n], source="hotkey")
                continue

        cv2.destroyAllWindows()

    # ---- REPL loop (worker thread) --------------------------------

    def _repl_loop(self) -> None:
        self._print_menu()
        while not self.exit_requested:
            try:
                cmd = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.request_exit()
                return

            if self.exit_requested:
                return
            if not cmd:
                continue

            if cmd == "q":
                self._handle_quit()
                return

            if cmd == "l":
                self._print_menu()
                continue

            if cmd == "s":
                self._handle_save()
                continue

            if cmd == "r":
                self._handle_reload()
                continue

            # Camera switch: pure digit
            if cmd.isdigit():
                n = int(cmd)
                if 1 <= n <= len(self.cameras):
                    self.set_camera(self.cameras[n - 1], source="repl")
                else:
                    print(f"  out of range: 1..{len(self.cameras)}")
                continue

            # Check toggle: c<digit>
            if cmd.startswith("c") and cmd[1:].isdigit():
                self._handle_toggle(cmd[1:])
                continue

            print(f"  unknown command: {cmd!r}    (l=list, q=quit)")

    # ---- REPL command handlers ------------------------------------

    def _handle_quit(self) -> None:
        if self.editor and self.editor.dirty:
            try:
                ans = input("Unsaved changes. Save before quit? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "y"
            if ans in ("", "y", "yes"):
                try:
                    self.editor.save()
                except Exception as exc:
                    print(f"Save failed: {exc}", file=sys.stderr)
        self.request_exit()
        print("bye!")

    def _handle_save(self) -> None:
        if not self.editor:
            print("(checks editor disabled — нет config/checks.yaml)")
            return
        if not self.editor.dirty:
            print("Nothing to save.")
            return
        try:
            self.editor.save()
        except Exception as exc:
            print(f"Save failed: {exc}", file=sys.stderr)

    def _handle_reload(self) -> None:
        if not self.editor:
            print("(checks editor disabled)")
            return
        if self.editor.dirty:
            try:
                ans = input("Discard unsaved changes? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if ans not in ("y", "yes"):
                return
        try:
            self.editor.reload()
        except Exception as exc:
            print(f"Reload failed: {exc}", file=sys.stderr)

    def _handle_toggle(self, n_str: str) -> None:
        if not self.editor:
            print("(checks editor disabled)")
            return
        n = int(n_str)
        if not (1 <= n <= len(ALL_CHECKS)):
            print(f"  out of range: c1..c{len(ALL_CHECKS)}")
            return
        cam = self.current_camera
        if cam not in self.editor.cameras:
            print(f"  камера {cam} не описана в checks.yaml — нечего тоглить")
            return
        self.editor.toggle(cam, ALL_CHECKS[n - 1])

    # ---- menu rendering -------------------------------------------

    def _print_menu(self) -> None:
        print()
        print("=" * 78)
        print("GasVision — Live Cameras + Checks Editor")
        if self.editor:
            print(f"  config: {self.editor.path}")
            if self.editor.dirty:
                print("  * unsaved changes")
            if self.editor.defaults:
                print(f"  defaults: {sorted(self.editor.defaults)}")
        print("=" * 78)
        print()
        print("Cameras (* = currently shown):")

        cur = self.current_camera
        for i, cam_code in enumerate(self.cameras, 1):
            marker = "*" if cam_code == cur else " "
            if self.editor and cam_code in self.editor.cameras:
                cam = self.editor.cameras[cam_code]
                on_count = len(cam["enabled"])
                ok = "✓" if cam["enabled"] == self.editor.defaults else " "
                missing = sorted(self.editor.defaults - cam["enabled"])
                extras = sorted(cam["enabled"] - self.editor.defaults)
                tail = []
                if missing:
                    tail.append(f"off: {', '.join(missing)}")
                if extras:
                    tail.append(f"+extra: {', '.join(extras)}")
                tail_str = ("    " + "    ".join(tail)) if tail else ""
                print(f"  [{i}]{marker}{cam_code:<10}  {on_count} on  {ok}{tail_str}")
            else:
                print(f"  [{i}]{marker}{cam_code:<10}  (нет в checks.yaml)")
        print()

        if self.editor and cur in self.editor.cameras:
            print(f"Checks for {cur}:")
            cam = self.editor.cameras[cur]
            for i, check_key in enumerate(ALL_CHECKS, 1):
                on = check_key in cam["enabled"]
                mark = "✓ ON " if on else "✗ OFF"
                in_default = check_key in self.editor.defaults
                note = "" if in_default else "   [не в default]"
                print(f"  [c{i}] {mark}   {check_key}{note}")
                print(f"           {CHECK_TITLES[check_key]}")
            print()

        print("─" * 78)
        print("Commands — печатать в этот ТЕРМИНАЛ и нажимать Enter:")
        print("  1..N      switch camera               c1 c2 c3 c4   toggle check 1..4")
        print("  l         re-list menu                s             save")
        print("  r         reload from disk            q             quit")
        print()
        print("Hotkeys — нажимать в окне ВИДЕО (cv2):")
        print("  1..9      switch camera               z x c v       toggle check 1..4")
        print("  a / d     prev / next camera          s             save")
        print("  R         rescan storage              q / Esc       quit")
        print()
        print("Пример: чтобы выключить проверку «человек на станции» на CAM-170 —")
        print("  печатай в терминале:  4   ← Enter   (переключиться на CAM-170)")
        print("                        c1  ← Enter   (toggle проверки c1)")
        print("                        s   ← Enter   (сохранить)")


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--storage",
        default=str(repo_root / "storage"),
        help="путь до корня storage (default: <repo>/storage)",
    )
    ap.add_argument(
        "--config",
        default=str(repo_root / "config" / "checks.yaml"),
        help="путь до checks.yaml (default: <repo>/config/checks.yaml)",
    )
    ap.add_argument(
        "--camera",
        default=None,
        help="показать только эту камеру",
    )
    ap.add_argument(
        "--fps",
        type=int,
        default=10,
        help="частота опроса latest.jpg (default: 10)",
    )
    ap.add_argument(
        "--no-checks",
        action="store_true",
        help="не включать редактор checks.yaml (только просмотр)",
    )
    args = ap.parse_args()

    storage = Path(args.storage).resolve()
    if not storage.is_dir():
        raise SystemExit(f"[error] storage not found: {storage}")

    editor: ChecksEditor | None = None
    if not args.no_checks:
        config_path = Path(args.config).resolve()
        if config_path.is_file():
            try:
                editor = ChecksEditor(config_path)
            except Exception as exc:
                print(f"[warn] не смог загрузить {config_path}: {exc}", file=sys.stderr)
                editor = None
        else:
            print(f"[info] {config_path} не найден — редактор проверок выключен", file=sys.stderr)

    dashboard = Dashboard(
        storage=storage,
        cameras_filter=args.camera,
        fps=args.fps,
        editor=editor,
    )
    dashboard.run()


if __name__ == "__main__":
    main()
