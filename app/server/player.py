"""Player transport: local log fallback or Arduino REST IR bridge."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from .db import SETTING_ARDUINO_BASE_URL, get_setting


@dataclass
class PlayResult:
    ok: bool
    command: str
    slot: int | None
    track_number: int | None
    message: str
    payload: dict


class PlayerTransport(Protocol):
    def play_disc(self, slot: int) -> PlayResult: ...

    def play_track(self, slot: int, track: int) -> PlayResult: ...

    def pause(self) -> PlayResult: ...

    def stop(self) -> PlayResult: ...

    def next_track(self) -> PlayResult: ...

    def prev_track(self) -> PlayResult: ...

    def status(self) -> dict: ...


def normalize_arduino_base_url(raw: str) -> str:
    text = (raw or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Arduino URL must be an http(s) URL with a host")
    return text


class PlayerService:
    """Reads Arduino URL from settings on each call; logs every command locally."""

    def __init__(self, conn: sqlite3.Connection, timeout: float = 5.0) -> None:
        self.conn = conn
        self.timeout = timeout

    def _base_url(self) -> str:
        return get_setting(self.conn, SETTING_ARDUINO_BASE_URL, "")

    def play_disc(self, slot: int) -> PlayResult:
        return self._play(slot=slot, track_number=None)

    def play_track(self, slot: int, track: int) -> PlayResult:
        return self._play(slot=slot, track_number=track)

    def play_loaded(
        self,
        *,
        slot: int,
        disc_id: int,
        track_id: int | None,
        track_number: int | None,
        track_title: str | None,
        album_title: str,
        artist: str,
    ) -> PlayResult:
        if track_number is None:
            command = "play_disc"
            message = f"Play: slot {slot} — {artist} / {album_title}"
        else:
            command = "play_track"
            title = track_title or f"track {track_number}"
            message = f"Play: slot {slot}, track {track_number} — {title}"
        return self._dispatch(
            command=command,
            message=message,
            slot=slot,
            track_number=track_number,
            disc_id=disc_id,
            track_id=track_id,
            arduino_path="/play",
            arduino_body={"disc": slot, **({"track": track_number} if track_number is not None else {})},
            extra={
                "disc_id": disc_id,
                "track_id": track_id,
                "artist": artist,
                "album": album_title,
                "track_title": track_title,
            },
        )

    def pause(self) -> PlayResult:
        return self._control("pause", "Pause", "/pause")

    def stop(self) -> PlayResult:
        return self._control("stop", "Stop", "/stop")

    def next_track(self) -> PlayResult:
        return self._control("next", "Next track", "/next")

    def prev_track(self) -> PlayResult:
        return self._control("prev", "Previous track", "/prev")

    def status(self) -> dict:
        base = self._base_url()
        if not base:
            return {"ok": True, "mode": "local", "state": None}
        data = self._request_json("GET", f"{base}/status")
        return {"ok": True, "mode": "arduino", **data}

    def health_check(self) -> dict:
        base = self._base_url()
        if not base:
            return {"ok": False, "detail": "Arduino URL is not configured"}
        data = self._request_json("GET", f"{base}/health")
        return {"ok": True, "base_url": base, "response": data}

    def _play(self, *, slot: int, track_number: int | None) -> PlayResult:
        if track_number is None:
            command = "play_disc"
            message = f"Play: slot {slot}"
            body: dict[str, Any] = {"disc": slot}
        else:
            command = "play_track"
            message = f"Play: slot {slot}, track {track_number}"
            body = {"disc": slot, "track": track_number}
        return self._dispatch(
            command=command,
            message=message,
            slot=slot,
            track_number=track_number,
            arduino_path="/play",
            arduino_body=body,
        )

    def _control(self, command: str, message: str, path: str) -> PlayResult:
        return self._dispatch(
            command=command,
            message=message,
            slot=None,
            track_number=None,
            arduino_path=path,
            arduino_body={},
        )

    def _dispatch(
        self,
        *,
        command: str,
        message: str,
        slot: int | None,
        track_number: int | None,
        arduino_path: str,
        arduino_body: dict,
        disc_id: int | None = None,
        track_id: int | None = None,
        extra: dict | None = None,
    ) -> PlayResult:
        base = self._base_url()
        mode = "arduino" if base else "local"
        remote: dict | None = None
        if base:
            remote = self._request_json(
                "POST",
                f"{base}{arduino_path}",
                json_body=arduino_body,
            )
            if mode == "arduino" and not message.startswith(("Play:", "Pause", "Stop", "Next", "Previous")):
                pass
        else:
            # Local fallback keeps UI usable without hardware.
            if message.startswith("Play:"):
                message = message.replace("Play:", "Local play:", 1)
            else:
                message = f"Local {message.lower()}"

        payload = {
            "slot": slot,
            "track_number": track_number,
            "command": command,
            "mode": mode,
            "message": message,
            "arduino": remote,
            **(extra or {}),
        }
        self.conn.execute(
            """
            INSERT INTO play_log (
                slot_number, disc_id, track_id, track_number, command, payload
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                slot if slot is not None else -1,
                disc_id,
                track_id,
                track_number,
                command,
                json.dumps(payload),
            ),
        )
        self.conn.commit()
        return PlayResult(
            ok=True,
            command=command,
            slot=slot,
            track_number=track_number,
            message=message,
            payload=payload,
        )

    def _request_json(
        self,
        method: str,
        url: str,
        json_body: dict | None = None,
    ) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method, url, json=json_body)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Arduino request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise RuntimeError(
                f"Arduino returned {response.status_code}: {detail}"
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Arduino returned non-JSON response") from exc
        if not isinstance(data, dict):
            return {"data": data}
        return data


# Back-compat alias used by older imports/tests.
MockTransport = PlayerService
ArduinoTransport = PlayerService
