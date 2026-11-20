"""Fetch a Discogs release and apply it to a catalog album."""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

import httpx

USER_AGENT = "DiscDetect/1.0 (local PD-F1009 catalog)"
API = "https://api.discogs.com"

_DISAMBIG = re.compile(r"\s*\(\d+\)")
_DASH_POS = re.compile(r"^(?:CD\s*)?(\d+)\s*[-–]\s*(\d+)$", re.I)
_DOT_POS = re.compile(r"^(\d+)\.(\d+)$")
_PLAIN_POS = re.compile(r"^\d+$")
_SILENCE = {"(silence)", "silence", "[silence]", "(silent)", "silent"}


class DiscogsError(RuntimeError):
    pass


def _token() -> str | None:
    return os.environ.get("DISCOGS_TOKEN") or os.environ.get("DISCOGS_USER_TOKEN") or None


def clean_name(name: str) -> str:
    return _DISAMBIG.sub("", name or "").strip()


def artist_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_name(value)
    text = ""
    previous_join = ","
    for artist in value:
        name = clean_name(artist.get("name") or "")
        if not name:
            continue
        if not text:
            text = name
        elif previous_join == ",":
            text += f", {name}"
        else:
            text += f" {previous_join} {name}"
        previous_join = (artist.get("join") or ",").strip() or ","
    return text


def _is_various(name: str) -> bool:
    return name.strip().lower() in {"various", "various artists"}


def _featuring_names(track: dict) -> list[str]:
    names: list[str] = []
    for extra in track.get("extraartists") or track.get("featuring") or []:
        if isinstance(extra, str):
            names.append(clean_name(extra))
            continue
        role = (extra.get("role") or "").lower()
        if "feat" not in role:
            continue
        name = clean_name(extra.get("name") or "")
        if name:
            names.append(name)
    return names


def _dotted_are_subtracks(positions: list[str]) -> bool:
    plain = [p for p in positions if _PLAIN_POS.fullmatch(p)]
    dots = [p for p in positions if _DOT_POS.fullmatch(p)]
    dashes = [p for p in positions if _DASH_POS.match(p)]
    if dashes:
        return True
    if plain and dots:
        return True
    return False


def _disc_number(pos: str, dotted_subtrack: bool) -> int:
    pos = (pos or "").strip()
    dashed = _DASH_POS.match(pos)
    if dashed:
        return int(dashed.group(1))
    dotted = _DOT_POS.match(pos)
    if dotted and not dotted_subtrack:
        return int(dotted.group(1))
    return 1


def cd_artist(release: dict) -> str:
    name = artist_text(release.get("artists"))
    if _is_various(name):
        return "Various"
    return name


def release_tracks(release: dict) -> list[tuple[int, str, str]]:
    """Return (disc_number, track_artist, title) for real tracks."""
    raw = release.get("tracklist") or release.get("tracks") or []
    positions = [
        str(item.get("pos") or item.get("position") or "").strip() for item in raw
    ]
    dotted_subtrack = _dotted_are_subtracks(positions)
    album_artist = cd_artist(release)
    rows: list[tuple[int, str, str]] = []
    for item in raw:
        kind = (item.get("type") or item.get("type_") or "track").lower()
        if kind in {"heading", "index"}:
            continue
        title = (item.get("title") or "").strip()
        if not title or title.strip().lower() in _SILENCE:
            continue
        pos = str(item.get("pos") or item.get("position") or "").strip()
        performer = artist_text(item.get("artists"))
        if not performer:
            performer = album_artist
        featured = [
            name
            for name in _featuring_names(item)
            if name.lower() not in performer.lower()
        ]
        if featured:
            performer = f"{performer} feat. {', '.join(featured)}"
        rows.append((_disc_number(pos, dotted_subtrack), performer, title))
    return rows


def primary_image_url(release: dict) -> str | None:
    images = release.get("images") or []
    primary = next((img for img in images if img.get("type") == "primary"), None)
    candidates = [primary] if primary else []
    candidates.extend(images)
    for img in candidates:
        if not img:
            continue
        for key in ("uri", "resource_url", "uri150"):
            url = (img.get(key) or "").strip()
            if url.startswith("http"):
                return url
    return None


def fetch_release(release_id: int, *, client: httpx.Client | None = None) -> dict:
    if release_id < 1:
        raise DiscogsError("Discogs release id must be a positive integer")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    auth = _token()
    if auth:
        headers["Authorization"] = f"Discogs token={auth}"
    url = f"{API}/releases/{release_id}"
    owns_client = client is None
    http = client or httpx.Client(timeout=60.0)
    try:
        response = http.get(url, headers=headers)
        if response.status_code == 404:
            raise DiscogsError(f"Discogs release {release_id} was not found")
        if response.status_code == 429:
            raise DiscogsError("Discogs rate limit reached; try again shortly")
        if response.status_code >= 400:
            raise DiscogsError(
                f"Discogs HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json()
    except httpx.HTTPError as exc:
        raise DiscogsError(f"Could not reach Discogs: {exc}") from exc
    finally:
        if owns_client:
            http.close()


def download_jpeg(url: str, *, client: httpx.Client | None = None) -> bytes | None:
    headers = {"User-Agent": USER_AGENT}
    owns_client = client is None
    http = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        response = http.get(url, headers=headers)
        if response.status_code >= 400:
            return None
        data = response.content
        if len(data) < 4 or data[:2] != b"\xff\xd8":
            return None
        if len(data) > 8 * 1024 * 1024:
            return None
        return data
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            http.close()


def apply_release_to_album(
    conn: sqlite3.Connection,
    album_id: int,
    release_id: int,
    *,
    client: httpx.Client | None = None,
    fetch_cover: bool = True,
) -> dict[str, Any]:
    """Replace album metadata, discs, tracks, and optionally cover from Discogs.

    Slot assignments are preserved by disc number when possible.
    """
    album = conn.execute(
        "SELECT id, source_image, photo_index FROM albums WHERE id = ?",
        (album_id,),
    ).fetchone()
    if not album:
        raise DiscogsError("Album not found")

    release = fetch_release(release_id, client=client)
    artist = cd_artist(release) or "Unknown Artist"
    title = (release.get("title") or "").strip() or "Unknown Album"
    tracks = release_tracks(release)
    if not tracks:
        raise DiscogsError("Discogs release has no usable track list")

    # Preserve slots keyed by current disc_number.
    existing = conn.execute(
        """
        SELECT d.id, d.disc_number, s.slot_number
        FROM discs d
        LEFT JOIN slots s ON s.disc_id = d.id
        WHERE d.album_id = ?
        """,
        (album_id,),
    ).fetchall()
    slots_by_disc_number = {
        int(row["disc_number"]): int(row["slot_number"])
        for row in existing
        if row["slot_number"] is not None
    }

    # Clearing discs nulls slots (ON DELETE SET NULL), then we restore by number.
    conn.execute("DELETE FROM discs WHERE album_id = ?", (album_id,))

    cover_bytes: bytes | None = None
    cover_path = ""
    if fetch_cover:
        image_url = primary_image_url(release)
        if image_url:
            cover_bytes = download_jpeg(image_url, client=client)
            if cover_bytes:
                cover_path = f"discogs:{release_id}"

    if cover_bytes is not None:
        conn.execute(
            """
            UPDATE albums
            SET artist = ?, title = ?, discogs_release_id = ?,
                needs_review = 0, cover_jpeg = ?, cover_path = ?
            WHERE id = ?
            """,
            (artist, title, release_id, cover_bytes, cover_path, album_id),
        )
    else:
        conn.execute(
            """
            UPDATE albums
            SET artist = ?, title = ?, discogs_release_id = ?, needs_review = 0
            WHERE id = ?
            """,
            (artist, title, release_id, album_id),
        )

    by_disc: dict[int, list[tuple[str, str]]] = {}
    for disc_no, track_artist, track_title in tracks:
        by_disc.setdefault(disc_no, []).append((track_artist, track_title))

    restored_slots = 0
    for disc_no in sorted(by_disc):
        cur = conn.execute(
            """
            INSERT INTO discs (album_id, disc_number, source_image, photo_index)
            VALUES (?, ?, ?, ?)
            """,
            (album_id, disc_no, album["source_image"], album["photo_index"]),
        )
        disc_id = int(cur.lastrowid)
        for track_number, (track_artist, track_title) in enumerate(
            by_disc[disc_no], start=1
        ):
            conn.execute(
                """
                INSERT INTO tracks (disc_id, track_number, artist, title)
                VALUES (?, ?, ?, ?)
                """,
                (disc_id, track_number, track_artist, track_title),
            )
        slot_number = slots_by_disc_number.get(disc_no)
        if slot_number is not None:
            conn.execute(
                "UPDATE slots SET disc_id = ? WHERE slot_number = ?",
                (disc_id, slot_number),
            )
            restored_slots += 1

    conn.commit()
    return {
        "release_id": release_id,
        "artist": artist,
        "title": title,
        "disc_count": len(by_disc),
        "track_count": len(tracks),
        "cover_updated": cover_bytes is not None,
        "slots_restored": restored_slots,
    }
