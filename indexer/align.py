"""A page for lining up a grid, then running search, identify, and export."""

from __future__ import annotations

import argparse
import errno
import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import cv2

from indexer.discogs import save_json
from indexer.grid import (
    Grid,
    default_corners,
    grid_from_block,
    load_grid,
    outer_corners,
    save_grid,
    write_crops,
    write_preview,
)
from indexer.runs import display_path

PAGE = Path(__file__).with_name("align_page.html").read_text(encoding="utf-8")
STEPS = ("search", "identify", "export")


class AlignSession:
    def __init__(self, image_path: Path, out: Path, rows: int | None, cols: int | None, inset: int):
        self.image_path = image_path
        self.out = out
        self.image = cv2.imread(str(image_path))
        if self.image is None:
            raise ValueError(f"Could not read image {image_path}")
        height, width = self.image.shape[:2]
        grid_path = out / "grid.json"
        grid = load_grid(grid_path) if grid_path.exists() else None
        if grid is not None and not _grid_matches_photo(out, image_path):
            print(
                "Saved boxes do not match this photo, so the page starts from a fresh box.",
                flush=True,
            )
            grid = None
        self.rows = rows or (grid.rows if grid else 8)
        self.cols = cols or (grid.cols if grid else 6)
        self.inset = inset if inset is not None else (max(grid.inset_x, grid.inset_y) if grid else 6)
        self.corners = outer_corners(grid) if grid else default_corners(width, height)
        self.out.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._running = False
        self._state = _fresh_progress()

    def state(self) -> dict:
        height, width = self.image.shape[:2]
        return {
            "rows": self.rows,
            "cols": self.cols,
            "inset": self.inset,
            "corners": self.corners,
            "width": width,
            "height": height,
            "image": self.image_path.name,
            "photo": self.photo_url(),
            "out": display_path(self.out),
            "saved": (self.out / "grid.json").is_file(),
        }

    def photo_url(self) -> str:
        stat = self.image_path.stat()
        source = quote(str(self.image_path.resolve()))
        return f"/photo?src={source}&v={stat.st_mtime_ns}-{stat.st_size}"

    def save(self, payload: dict) -> dict:
        with self._lock:
            if self._running:
                raise RuntimeError("Wait until processing finishes before saving again.")
        rows = int(payload["rows"])
        cols = int(payload["cols"])
        inset = int(payload.get("inset") or 0)
        corners = payload["corners"]
        if len(corners) != 4:
            raise ValueError("Drag all four corners before saving.")
        grid: Grid = grid_from_block(self.image.shape, rows, cols, corners, inset)
        self.rows, self.cols, self.inset, self.corners = rows, cols, inset, grid.block_corners
        save_grid(grid, self.out / "grid.json")
        crops = write_crops(self.image, grid, self.out / "crops")
        write_preview(self.image, grid, self.out / "preview.jpg")
        save_json(
            self.out / "run.json",
            {"image": self.image_path.name, "image_path": str(self.image_path), "cases": len(crops)},
        )
        return {"cases": len(crops), "out": display_path(self.out), "preview": str(self.out / "preview.jpg")}

    def start_job(self, steps: list[str], force: bool, worker=None) -> None:
        if not steps or any(step not in STEPS for step in steps):
            raise ValueError("Choose search, identify, or export.")
        if not (self.out / "grid.json").is_file():
            raise ValueError("Save the crops before processing.")
        with self._lock:
            if self._running:
                raise RuntimeError("A processing step is already running.")
            self._running = True
            self._state = _fresh_progress()
            self._state["running"] = True
            self._state["step"] = steps[0]
        target = worker or self._execute
        thread = threading.Thread(target=self._guard, args=(steps, force, target), daemon=True)
        thread.start()

    def _guard(self, steps: list[str], force: bool, worker) -> None:
        try:
            worker(steps, force)
        except Exception as exc:
            self._append(f"error: {exc}")
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                self._running = False
                self._state["running"] = False

    def _execute(self, steps: list[str], force: bool) -> None:
        from indexer.cli import _log_sink, _progress_sink, cmd_export, cmd_identify, cmd_search

        def sink(message: str) -> None:
            self._append(message)
            print(message, flush=True)

        def progress(step: str, done: int, total: int, detail: str = "") -> None:
            with self._lock:
                self._state["step"] = step
                self._state["done"] = done
                self._state["total"] = total
                self._state["detail"] = detail

        log_token = _log_sink.set(sink)
        progress_token = _progress_sink.set(progress)
        try:
            args = argparse.Namespace(
                out=str(self.out),
                force=force,
                limit=0,
                pause=2.7,
                search_pause=1.0,
                overrides=None,
                releases=[],
                csv=None,
                selection=None,
            )
            commands = {"search": cmd_search, "identify": cmd_identify, "export": cmd_export}
            for step in steps:
                self._append(f"Starting {step}.")
                commands[step](args)
            self._append("Finished.")
        finally:
            _log_sink.reset(log_token)
            _progress_sink.reset(progress_token)

    def progress(self) -> dict:
        with self._lock:
            state = dict(self._state)
            state["lines"] = list(self._state["lines"])
            state["running"] = self._running
        state["results"] = _review_results(self.out)
        state["overrides"] = _saved_overrides(self.out)
        csv_path = self.out / "cd_index.csv"
        state["csv"] = csv_path.name if csv_path.is_file() else None
        state["out"] = display_path(self.out)
        return state

    def save_override(self, payload: dict) -> dict:
        index = int(payload["index"])
        if index < 1:
            raise ValueError("Pick a case first.")
        artist = str(payload.get("artist") or "").strip()
        album = str(payload.get("album") or "").strip()
        release_text = str(payload.get("release_id") or "").strip()
        entry: dict = {}
        if release_text:
            found = re.search(r"\d{3,}", release_text)
            if not found:
                raise ValueError("The Discogs release id is the number in the release address.")
            entry["release_id"] = int(found.group(0))
        if artist:
            entry["artist"] = artist
        if album:
            entry["album"] = album
        query = " ".join(part for part in (artist, album) if part)
        if query:
            entry["query"] = query
        if not entry.get("release_id") and not entry.get("query"):
            raise ValueError("Enter an artist and album, or a Discogs release id.")
        current = _saved_overrides(self.out)
        current[str(index)] = entry
        save_json(self.out / "overrides.json", current)
        return {"index": index, "override": entry, "path": display_path(self.out / "overrides.json")}

    def crop_file(self, name: str) -> Path | None:
        if not re.fullmatch(r"\d{2}\.jpg", name):
            return None
        path = (self.out / "crops" / name).resolve()
        root = (self.out / "crops").resolve()
        if path.parent != root or not path.is_file():
            return None
        return path

    def _append(self, message: str) -> None:
        with self._lock:
            self._state["lines"].append(message)


def _fresh_progress() -> dict:
    return {
        "running": False,
        "step": "",
        "done": 0,
        "total": 0,
        "detail": "",
        "lines": [],
        "error": None,
    }


def _review_results(out: Path) -> list[dict]:
    path = out / "review.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    rows = []
    for item in data.get("items") or []:
        chosen = item.get("chosen") or {}
        artist = (chosen.get("artists") or "").strip()
        title = (chosen.get("title") or "").strip()
        if artist and title:
            label = f"{artist} — {title}"
        else:
            label = title or artist
        try:
            crop = f"/crops/{int(item.get('index')):02d}.jpg"
        except (TypeError, ValueError):
            crop = ""
        rows.append(
            {
                "index": item.get("index"),
                "status": item.get("status") or "",
                "label": label,
                "note": item.get("note") or "",
                "crop": crop,
            }
        )
    return rows


def _saved_overrides(out: Path) -> dict:
    path = out / "overrides.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def make_server(session: AlignSession, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _send(self, code: int, body: bytes, content_type: str, disposition: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if disposition:
                self.send_header("Content-Disposition", disposition)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict) -> None:
            self._send(code, json.dumps(payload).encode(), "application/json")

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 2_000_000:
                raise ValueError("Request is too large.")
            return json.loads(self.rfile.read(length).decode())

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/photo":
                data = session.image_path.read_bytes()
                kind = "image/png" if session.image_path.suffix.lower() == ".png" else "image/jpeg"
                self._send(200, data, kind)
            elif path == "/state":
                self._json(200, session.state())
            elif path == "/progress":
                self._json(200, session.progress())
            elif path.startswith("/crops/"):
                crop = session.crop_file(path.removeprefix("/crops/"))
                if crop is None:
                    self._send(404, b"Not found", "text/plain")
                    return
                self._send(200, crop.read_bytes(), "image/jpeg")
            elif path == "/cd_index.csv":
                csv_path = session.out / "cd_index.csv"
                if not csv_path.is_file():
                    self._send(404, b"Not found", "text/plain")
                    return
                self._send(
                    200,
                    csv_path.read_bytes(),
                    "text/csv; charset=utf-8",
                    "attachment; filename=cd_index.csv",
                )
            else:
                self._send(404, b"Not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path not in ("/save", "/process", "/overrides"):
                self._send(404, b"Not found", "text/plain")
                return
            try:
                payload = self._read_json()
                if path == "/save":
                    result = session.save(payload)
                elif path == "/overrides":
                    result = session.save_override(payload)
                else:
                    steps = payload.get("steps") or list(STEPS)
                    session.start_job([str(step) for step in steps], bool(payload.get("force")))
                    result = session.progress()
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
                return
            except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, result)

    return ThreadingHTTPServer((host, port), Handler)


def _grid_matches_photo(out: Path, image_path: Path) -> bool:
    """A saved grid belongs to this file only. A replaced photo starts fresh."""
    grid_path = out / "grid.json"
    try:
        if image_path.stat().st_mtime > grid_path.stat().st_mtime:
            return False
    except OSError:
        return False
    meta_path = out / "run.json"
    if not meta_path.is_file():
        return True
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return False
    saved = meta.get("image_path")
    if isinstance(saved, str) and saved:
        try:
            return Path(saved).resolve() == image_path.resolve()
        except OSError:
            return False
    return meta.get("image") in (None, image_path.name)


def ensure_port_free(port: int, host: str = "0.0.0.0") -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"Port {port} is already in use, so http://127.0.0.1:{port} is still the previous photo.\n"
                "Stop that align command with Ctrl+C, then run this one again.\n"
                f"Or add --port {port + 1} and open the address that command prints."
            )
        raise
    finally:
        sock.close()


def serve(session: AlignSession, host: str = "0.0.0.0", port: int = 47321) -> None:
    server = make_server(session, host, port)
    print(f"Showing {session.image_path.resolve()}", flush=True)
    print(f"Line up the cases at http://127.0.0.1:{port}", flush=True)
    print("If the window still shows a previous photo, reload it.", flush=True)
    print("Save the crops, then start search, identify, and export on the page.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()
