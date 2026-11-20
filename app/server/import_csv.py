"""Import cd_index.csv into SQLite, preserving slot assignments on re-import."""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .db import DEFAULT_CSV_PATH, REPO_ROOT, init_schema


@dataclass
class _TrackRow:
    artist: str
    title: str


@dataclass
class _DiscBucket:
    disc_number: int
    tracks: list[_TrackRow] = field(default_factory=list)


@dataclass
class _AlbumBucket:
    source_image: str
    photo_index: int
    artist: str
    title: str
    needs_review: bool
    cover_path: str
    discogs_release_id: int | None = None
    discs: dict[int, _DiscBucket] = field(default_factory=dict)


def _truthy_review(value: str) -> bool:
    return value.strip().lower() in {"yes", "y", "true", "1"}


def _parse_int(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _cover_candidates(cover_path: str, crops_base: Path) -> list[Path]:
    """Resolve Cover Crop against the chosen crops base folder."""
    raw = Path(cover_path.strip())
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(crops_base / raw)
        # Allow pointing crops_base at the crops/ folder itself.
        candidates.append(crops_base / raw.name)
        if len(raw.parts) > 1:
            candidates.append(crops_base / Path(*raw.parts[-2:]))
    # De-dupe while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _read_cover(cover_path: str, crops_base: Path) -> bytes | None:
    if not cover_path.strip():
        return None
    for path in _cover_candidates(cover_path, crops_base):
        if path.is_file():
            return path.read_bytes()
    return None


def load_csv_buckets(
    csv_path: Path,
) -> dict[tuple[str, int], _AlbumBucket]:
    albums: dict[tuple[str, int], _AlbumBucket] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            source_image = (row.get("Input Image") or "").strip()
            photo_index = _parse_int(row.get("Index") or "")
            disc_number = _parse_int(row.get("CD Number") or "")
            track_title = (row.get("Track Title") or "").strip()
            if not source_image or photo_index is None:
                continue
            # Keep unidentified cases (still have a cover) but skip empty junk rows.
            if not track_title and not (row.get("Cover Crop") or "").strip():
                continue
            if disc_number is None:
                disc_number = 1

            album_key = (source_image, photo_index)
            release_id = _parse_int(row.get("Discogs Release ID") or "")
            album = albums.get(album_key)
            if album is None:
                album = _AlbumBucket(
                    source_image=source_image,
                    photo_index=photo_index,
                    artist=(row.get("CD Artist") or "").strip() or "Unknown Artist",
                    title=(row.get("Album") or "").strip() or "Unknown Album",
                    needs_review=_truthy_review(row.get("Needs Review") or ""),
                    cover_path=(row.get("Cover Crop") or "").strip(),
                    discogs_release_id=release_id,
                )
                albums[album_key] = album
            else:
                if _truthy_review(row.get("Needs Review") or ""):
                    album.needs_review = True
                if not album.cover_path:
                    album.cover_path = (row.get("Cover Crop") or "").strip()
                if album.discogs_release_id is None and release_id is not None:
                    album.discogs_release_id = release_id

            disc = album.discs.get(disc_number)
            if disc is None:
                disc = _DiscBucket(disc_number=disc_number)
                album.discs[disc_number] = disc

            if track_title:
                disc.tracks.append(
                    _TrackRow(
                        artist=(row.get("Track Artist") or "").strip()
                        or album.artist,
                        title=track_title,
                    )
                )
    return albums


def import_catalog(
    conn: sqlite3.Connection,
    csv_path: Path | str = DEFAULT_CSV_PATH,
    repo_root: Path | str = REPO_ROOT,
    crops_base: Path | str | None = None,
) -> dict[str, int]:
    """Upsert catalog from CSV. Existing slot assignments are kept by natural key."""
    csv_path = Path(csv_path)
    crops_root = Path(crops_base) if crops_base is not None else Path(repo_root)
    init_schema(conn)

    albums = load_csv_buckets(csv_path)

    # Snapshot current slot assignments keyed by natural disc identity.
    existing_slots = conn.execute(
        """
        SELECT s.slot_number, d.source_image, d.photo_index, d.disc_number
        FROM slots s
        JOIN discs d ON d.id = s.disc_id
        WHERE s.disc_id IS NOT NULL
        """
    ).fetchall()
    slot_by_key: dict[tuple[str, int, int], int] = {
        (row["source_image"], row["photo_index"], row["disc_number"]): row[
            "slot_number"
        ]
        for row in existing_slots
    }

    # Clear catalog tables but leave slots empty until remapped.
    conn.execute("UPDATE slots SET disc_id = NULL")
    conn.execute("DELETE FROM tracks")
    conn.execute("DELETE FROM discs")
    conn.execute("DELETE FROM albums")
    # Reset autoincrement so ids stay compact after a full rebuild.
    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('albums', 'discs', 'tracks')")

    album_count = 0
    disc_count = 0
    track_count = 0
    cover_count = 0

    for album_key in sorted(albums.keys(), key=lambda k: (k[0], k[1])):
        album = albums[album_key]
        cover_bytes = _read_cover(album.cover_path, crops_root)
        if cover_bytes is not None:
            cover_count += 1

        cur = conn.execute(
            """
            INSERT INTO albums (
                source_image, photo_index, artist, title,
                needs_review, discogs_release_id, cover_path, cover_jpeg
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                album.source_image,
                album.photo_index,
                album.artist,
                album.title,
                1 if album.needs_review else 0,
                album.discogs_release_id,
                album.cover_path,
                cover_bytes,
            ),
        )
        album_id = cur.lastrowid
        album_count += 1

        for disc_number in sorted(album.discs.keys()):
            disc = album.discs[disc_number]
            dcur = conn.execute(
                """
                INSERT INTO discs (
                    album_id, disc_number, source_image, photo_index
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    album_id,
                    disc.disc_number,
                    album.source_image,
                    album.photo_index,
                ),
            )
            disc_id = dcur.lastrowid
            disc_count += 1

            natural = (album.source_image, album.photo_index, disc.disc_number)
            if natural in slot_by_key:
                conn.execute(
                    "UPDATE slots SET disc_id = ? WHERE slot_number = ?",
                    (disc_id, slot_by_key[natural]),
                )

            for track_number, track in enumerate(disc.tracks, start=1):
                conn.execute(
                    """
                    INSERT INTO tracks (disc_id, track_number, artist, title)
                    VALUES (?, ?, ?, ?)
                    """,
                    (disc_id, track_number, track.artist, track.title),
                )
                track_count += 1

    conn.commit()
    return {
        "albums": album_count,
        "discs": disc_count,
        "tracks": track_count,
        "covers": cover_count,
    }


def ensure_imported(
    conn: sqlite3.Connection,
    csv_path: Path | str = DEFAULT_CSV_PATH,
    repo_root: Path | str = REPO_ROOT,
    crops_base: Path | str | None = None,
    force: bool = False,
) -> dict[str, int]:
    init_schema(conn)
    album_count = conn.execute("SELECT COUNT(*) AS n FROM albums").fetchone()["n"]
    if album_count and not force:
        return {
            "albums": album_count,
            "discs": conn.execute("SELECT COUNT(*) AS n FROM discs").fetchone()["n"],
            "tracks": conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"],
            "covers": conn.execute(
                "SELECT COUNT(*) AS n FROM albums WHERE cover_jpeg IS NOT NULL"
            ).fetchone()["n"],
            "skipped": 1,
        }
    return import_catalog(
        conn,
        csv_path=csv_path,
        repo_root=repo_root,
        crops_base=crops_base if crops_base is not None else repo_root,
    )
