"""A page for lining up a grid by dragging its four corners.

The automatic preview is a regular box. On a photo taken at an angle, or
when the detector locks onto the table edge, that box can sit on the wrong
part of the picture. Dragging the corners rebuilds every case at once.
"""

from __future__ import annotations

import json
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

PAGE = Path(__file__).with_name("align_page.html").read_text(encoding="utf-8")


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
        }

    def save(self, payload: dict) -> dict:
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
        return {"cases": len(crops), "out": str(self.out), "preview": str(self.out / "preview.jpg")}


def make_server(session: AlignSession, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/photo":
                data = session.image_path.read_bytes()
                kind = "image/png" if session.image_path.suffix.lower() == ".png" else "image/jpeg"
                self._send(200, data, kind)
            elif self.path == "/state":
                self._send(200, json.dumps(session.state()).encode(), "application/json")
            else:
                self._send(404, b"Not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/save":
                self._send(404, b"Not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length).decode())
                result = session.save(payload)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self._send(400, body, "application/json")
                return
            self._send(200, json.dumps(result).encode(), "application/json")

    return ThreadingHTTPServer((host, port), Handler)


def serve(session: AlignSession, host: str = "0.0.0.0", port: int = 47321) -> None:
    server = make_server(session, host, port)
    print(f"Line up the cases at http://127.0.0.1:{port}", flush=True)
    print("Drag the corners, set the rows and columns, then Save crops.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()
