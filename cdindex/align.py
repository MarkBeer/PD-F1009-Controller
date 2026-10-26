"""A page for lining up a grid, then running search, identify, and export."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2

from cdindex.discogs import save_json
from cdindex.grid import (
    Grid,
    default_corners,
    grid_from_block,
    load_grid,
    outer_corners,
    save_grid,
    write_crops,
    write_preview,
)
from cdindex.runs import display_path

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
            "out": display_path(self.out),
            "saved": (self.out / "grid.json").is_file(),
        }

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
        from cdindex.cli import _log_sink, _progress_sink, cmd_export, cmd_identify, cmd_search

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
        csv_path = self.out / "cd_index.csv"
        state["csv"] = csv_path.name if csv_path.is_file() else None
        state["out"] = display_path(self.out)
        return state

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
        rows.append(
            {
                "index": item.get("index"),
                "status": item.get("status") or "",
                "label": label,
                "note": item.get("note") or "",
            }
        )
    return rows


def make_server(session: AlignSession, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _send(self, code: int, body: bytes, content_type: str, disposition: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
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
            if path not in ("/save", "/process"):
                self._send(404, b"Not found", "text/plain")
                return
            try:
                payload = self._read_json()
                if path == "/save":
                    result = session.save(payload)
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


def serve(session: AlignSession, host: str = "0.0.0.0", port: int = 47321) -> None:
    server = make_server(session, host, port)
    print(f"Line up the cases at http://127.0.0.1:{port}", flush=True)
    print("Save the crops, then start search, identify, and export on the page.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()
