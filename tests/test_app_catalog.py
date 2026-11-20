"""Tests for the PD-F1009 catalog importer and API."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server.db import connect, init_schema
from app.server.import_csv import import_catalog
from app.server.main import create_app

REPO = Path(__file__).resolve().parents[1]


def _write_tiny_jpeg(path: Path) -> None:
    # Minimal valid-enough JPEG payload for blob storage tests.
    path.write_bytes(
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


@pytest.fixture()
def sample_tree(tmp_path: Path):
    crop1 = tmp_path / "crops" / "01.jpg"
    crop2 = tmp_path / "crops" / "02.jpg"
    crop1.parent.mkdir(parents=True)
    _write_tiny_jpeg(crop1)
    _write_tiny_jpeg(crop2)

    csv_path = tmp_path / "cd_index.csv"
    rows = [
        {
            "Input Image": "image1.jpg",
            "Index": "3",
            "CD Artist": "Madonna",
            "Album": "Bedtime Stories",
            "CD Number": "1",
            "Track Artist": "Madonna",
            "Track Title": "Survival",
            "Cover Crop": "crops/01.jpg",
            "Discogs Release ID": "397566",
            "Needs Review": "no",
        },
        {
            "Input Image": "image1.jpg",
            "Index": "3",
            "CD Artist": "Madonna",
            "Album": "Bedtime Stories",
            "CD Number": "1",
            "Track Artist": "Madonna",
            "Track Title": "Secret",
            "Cover Crop": "crops/01.jpg",
            "Discogs Release ID": "397566",
            "Needs Review": "no",
        },
        {
            "Input Image": "image1.jpg",
            "Index": "3",
            "CD Artist": "Madonna",
            "Album": "Bedtime Stories",
            "CD Number": "2",
            "Track Artist": "Madonna",
            "Track Title": "Survival (Quiet Storm Demo Remix)",
            "Cover Crop": "crops/01.jpg",
            "Discogs Release ID": "397566",
            "Needs Review": "no",
        },
        {
            "Input Image": "image2.jpg",
            "Index": "1",
            "CD Artist": "Various",
            "Album": "Solo",
            "CD Number": "1",
            "Track Artist": "Artist",
            "Track Title": "Song",
            "Cover Crop": "crops/02.jpg",
            "Discogs Release ID": "",
            "Needs Review": "yes",
        },
        # blank junk rows
        {
            "Input Image": "",
            "Index": "",
            "CD Artist": "",
            "Album": "",
            "CD Number": "",
            "Track Artist": "",
            "Track Title": "",
            "Cover Crop": "",
            "Discogs Release ID": "",
            "Needs Review": "",
        },
        {
            "Input Image": "",
            "Index": "",
            "CD Artist": "",
            "Album": "",
            "CD Number": "",
            "Track Artist": "",
            "Track Title": "0",
            "Cover Crop": "",
            "Discogs Release ID": "",
            "Needs Review": "",
        },
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Input Image",
                "Index",
                "CD Artist",
                "Album",
                "CD Number",
                "Track Artist",
                "Track Title",
                "Cover Crop",
                "Discogs Release ID",
                "Needs Review",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    return {"root": tmp_path, "csv": csv_path}


def test_import_groups_multi_disc_and_stores_cover(sample_tree):
    db_path = sample_tree["root"] / "catalog.sqlite"
    conn = connect(db_path)
    stats = import_catalog(
        conn,
        csv_path=sample_tree["csv"],
        repo_root=sample_tree["root"],
    )
    assert stats["albums"] == 2
    assert stats["discs"] == 3
    assert stats["tracks"] == 4
    assert stats["covers"] == 2

    madonna = conn.execute(
        "SELECT id, needs_review, discogs_release_id FROM albums WHERE title = ?",
        ("Bedtime Stories",),
    ).fetchone()
    assert madonna is not None
    assert madonna["needs_review"] == 0
    assert madonna["discogs_release_id"] == 397566
    cover = conn.execute(
        "SELECT cover_jpeg FROM albums WHERE id = ?",
        (madonna["id"],),
    ).fetchone()["cover_jpeg"]
    assert cover is not None and cover[:2] == b"\xff\xd8"

    solo = conn.execute(
        "SELECT discogs_release_id FROM albums WHERE title = ?",
        ("Solo",),
    ).fetchone()
    assert solo["discogs_release_id"] is None

    discs = conn.execute(
        "SELECT disc_number FROM discs WHERE album_id = ? ORDER BY disc_number",
        (madonna["id"],),
    ).fetchall()
    assert [d["disc_number"] for d in discs] == [1, 2]

    tracks_disc1 = conn.execute(
        """
        SELECT track_number, title FROM tracks
        WHERE disc_id = (
            SELECT id FROM discs WHERE album_id = ? AND disc_number = 1
        )
        ORDER BY track_number
        """,
        (madonna["id"],),
    ).fetchall()
    assert [t["title"] for t in tracks_disc1] == ["Survival", "Secret"]
    assert [t["track_number"] for t in tracks_disc1] == [1, 2]
    conn.close()


def test_reimport_preserves_slots(sample_tree):
    db_path = sample_tree["root"] / "catalog.sqlite"
    conn = connect(db_path)
    import_catalog(conn, csv_path=sample_tree["csv"], repo_root=sample_tree["root"])
    disc = conn.execute(
        """
        SELECT id FROM discs
        WHERE source_image = 'image1.jpg' AND photo_index = 3 AND disc_number = 2
        """
    ).fetchone()
    conn.execute(
        "UPDATE slots SET disc_id = ? WHERE slot_number = 42",
        (disc["id"],),
    )
    conn.commit()

    import_catalog(conn, csv_path=sample_tree["csv"], repo_root=sample_tree["root"])
    slot = conn.execute(
        "SELECT disc_id FROM slots WHERE slot_number = 42"
    ).fetchone()
    assert slot["disc_id"] is not None
    remapped = conn.execute(
        "SELECT source_image, photo_index, disc_number FROM discs WHERE id = ?",
        (slot["disc_id"],),
    ).fetchone()
    assert (
        remapped["source_image"],
        remapped["photo_index"],
        remapped["disc_number"],
    ) == ("image1.jpg", 3, 2)
    conn.close()


@pytest.fixture()
def client(sample_tree, tmp_path):
    db_path = tmp_path / "api.sqlite"
    app = create_app(
        db_path=db_path,
        csv_path=sample_tree["csv"],
        repo_root=sample_tree["root"],
        import_on_startup=True,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_no_startup_import_and_csv_upload(sample_tree, tmp_path):
    db_path = tmp_path / "empty.sqlite"
    app = create_app(
        db_path=db_path,
        csv_path=sample_tree["csv"],
        repo_root=sample_tree["root"],
        import_on_startup=False,
    )
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").json()["counts"]["albums"] == 0
        defaults = test_client.get("/api/catalog/defaults").json()
        assert defaults["crops_base"]

        missing_base = test_client.post(
            "/api/catalog/import",
            files={
                "file": (
                    "cd_index.csv",
                    sample_tree["csv"].read_bytes(),
                    "text/csv",
                )
            },
            data={"crops_base": str(tmp_path / "does-not-exist")},
        )
        assert missing_base.status_code == 400

        uploaded = test_client.post(
            "/api/catalog/import",
            files={
                "file": (
                    "cd_index.csv",
                    sample_tree["csv"].read_bytes(),
                    "text/csv",
                )
            },
            data={"crops_base": str(sample_tree["root"])},
        )
        assert uploaded.status_code == 200
        body = uploaded.json()
        assert body["ok"] is True
        assert body["stats"]["albums"] == 2
        assert body["stats"]["covers"] == 2
        assert test_client.get("/api/health").json()["counts"]["albums"] == 2


def test_slot_assignment_and_move(client):
    albums = client.get("/api/albums").json()["albums"]
    madonna = next(a for a in albums if a["title"] == "Bedtime Stories")
    disc1 = madonna["discs"][0]["id"]
    disc2 = madonna["discs"][1]["id"]

    r = client.put("/api/slots/10", json={"disc_id": disc1})
    assert r.status_code == 200

    # Move disc1 to another slot.
    r = client.put("/api/slots/11", json={"disc_id": disc1})
    assert r.status_code == 200
    slots = {s["slot_number"]: s["disc_id"] for s in client.get("/api/slots").json()["slots"]}
    assert slots[10] is None
    assert slots[11] == disc1

    # Occupy slot then conflict without replace.
    client.put("/api/slots/12", json={"disc_id": disc2})
    conflict = client.put("/api/slots/12", json={"disc_id": disc1, "replace": False})
    assert conflict.status_code == 409

    replaced = client.put("/api/slots/12", json={"disc_id": disc1, "replace": True})
    assert replaced.status_code == 200
    slots = {s["slot_number"]: s["disc_id"] for s in client.get("/api/slots").json()["slots"]}
    assert slots[12] == disc1
    assert disc2 not in slots.values()


def test_update_album_and_cover(client, sample_tree):
    albums = client.get("/api/albums").json()["albums"]
    madonna = next(a for a in albums if a["title"] == "Bedtime Stories")

    patched = client.patch(
        f"/api/albums/{madonna['id']}",
        json={
            "artist": "Madonna Louise",
            "title": "Bedtime Stories (Remaster)",
            "discogs_release_id": 12345,
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["artist"] == "Madonna Louise"
    assert body["title"] == "Bedtime Stories (Remaster)"
    assert body["discogs_release_id"] == 12345

    cleared = client.patch(
        f"/api/albums/{madonna['id']}",
        json={"clear_discogs_release_id": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["discogs_release_id"] is None

    jpeg = (sample_tree["root"] / "crops" / "02.jpg").read_bytes()
    uploaded = client.put(
        f"/api/covers/{madonna['id']}",
        files={"file": ("new-cover.jpg", jpeg, "image/jpeg")},
    )
    assert uploaded.status_code == 200
    cover = client.get(f"/api/covers/{madonna['id']}")
    assert cover.status_code == 200
    assert cover.content == jpeg


def test_play_requires_loaded_disc_and_logs(client):
    albums = client.get("/api/albums").json()["albums"]
    madonna = next(a for a in albums if a["title"] == "Bedtime Stories")
    disc = madonna["discs"][0]
    track_album = client.get(f"/api/albums/{madonna['id']}").json()
    track_id = track_album["discs"][0]["tracks"][1]["id"]  # Secret

    denied = client.post(
        "/api/player/play",
        json={"disc_id": disc["id"], "track_id": track_id},
    )
    assert denied.status_code == 400

    client.put("/api/slots/7", json={"disc_id": disc["id"]})
    played = client.post(
        "/api/player/play",
        json={"disc_id": disc["id"], "track_id": track_id},
    )
    assert played.status_code == 200
    body = played.json()
    assert body["ok"] is True
    assert body["slot"] == 7
    assert body["track_number"] == 2
    assert "Secret" in body["message"]

    now = client.get("/api/player/now").json()["now"]
    assert now is not None
    assert now["slot"] == 7
    assert now["track_number"] == 2

    disc_only = client.post(
        "/api/player/play",
        json={"disc_id": disc["id"], "track_id": None},
    )
    assert disc_only.status_code == 200
    assert disc_only.json()["track_number"] is None

    library = client.get("/api/player/library").json()["discs"]
    assert any(d["id"] == disc["id"] for d in library)

    cover = client.get(f"/api/covers/{madonna['id']}")
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/jpeg"
