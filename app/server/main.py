"""FastAPI application for the PD-F1009 catalog and player."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import (
    APP_DIR,
    DEFAULT_CSV_PATH,
    DEFAULT_DB_PATH,
    REPO_ROOT,
    SETTING_ARDUINO_BASE_URL,
    SLOT_MAX,
    SLOT_MIN,
    connect,
    get_setting,
    init_schema,
    set_setting,
)
from .discogs import DiscogsError, apply_release_to_album
from .import_csv import ensure_imported, import_catalog
from .player import PlayerService, normalize_arduino_base_url


def resolve_db_path() -> Path:
    raw = os.environ.get("DISC_DETECT_DB")
    return Path(raw) if raw else DEFAULT_DB_PATH


def resolve_web_dist() -> Path | None:
    raw = os.environ.get("DISC_DETECT_WEB_DIST")
    candidate = Path(raw) if raw else APP_DIR / "web" / "dist"
    if candidate.is_dir() and (candidate / "index.html").is_file():
        return candidate
    return None


class SlotAssignment(BaseModel):
    disc_id: int | None = None
    replace: bool = False


class PlayRequest(BaseModel):
    disc_id: int = Field(..., ge=1)
    track_id: int | None = None


class PlayerControlRequest(BaseModel):
    action: str


class ArduinoSettings(BaseModel):
    base_url: str = ""


class AlbumUpdate(BaseModel):
    artist: str | None = None
    title: str | None = None
    discogs_release_id: int | None = Field(default=None)
    clear_discogs_release_id: bool = False


class DiscogsSyncRequest(BaseModel):
    release_id: int | None = Field(default=None, ge=1)
    fetch_cover: bool = True


def create_app(
    db_path: Path | str = DEFAULT_DB_PATH,
    csv_path: Path | str = DEFAULT_CSV_PATH,
    repo_root: Path | str = REPO_ROOT,
    import_on_startup: bool = False,
) -> FastAPI:
    state: dict[str, Any] = {"csv_path": Path(csv_path), "repo_root": Path(repo_root)}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(db_path)
        init_schema(conn)
        if import_on_startup:
            ensure_imported(
                conn,
                csv_path=state["csv_path"],
                repo_root=state["repo_root"],
            )
        state["conn"] = conn
        state["transport"] = PlayerService(conn)
        yield
        conn.close()

    app = FastAPI(title="PD-F1009 Catalog", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def db() -> sqlite3.Connection:
        return state["conn"]

    def transport() -> PlayerService:
        return state["transport"]

    def album_dict(row: sqlite3.Row, discs: list[dict] | None = None) -> dict:
        return {
            "id": row["id"],
            "artist": row["artist"],
            "title": row["title"],
            "source_image": row["source_image"],
            "photo_index": row["photo_index"],
            "needs_review": bool(row["needs_review"]),
            "discogs_release_id": (
                row["discogs_release_id"]
                if "discogs_release_id" in row.keys()
                else None
            ),
            "cover_url": f"/api/covers/{row['id']}",
            "has_cover": row["has_cover"] == 1 if "has_cover" in row.keys() else None,
            "discs": discs or [],
        }

    def disc_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "album_id": row["album_id"],
            "disc_number": row["disc_number"],
            "slot_number": row["slot_number"],
            "track_count": row["track_count"] if "track_count" in row.keys() else None,
            "artist": row["artist"] if "artist" in row.keys() else None,
            "album_title": row["album_title"] if "album_title" in row.keys() else None,
            "cover_url": (
                f"/api/covers/{row['album_id']}"
                if "album_id" in row.keys()
                else None
            ),
            "disc_count_in_set": (
                row["disc_count_in_set"]
                if "disc_count_in_set" in row.keys()
                else None
            ),
        }

    @app.get("/api/health")
    def health() -> dict:
        counts = {
            "albums": db().execute("SELECT COUNT(*) AS n FROM albums").fetchone()["n"],
            "discs": db().execute("SELECT COUNT(*) AS n FROM discs").fetchone()["n"],
            "tracks": db().execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"],
            "loaded": db()
            .execute("SELECT COUNT(*) AS n FROM slots WHERE disc_id IS NOT NULL")
            .fetchone()["n"],
        }
        return {"ok": True, "counts": counts}

    @app.get("/api/catalog/defaults")
    def catalog_defaults() -> dict:
        return {
            "crops_base": str(state["repo_root"].resolve()),
        }

    @app.get("/api/settings/arduino")
    def get_arduino_settings() -> dict:
        return {
            "base_url": get_setting(db(), SETTING_ARDUINO_BASE_URL, ""),
        }

    @app.put("/api/settings/arduino")
    def put_arduino_settings(body: ArduinoSettings) -> dict:
        try:
            normalized = normalize_arduino_base_url(body.base_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_setting(db(), SETTING_ARDUINO_BASE_URL, normalized)
        return {"base_url": normalized}

    @app.get("/api/settings/arduino/test")
    def test_arduino_settings() -> dict:
        try:
            return transport().health_check()
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/catalog/import")
    async def import_catalog_csv(
        file: UploadFile = File(...),
        crops_base: str = Form(...),
    ) -> dict:
        name = (file.filename or "").lower()
        content_type = (file.content_type or "").lower()
        if not (
            name.endswith(".csv")
            or content_type in {"text/csv", "application/csv", "application/vnd.ms-excel"}
        ):
            raise HTTPException(status_code=400, detail="Upload a CSV file")

        crops_root = Path(crops_base.strip()).expanduser()
        if not crops_base.strip():
            raise HTTPException(
                status_code=400,
                detail="Crop base folder is required",
            )
        if not crops_root.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Crop base folder not found: {crops_root}",
            )

        raw = await file.read()
        if not raw.strip():
            raise HTTPException(status_code=400, detail="CSV file is empty")

        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".csv",
            delete=False,
        ) as handle:
            handle.write(raw)
            temp_path = Path(handle.name)

        try:
            stats = import_catalog(
                db(),
                csv_path=temp_path,
                repo_root=state["repo_root"],
                crops_base=crops_root.resolve(),
            )
        except Exception as exc:  # noqa: BLE001 - surface parse errors to client
            raise HTTPException(
                status_code=400,
                detail=f"Failed to import CSV: {exc}",
            ) from exc
        finally:
            temp_path.unlink(missing_ok=True)

        return {
            "ok": True,
            "filename": file.filename,
            "crops_base": str(crops_root.resolve()),
            "stats": stats,
        }

    @app.get("/api/albums")
    def list_albums(q: str | None = Query(None)) -> dict:
        params: list[Any] = []
        where = ""
        if q and q.strip():
            like = f"%{q.strip()}%"
            where = """
                WHERE a.artist LIKE ? OR a.title LIKE ?
                   OR EXISTS (
                        SELECT 1 FROM tracks t
                        JOIN discs d2 ON d2.id = t.disc_id
                        WHERE d2.album_id = a.id
                          AND (t.title LIKE ? OR t.artist LIKE ?)
                   )
            """
            params = [like, like, like, like]

        albums = db().execute(
            f"""
            SELECT a.id, a.artist, a.title, a.source_image, a.photo_index,
                   a.needs_review, a.discogs_release_id,
                   CASE WHEN a.cover_jpeg IS NULL THEN 0 ELSE 1 END AS has_cover
            FROM albums a
            {where}
            ORDER BY a.artist COLLATE NOCASE, a.title COLLATE NOCASE, a.photo_index
            """,
            params,
        ).fetchall()

        discs = db().execute(
            """
            SELECT d.id, d.album_id, d.disc_number, s.slot_number,
                   (SELECT COUNT(*) FROM tracks t WHERE t.disc_id = d.id) AS track_count,
                   (SELECT COUNT(*) FROM discs d3 WHERE d3.album_id = d.album_id)
                       AS disc_count_in_set
            FROM discs d
            LEFT JOIN slots s ON s.disc_id = d.id
            ORDER BY d.album_id, d.disc_number
            """
        ).fetchall()
        by_album: dict[int, list[dict]] = {}
        for disc in discs:
            by_album.setdefault(disc["album_id"], []).append(disc_dict(disc))

        return {
            "albums": [
                album_dict(album, by_album.get(album["id"], [])) for album in albums
            ]
        }

    @app.get("/api/albums/{album_id}")
    def get_album(album_id: int) -> dict:
        album = db().execute(
            """
            SELECT a.id, a.artist, a.title, a.source_image, a.photo_index,
                   a.needs_review, a.discogs_release_id,
                   CASE WHEN a.cover_jpeg IS NULL THEN 0 ELSE 1 END AS has_cover
            FROM albums a WHERE a.id = ?
            """,
            (album_id,),
        ).fetchone()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        discs = db().execute(
            """
            SELECT d.id, d.album_id, d.disc_number, s.slot_number,
                   (SELECT COUNT(*) FROM tracks t WHERE t.disc_id = d.id) AS track_count,
                   (SELECT COUNT(*) FROM discs d3 WHERE d3.album_id = d.album_id)
                       AS disc_count_in_set
            FROM discs d
            LEFT JOIN slots s ON s.disc_id = d.id
            WHERE d.album_id = ?
            ORDER BY d.disc_number
            """,
            (album_id,),
        ).fetchall()
        disc_payload = []
        for disc in discs:
            tracks = db().execute(
                """
                SELECT id, track_number, artist, title
                FROM tracks WHERE disc_id = ? ORDER BY track_number
                """,
                (disc["id"],),
            ).fetchall()
            item = disc_dict(disc)
            item["tracks"] = [dict(t) for t in tracks]
            disc_payload.append(item)
        return album_dict(album, disc_payload)

    @app.patch("/api/albums/{album_id}")
    def update_album(album_id: int, body: AlbumUpdate) -> dict:
        row = db().execute(
            "SELECT id FROM albums WHERE id = ?",
            (album_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Album not found")

        updates: list[str] = []
        params: list[Any] = []
        if body.artist is not None:
            artist = body.artist.strip()
            if not artist:
                raise HTTPException(status_code=400, detail="Artist cannot be empty")
            updates.append("artist = ?")
            params.append(artist)
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(status_code=400, detail="Album title cannot be empty")
            updates.append("title = ?")
            params.append(title)
        if body.clear_discogs_release_id:
            updates.append("discogs_release_id = NULL")
        elif body.discogs_release_id is not None:
            if body.discogs_release_id < 1:
                raise HTTPException(
                    status_code=400,
                    detail="Discogs release id must be a positive integer",
                )
            updates.append("discogs_release_id = ?")
            params.append(body.discogs_release_id)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(album_id)
        db().execute(
            f"UPDATE albums SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        db().commit()
        return get_album(album_id)

    @app.post("/api/albums/{album_id}/discogs-sync")
    def sync_album_from_discogs(
        album_id: int,
        body: DiscogsSyncRequest = DiscogsSyncRequest(),
    ) -> dict:
        row = db().execute(
            "SELECT id, discogs_release_id FROM albums WHERE id = ?",
            (album_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Album not found")

        release_id = body.release_id
        if release_id is None:
            release_id = row["discogs_release_id"]
        if release_id is None:
            raise HTTPException(
                status_code=400,
                detail="Discogs release id is required",
            )

        try:
            sync = apply_release_to_album(
                db(),
                album_id,
                int(release_id),
                fetch_cover=body.fetch_cover,
            )
        except DiscogsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        album = get_album(album_id)
        return {"album": album, "sync": sync}

    @app.put("/api/covers/{album_id}")
    async def upload_cover(
        album_id: int,
        file: UploadFile = File(...),
    ) -> dict:
        row = db().execute(
            "SELECT id FROM albums WHERE id = ?",
            (album_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Album not found")

        content_type = (file.content_type or "").lower()
        name = (file.filename or "").lower()
        if content_type not in {"image/jpeg", "image/jpg", "image/pjpeg"} and not (
            name.endswith(".jpg") or name.endswith(".jpeg")
        ):
            raise HTTPException(
                status_code=400,
                detail="Cover must be a JPEG image",
            )

        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty image upload")
        if data[:2] != b"\xff\xd8":
            raise HTTPException(status_code=400, detail="Cover must be a JPEG image")
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Cover image is too large")

        db().execute(
            "UPDATE albums SET cover_jpeg = ?, cover_path = ? WHERE id = ?",
            (data, f"upload:{file.filename or 'cover.jpg'}", album_id),
        )
        db().commit()
        return get_album(album_id)

    @app.get("/api/covers/{album_id}")
    def get_cover(album_id: int) -> Response:
        row = db().execute(
            "SELECT cover_jpeg FROM albums WHERE id = ?",
            (album_id,),
        ).fetchone()
        data = row["cover_jpeg"] if row else None
        if not data or len(data) < 4 or data[:2] != b"\xff\xd8":
            raise HTTPException(status_code=404, detail="Cover not found")
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Length": str(len(data)),
            },
        )

    @app.get("/api/slots")
    def list_slots() -> dict:
        rows = db().execute(
            """
            SELECT s.slot_number, s.disc_id,
                   d.disc_number, d.album_id,
                   a.artist, a.title AS album_title,
                   CASE WHEN a.cover_jpeg IS NULL THEN 0 ELSE 1 END AS has_cover
            FROM slots s
            LEFT JOIN discs d ON d.id = s.disc_id
            LEFT JOIN albums a ON a.id = d.album_id
            ORDER BY s.slot_number
            """
        ).fetchall()
        slots = []
        for row in rows:
            slots.append(
                {
                    "slot_number": row["slot_number"],
                    "disc_id": row["disc_id"],
                    "disc_number": row["disc_number"],
                    "album_id": row["album_id"],
                    "artist": row["artist"],
                    "album_title": row["album_title"],
                    "cover_url": (
                        f"/api/covers/{row['album_id']}" if row["album_id"] else None
                    ),
                    "has_cover": bool(row["has_cover"]) if row["album_id"] else False,
                }
            )
        return {"slots": slots, "slot_min": SLOT_MIN, "slot_max": SLOT_MAX}

    @app.put("/api/slots/{slot_number}")
    def assign_slot(slot_number: int, body: SlotAssignment) -> dict:
        if slot_number < SLOT_MIN or slot_number > SLOT_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"Slot must be between {SLOT_MIN} and {SLOT_MAX}",
            )

        if body.disc_id is None:
            db().execute(
                "UPDATE slots SET disc_id = NULL WHERE slot_number = ?",
                (slot_number,),
            )
            db().commit()
            return {"slot_number": slot_number, "disc_id": None}

        disc = db().execute(
            "SELECT id FROM discs WHERE id = ?",
            (body.disc_id,),
        ).fetchone()
        if not disc:
            raise HTTPException(status_code=404, detail="Disc not found")

        current = db().execute(
            "SELECT disc_id FROM slots WHERE slot_number = ?",
            (slot_number,),
        ).fetchone()
        if (
            current
            and current["disc_id"] is not None
            and current["disc_id"] != body.disc_id
            and not body.replace
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Slot is occupied",
                    "slot_number": slot_number,
                    "disc_id": current["disc_id"],
                },
            )

        # Move: clear any previous slot for this disc.
        db().execute(
            "UPDATE slots SET disc_id = NULL WHERE disc_id = ?",
            (body.disc_id,),
        )
        db().execute(
            "UPDATE slots SET disc_id = ? WHERE slot_number = ?",
            (body.disc_id, slot_number),
        )
        db().commit()
        return {"slot_number": slot_number, "disc_id": body.disc_id}

    @app.get("/api/player/library")
    def player_library(q: str | None = Query(None)) -> dict:
        params: list[Any] = []
        where = "WHERE s.disc_id IS NOT NULL"
        if q and q.strip():
            like = f"%{q.strip()}%"
            where += """
                AND (
                    a.artist LIKE ? OR a.title LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM tracks t
                        WHERE t.disc_id = d.id
                          AND (t.title LIKE ? OR t.artist LIKE ?)
                    )
                )
            """
            params = [like, like, like, like]

        rows = db().execute(
            f"""
            SELECT d.id, d.album_id, d.disc_number, s.slot_number,
                   a.artist, a.title AS album_title,
                   (SELECT COUNT(*) FROM tracks t WHERE t.disc_id = d.id) AS track_count,
                   (SELECT COUNT(*) FROM discs d3 WHERE d3.album_id = d.album_id)
                       AS disc_count_in_set
            FROM slots s
            JOIN discs d ON d.id = s.disc_id
            JOIN albums a ON a.id = d.album_id
            {where}
            ORDER BY s.slot_number
            """,
            params,
        ).fetchall()

        discs = []
        for row in rows:
            tracks = db().execute(
                """
                SELECT id, track_number, artist, title
                FROM tracks WHERE disc_id = ? ORDER BY track_number
                """,
                (row["id"],),
            ).fetchall()
            item = disc_dict(row)
            item["tracks"] = [dict(t) for t in tracks]
            discs.append(item)
        return {"discs": discs}

    @app.post("/api/player/play")
    def play(body: PlayRequest) -> dict:
        slot_row = db().execute(
            """
            SELECT s.slot_number, d.id AS disc_id, d.album_id, d.disc_number,
                   a.artist, a.title AS album_title
            FROM slots s
            JOIN discs d ON d.id = s.disc_id
            JOIN albums a ON a.id = d.album_id
            WHERE s.disc_id = ?
            """,
            (body.disc_id,),
        ).fetchone()
        if not slot_row:
            raise HTTPException(
                status_code=400,
                detail="Disc is not loaded in any slot",
            )

        track_id = None
        track_number = None
        track_title = None
        if body.track_id is not None:
            track = db().execute(
                """
                SELECT id, track_number, title FROM tracks
                WHERE id = ? AND disc_id = ?
                """,
                (body.track_id, body.disc_id),
            ).fetchone()
            if not track:
                raise HTTPException(
                    status_code=400,
                    detail="Track does not belong to that disc",
                )
            track_id = track["id"]
            track_number = track["track_number"]
            track_title = track["title"]

        try:
            result = transport().play_loaded(
                slot=slot_row["slot_number"],
                disc_id=body.disc_id,
                track_id=track_id,
                track_number=track_number,
                track_title=track_title,
                album_title=slot_row["album_title"],
                artist=slot_row["artist"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "ok": result.ok,
            "command": result.command,
            "slot": result.slot,
            "track_number": result.track_number,
            "message": result.message,
            "payload": result.payload,
            "disc_id": body.disc_id,
            "track_id": track_id,
            "artist": slot_row["artist"],
            "album_title": slot_row["album_title"],
            "track_title": track_title,
            "cover_url": f"/api/covers/{slot_row['album_id']}",
        }

    @app.post("/api/player/control")
    def player_control(body: PlayerControlRequest) -> dict:
        action = (body.action or "").strip().lower()
        try:
            if action == "pause":
                result = transport().pause()
            elif action == "stop":
                result = transport().stop()
            elif action == "next":
                result = transport().next_track()
            elif action == "prev":
                result = transport().prev_track()
            else:
                raise HTTPException(
                    status_code=400,
                    detail="action must be pause, stop, next, or prev",
                )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "ok": result.ok,
            "command": result.command,
            "message": result.message,
            "payload": result.payload,
        }

    @app.get("/api/player/now")
    def now_playing() -> dict:
        row = db().execute(
            """
            SELECT id, slot_number, disc_id, track_id, track_number,
                   command, payload, created_at
            FROM play_log
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return {"now": None}
        payload = json.loads(row["payload"])
        album_id = None
        artist = payload.get("artist")
        album_title = payload.get("album")
        track_title = payload.get("track_title")
        cover_url = None
        slot = row["slot_number"]
        if slot is not None and slot < 0:
            slot = payload.get("slot")
        if row["disc_id"]:
            disc = db().execute(
                "SELECT album_id FROM discs WHERE id = ?",
                (row["disc_id"],),
            ).fetchone()
            if disc:
                album_id = disc["album_id"]
                cover_url = f"/api/covers/{album_id}"
                if not artist:
                    album = db().execute(
                        "SELECT artist, title FROM albums WHERE id = ?",
                        (album_id,),
                    ).fetchone()
                    if album:
                        artist = album["artist"]
                        album_title = album["title"]
        message = payload.get("message")
        if not message:
            if row["command"] in {"pause", "stop", "next", "prev"}:
                message = {
                    "pause": "Pause",
                    "stop": "Stop",
                    "next": "Next track",
                    "prev": "Previous track",
                }.get(row["command"], row["command"])
            else:
                message = (
                    f"Play: slot {slot}"
                    + (
                        f", track {row['track_number']}"
                        if row["track_number"] is not None
                        else ""
                    )
                    + (
                        f" — {track_title}"
                        if track_title
                        else (
                            f" — {artist} / {album_title}"
                            if artist and album_title
                            else ""
                        )
                    )
                )
        return {
            "now": {
                "id": row["id"],
                "slot": slot,
                "disc_id": row["disc_id"],
                "track_id": row["track_id"],
                "track_number": row["track_number"],
                "command": row["command"],
                "message": message,
                "artist": artist,
                "album_title": album_title,
                "track_title": track_title,
                "cover_url": cover_url,
                "created_at": row["created_at"],
                "payload": payload,
            }
        }

    web_dist = resolve_web_dist()
    if web_dist is not None:
        assets_dir = web_dist / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=assets_dir),
                name="web-assets",
            )

        @app.get("/{full_path:path}")
        def serve_spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/") or full_path == "api":
                raise HTTPException(status_code=404, detail="Not found")
            candidate = web_dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")

    return app


app = create_app(db_path=resolve_db_path())


def main() -> None:
    import uvicorn

    host = os.environ.get("DISC_DETECT_HOST", "127.0.0.1")
    port = int(os.environ.get("DISC_DETECT_PORT", "8000"))
    reload = os.environ.get("DISC_DETECT_RELOAD", "1") == "1"
    uvicorn.run(
        "app.server.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
