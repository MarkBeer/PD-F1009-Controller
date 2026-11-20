"""One folder per photo, so a new set of cases does not reuse an old search."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

INDEXER_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PARENT = INDEXER_ROOT / "output"


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def slug_stem(stem: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in stem)
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts)[:48] or "photo"


def is_run_directory(path: Path) -> bool:
    return (path / "run.json").is_file() or (path / "grid.json").is_file()


def run_image_name(path: Path) -> str | None:
    meta = path / "run.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text())
    except json.JSONDecodeError:
        return None
    image = data.get("image")
    return image if isinstance(image, str) and image else None


def new_run_directory(parent: Path, image_path: Path, moment: datetime | None = None) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    stamp = (moment or datetime.now()).strftime("%Y%m%d-%H%M%S")
    slug = slug_stem(image_path.stem)
    path = parent / f"{stamp}-{slug}"
    suffix = 2
    while path.exists():
        path = parent / f"{stamp}-{slug}-{suffix}"
        suffix += 1
    path.mkdir()
    (path / "run.json").write_text(
        json.dumps(
            {"image": image_path.name, "image_path": str(image_path), "cases": 0},
            indent=2,
        )
        + "\n"
    )
    return path


def resolve_run_directory(requested: Path | None, image_path: Path) -> tuple[Path, str]:
    """Return the folder for this photo and a line to print.

    A parent directory gets a new timestamped child. A folder that already
    belongs to this photo is reused, so the grid can be adjusted. A folder
    that belongs to a different photo is left alone and a sibling is created.
    """
    parent = DEFAULT_OUTPUT_PARENT if requested is None else requested
    if is_run_directory(parent):
        existing = run_image_name(parent)
        if existing == image_path.name:
            shown = display_path(parent)
            return parent, f"Resuming {shown}"
        path = new_run_directory(parent.parent, image_path)
        shown = display_path(path)
        if existing:
            previous = display_path(parent)
            return path, f"{previous} already holds {existing}. New run folder: {shown}"
        return path, f"New run folder: {shown}"
    path = new_run_directory(parent, image_path)
    return path, f"New run folder: {display_path(path)}"
