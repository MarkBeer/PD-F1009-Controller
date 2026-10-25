"""Turn a Discogs release into one CSV row per track."""

from __future__ import annotations

import csv
import re
from pathlib import Path

COLUMNS = [
    "Input Image",
    "Index",
    "CD Artist",
    "Album",
    "CD Number",
    "Track Artist",
    "Track Title",
    "Cover Crop",
]

_DISAMBIG = re.compile(r"\s*\(\d+\)")
_DASH_POS = re.compile(r"^(?:CD\s*)?(\d+)\s*[-–]\s*(\d+)$", re.I)
_DOT_POS = re.compile(r"^(\d+)\.(\d+)$")
_PLAIN_POS = re.compile(r"^\d+$")
_SILENCE = {"(silence)", "silence", "[silence]", "(silent)", "silent"}


def clean_name(name: str) -> str:
    return _DISAMBIG.sub("", name or "").strip()


def artist_text(value) -> str:
    """Accept a Discogs artist string or a list of {name, join} objects."""
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


def is_various(name: str) -> bool:
    return name.strip().lower() in {"various", "various artists"}


def _is_silence(title: str) -> bool:
    return title.strip().lower() in _SILENCE


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
    """14.1 next to plain '14' is a hidden subtrack. 1.1, 1.2, 2.1 is disc.track."""
    plain = [p for p in positions if _PLAIN_POS.fullmatch(p)]
    dots = [p for p in positions if _DOT_POS.fullmatch(p)]
    dashes = [p for p in positions if _DASH_POS.match(p)]
    if dashes:
        return True
    if plain and dots:
        return True
    return False


def disc_number(pos: str, dotted_subtrack: bool) -> int:
    pos = (pos or "").strip()
    dashed = _DASH_POS.match(pos)
    if dashed:
        return int(dashed.group(1))
    dotted = _DOT_POS.match(pos)
    if dotted and not dotted_subtrack:
        return int(dotted.group(1))
    return 1


def release_tracks(release: dict) -> list[tuple[int, str, str]]:
    """Return (disc, track artist, title) for real tracks on a slim or raw release."""
    raw = release.get("tracks")
    if raw is None:
        raw = release.get("tracklist") or []
    positions = [str(item.get("pos") or item.get("position") or "").strip() for item in raw]
    dotted_subtrack = _dotted_are_subtracks(positions)
    album_artist = artist_text(release.get("artists"))
    if is_various(album_artist):
        album_artist = "Various"
    rows: list[tuple[int, str, str]] = []
    for item in raw:
        kind = (item.get("type") or item.get("type_") or "track").lower()
        if kind in {"heading", "index"}:
            continue
        title = (item.get("title") or "").strip()
        if not title or _is_silence(title):
            continue
        pos = str(item.get("pos") or item.get("position") or "").strip()
        performer = artist_text(item.get("artists"))
        if not performer:
            performer = album_artist
        featured = [name for name in _featuring_names(item) if name.lower() not in performer.lower()]
        if featured:
            performer = f"{performer} feat. {', '.join(featured)}"
        rows.append((disc_number(pos, dotted_subtrack), performer, title))
    return rows


def cd_artist(release: dict) -> str:
    name = artist_text(release.get("artists"))
    if is_various(name):
        return "Various"
    return name


def catalog_rows(
    image_name: str,
    index: int,
    crop: str,
    release: dict | None,
    album: str | None = None,
) -> list[dict[str, str]]:
    """One dict per track. A missing release becomes a single 'unable to identify' row."""
    if release is None:
        return [
            {
                "Input Image": image_name,
                "Index": str(index),
                "CD Artist": "",
                "Album": "",
                "CD Number": "",
                "Track Artist": "",
                "Track Title": "unable to identify",
                "Cover Crop": crop,
            }
        ]
    artist = cd_artist(release)
    title = (album or release.get("title") or "").strip()
    tracks = release_tracks(release)
    if not tracks:
        return [
            {
                "Input Image": image_name,
                "Index": str(index),
                "CD Artist": artist,
                "Album": title,
                "CD Number": "",
                "Track Artist": "",
                "Track Title": "track info unavailable",
                "Cover Crop": crop,
            }
        ]
    return [
        {
            "Input Image": image_name,
            "Index": str(index),
            "CD Artist": artist,
            "Album": title,
            "CD Number": str(disc),
            "Track Artist": performer,
            "Track Title": track_title,
            "Cover Crop": crop,
        }
        for disc, performer, track_title in tracks
    ]


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
