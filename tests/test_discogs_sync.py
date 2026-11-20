"""Discogs album sync API tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.server.main import create_app

TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
    "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
    "1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d"
    "0d1832211c2132323232323232323232323232323232323232323232323232323232"
    "323232323232323232323232323232323232323232ffc00011080001000103011100"
    "021101031101ffc40014000100000000000000000000000000000000ffc400141001"
    "00000000000000000000000000000000ffda000c0301000210031000003f00bf80ffd9"
)


@pytest.fixture()
def sample_csv(tmp_path: Path):
    crop = tmp_path / "crops" / "01.jpg"
    crop.parent.mkdir()
    crop.write_bytes(TINY_JPEG)
    csv_path = tmp_path / "cd_index.csv"
    csv_path.write_text(
        "Input Image,Index,CD Artist,Album,CD Number,Track Artist,Track Title,"
        "Cover Crop,Discogs Release ID,Needs Review\n"
        "image1.jpg,1,Wrong Artist,Wrong Title,1,Wrong Artist,Old Song,"
        "crops/01.jpg,,yes\n"
        "image1.jpg,1,Wrong Artist,Wrong Title,2,Wrong Artist,Old Song 2,"
        "crops/01.jpg,,yes\n",
        encoding="utf-8",
    )
    return {"root": tmp_path, "csv": csv_path}


@pytest.fixture()
def client(sample_csv, tmp_path):
    app = create_app(
        db_path=tmp_path / "discogs.sqlite",
        csv_path=sample_csv["csv"],
        repo_root=sample_csv["root"],
        import_on_startup=True,
    )
    with TestClient(app) as test_client:
        yield test_client


def _release_payload():
    return {
        "id": 292433,
        "title": "True Blue",
        "artists": [{"name": "Madonna", "join": ""}],
        "tracklist": [
            {"position": "1", "type_": "track", "title": "Papa Don't Preach"},
            {"position": "2", "type_": "track", "title": "Open Your Heart"},
            {
                "position": "3",
                "type_": "track",
                "title": "White Heat",
                "extraartists": [{"name": "Someone", "role": "Featuring"}],
            },
            {"position": "1-1", "type_": "heading", "title": "Bonus"},
        ],
        "images": [
            {
                "type": "primary",
                "uri": "https://example.test/cover.jpg",
            }
        ],
    }


def test_discogs_sync_updates_metadata_tracks_cover_and_keeps_slots(client):
    albums = client.get("/api/albums").json()["albums"]
    album = albums[0]
    disc1 = next(d for d in album["discs"] if d["disc_number"] == 1)
    client.put("/api/slots/12", json={"disc_id": disc1["id"]})

    release = _release_payload()

    def fake_get(url, headers=None, **kwargs):
        response = MagicMock()
        response.status_code = 200
        if "api.discogs.com/releases/292433" in url:
            response.json.return_value = release
            response.text = ""
            return response
        if url.endswith("cover.jpg"):
            response.content = TINY_JPEG
            return response
        response.status_code = 404
        response.text = "missing"
        response.content = b""
        response.json.return_value = {}
        return response

    mock_client = MagicMock()
    mock_client.get.side_effect = fake_get
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("app.server.discogs.httpx.Client", return_value=mock_client):
        # Client() is used as context? Our code uses Client() without context manager
        # and closes manually. So return_value should be the instance.
        mock_client.close = MagicMock()
        result = client.post(
            f"/api/albums/{album['id']}/discogs-sync",
            json={"release_id": 292433, "fetch_cover": True},
        )

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["sync"]["artist"] == "Madonna"
    assert body["sync"]["title"] == "True Blue"
    assert body["sync"]["track_count"] == 3
    assert body["sync"]["cover_updated"] is True
    assert body["sync"]["slots_restored"] == 1

    updated = body["album"]
    assert updated["artist"] == "Madonna"
    assert updated["title"] == "True Blue"
    assert updated["discogs_release_id"] == 292433
    assert updated["needs_review"] is False
    assert len(updated["discs"]) == 1
    titles = [t["title"] for t in updated["discs"][0]["tracks"]]
    assert titles == [
        "Papa Don't Preach",
        "Open Your Heart",
        "White Heat",
    ]
    assert "feat. Someone" in updated["discs"][0]["tracks"][2]["artist"]

    slots = client.get("/api/slots").json()["slots"]
    slot12 = next(s for s in slots if s["slot_number"] == 12)
    assert slot12["disc_id"] == updated["discs"][0]["id"]
    assert slot12["album_title"] == "True Blue"

    cover = client.get(f"/api/covers/{album['id']}")
    assert cover.status_code == 200
    assert cover.content[:2] == b"\xff\xd8"


def test_discogs_sync_requires_release_id(client):
    album_id = client.get("/api/albums").json()["albums"][0]["id"]
    missing = client.post(f"/api/albums/{album_id}/discogs-sync", json={})
    assert missing.status_code == 400
    assert "release id" in missing.json()["detail"].lower()


def test_discogs_sync_not_found(client):
    album_id = client.get("/api/albums").json()["albums"][0]["id"]

    response = MagicMock()
    response.status_code = 404
    response.text = "not found"
    response.json.return_value = {}

    mock_client = MagicMock()
    mock_client.get.return_value = response
    mock_client.close = MagicMock()

    with patch("app.server.discogs.httpx.Client", return_value=mock_client):
        result = client.post(
            f"/api/albums/{album_id}/discogs-sync",
            json={"release_id": 99999999},
        )
    assert result.status_code == 400
    assert "not found" in result.json()["detail"].lower()
