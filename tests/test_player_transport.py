"""Arduino / local player transport tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.server.main import create_app
from app.server.player import normalize_arduino_base_url


@pytest.fixture()
def sample_csv(tmp_path: Path):
    crop = tmp_path / "crops" / "01.jpg"
    crop.parent.mkdir()
    crop.write_bytes(
        bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
            "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
            "1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d"
            "0d1832211c2132323232323232323232323232323232323232323232323232323232"
            "323232323232323232323232323232323232323232ffc00011080001000103011100"
            "021101031101ffc40014000100000000000000000000000000000000ffc400141001"
            "00000000000000000000000000000000ffda000c0301000210031000003f00bf80ffd9"
        )
    )
    csv_path = tmp_path / "cd_index.csv"
    csv_path.write_text(
        "Input Image,Index,CD Artist,Album,CD Number,Track Artist,Track Title,"
        "Cover Crop,Discogs Release ID,Needs Review\n"
        "image1.jpg,1,Artist,Album,1,Artist,Song One,crops/01.jpg,1,no\n"
        "image1.jpg,1,Artist,Album,1,Artist,Song Two,crops/01.jpg,1,no\n",
        encoding="utf-8",
    )
    return {"root": tmp_path, "csv": csv_path}


@pytest.fixture()
def client(sample_csv, tmp_path):
    app = create_app(
        db_path=tmp_path / "player.sqlite",
        csv_path=sample_csv["csv"],
        repo_root=sample_csv["root"],
        import_on_startup=True,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_normalize_arduino_url():
    assert normalize_arduino_base_url(" http://192.168.1.50/ ") == "http://192.168.1.50"
    assert normalize_arduino_base_url("") == ""
    with pytest.raises(ValueError):
        normalize_arduino_base_url("ftp://bad")


def test_arduino_settings_round_trip(client):
    assert client.get("/api/settings/arduino").json()["base_url"] == ""
    bad = client.put("/api/settings/arduino", json={"base_url": "not-a-url"})
    assert bad.status_code == 400
    saved = client.put(
        "/api/settings/arduino",
        json={"base_url": "http://192.168.1.50/"},
    )
    assert saved.status_code == 200
    assert saved.json()["base_url"] == "http://192.168.1.50"
    assert client.get("/api/settings/arduino").json()["base_url"] == "http://192.168.1.50"
    cleared = client.put("/api/settings/arduino", json={"base_url": ""})
    assert cleared.json()["base_url"] == ""


def test_local_play_without_arduino_url(client):
    albums = client.get("/api/albums").json()["albums"]
    disc_id = albums[0]["discs"][0]["id"]
    client.put("/api/slots/5", json={"disc_id": disc_id})
    played = client.post("/api/player/play", json={"disc_id": disc_id, "track_id": None})
    assert played.status_code == 200
    assert "Local play" in played.json()["message"]
    now = client.get("/api/player/now").json()["now"]
    assert now["slot"] == 5
    assert now["payload"]["mode"] == "local"


def test_play_and_control_hit_arduino(client):
    albums = client.get("/api/albums").json()["albums"]
    disc = albums[0]["discs"][0]
    track_id = client.get(f"/api/albums/{albums[0]['id']}").json()["discs"][0]["tracks"][1]["id"]
    client.put("/api/slots/7", json={"disc_id": disc["id"]})
    client.put("/api/settings/arduino", json={"base_url": "http://arduino.test"})

    calls: list[tuple[str, str, dict | None]] = []

    class FakeResponse:
        def __init__(self, data=None, status=200):
            self.status_code = status
            self._data = data or {"ok": True}
            self.content = json.dumps(self._data).encode()
            self.text = self.content.decode()
            self.reason_phrase = "OK"

        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, json=None):
            calls.append((method, url, json))
            return FakeResponse({"ok": True, "echo": json})

    with patch("app.server.player.httpx.Client", FakeClient):
        played = client.post(
            "/api/player/play",
            json={"disc_id": disc["id"], "track_id": track_id},
        )
        assert played.status_code == 200
        assert played.json()["message"].startswith("Play:")
        assert ("POST", "http://arduino.test/play", {"disc": 7, "track": 2}) in calls

        paused = client.post("/api/player/control", json={"action": "pause"})
        assert paused.status_code == 200
        assert ("POST", "http://arduino.test/pause", {}) in calls

        nxt = client.post("/api/player/control", json={"action": "next"})
        assert nxt.status_code == 200
        assert ("POST", "http://arduino.test/next", {}) in calls

        health = client.get("/api/settings/arduino/test")
        assert health.status_code == 200
        assert ("GET", "http://arduino.test/health", None) in calls


def test_arduino_failure_surfaces_502(client):
    albums = client.get("/api/albums").json()["albums"]
    disc_id = albums[0]["discs"][0]["id"]
    client.put("/api/slots/3", json={"disc_id": disc_id})
    client.put("/api/settings/arduino", json={"base_url": "http://arduino.test"})

    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, *args, **kwargs):
            raise __import__("httpx").ConnectError("down")

    with patch("app.server.player.httpx.Client", BoomClient):
        failed = client.post("/api/player/play", json={"disc_id": disc_id})
        assert failed.status_code == 502
