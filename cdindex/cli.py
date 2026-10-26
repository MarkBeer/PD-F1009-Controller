"""Command line for a new photo of jewel cases.

    python -m cdindex run photo.jpg --out output/run
    python -m cdindex export --out output/run

detect writes grid.json, preview.jpg, and crops/.
align opens a page where the four corners of a bad grid can be dragged.
search uploads each crop to Yandex.
identify picks a Discogs CD and writes review.json.
export writes cd_index.csv, one row per track.

Pin a wrong match by adding out/overrides.json and running identify again:

    {"12": {"release_id": 711192, "album": "Like a Prayer"}}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

from cdindex.catalog import catalog_rows, write_csv
from cdindex.discogs import (
    DiscogsError,
    choose_queries,
    choose_status,
    evidence_texts,
    fetch_release,
    load_release_file,
    rank_hits,
    save_json,
    search,
)
from cdindex.grid import detect_grid, load_grid, save_grid, write_crops, write_preview
from cdindex.yandex import YandexError, search_cover


def _log(message: str) -> None:
    print(message, flush=True)


def _read_image(path: Path):
    image = cv2.imread(str(path))
    if image is None:
        raise SystemExit(f"Could not read image {path}")
    return image


def _run_meta(out: Path) -> dict:
    path = out / "run.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _overrides(path: Path | None, out: Path) -> dict:
    chosen = path
    if chosen is None and (out / "overrides.json").exists():
        chosen = out / "overrides.json"
    if chosen is None or not Path(chosen).exists():
        return {}
    data = json.loads(Path(chosen).read_text())
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _release_dirs(out: Path, extra: list[Path]) -> list[Path]:
    return [out / "discogs" / "releases", *extra]


def _find_release(release_id: int, directories: list[Path]) -> dict | None:
    for directory in directories:
        found = load_release_file(directory, release_id)
        if found is not None:
            return found
    return None


def _store_release(out: Path, release: dict) -> None:
    save_json(out / "discogs" / "releases" / f"{release['id']}.json", release)


def cmd_detect(args: argparse.Namespace) -> int:
    out = Path(args.out)
    image_path = Path(args.image)
    image = _read_image(image_path)
    if args.grid:
        grid = load_grid(Path(args.grid))
        _log(f"Using grid {args.grid} ({grid.rows} x {grid.cols}, {len(grid.cells)} cases)")
    else:
        grid = detect_grid(image, rows=args.rows, cols=args.cols, inset=args.inset)
        _log(
            f"Detected {grid.cols} columns x {grid.rows} rows "
            f"({len(grid.cells)} cases, edge score {grid.edge_score})"
        )
    out.mkdir(parents=True, exist_ok=True)
    save_grid(grid, out / "grid.json")
    crops = write_crops(image, grid, out / "crops")
    write_preview(image, grid, out / "preview.jpg")
    save_json(
        out / "run.json",
        {"image": image_path.name, "image_path": str(image_path), "cases": len(crops)},
    )
    _log(f"Wrote {len(crops)} crops, {out / 'grid.json'}, and {out / 'preview.jpg'}")
    _log("Check preview.jpg. If a case is clipped, edit grid.json or pass --rows and --cols.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    out = Path(args.out)
    grid = load_grid(out / "grid.json")
    pause = getattr(args, "search_pause", None)
    if pause is None:
        pause = args.pause
    found = 0
    for cell in grid.cells:
        if args.limit and cell.index > args.limit:
            break
        dest = out / "yandex" / f"{cell.index:02d}.json"
        if dest.exists() and not args.force:
            found += 1
            continue
        crop = out / "crops" / f"{cell.index:02d}.jpg"
        _log(f"Image search {cell.index:02d} {crop.name}")
        try:
            result = search_cover(crop)
        except YandexError as exc:
            result = {"tags": [], "objects": [], "sites": [], "error": str(exc)}
            _log(f"  {exc}")
        save_json(dest, result)
        found += 1
        time.sleep(pause)
    _log(f"Image search files: {found}")
    return 0


def _candidate(hit: dict) -> dict:
    return {
        "release_id": hit.get("id"),
        "title": hit.get("title") or "",
        "country": hit.get("country") or "",
        "year": hit.get("year") or "",
        "catno": hit.get("catno") or "",
        "format": hit.get("format") or [],
        "score": hit.get("score"),
    }


def _chosen_from_release(release: dict, score: float | None = None) -> dict:
    labels = release.get("labels") or []
    catno = labels[0].get("catno") if labels and isinstance(labels[0], dict) else ""
    return {
        "release_id": release.get("id"),
        "title": release.get("title") or "",
        "artists": release.get("artists") or "",
        "country": release.get("country") or "",
        "year": release.get("year") or "",
        "catno": catno or "",
        "score": score,
    }


def _ensure_release(release_id: int, out: Path, extra: list[Path], pause: float) -> dict:
    found = _find_release(release_id, _release_dirs(out, extra))
    if found is not None:
        if not (out / "discogs" / "releases" / f"{release_id}.json").exists():
            _store_release(out, found)
        return found
    _log(f"  fetch Discogs release {release_id}")
    release = fetch_release(release_id, pause=pause)
    _store_release(out, release)
    return release


def cmd_identify(args: argparse.Namespace) -> int:
    out = Path(args.out)
    grid = load_grid(out / "grid.json")
    overrides = _overrides(Path(args.overrides) if args.overrides else None, out)
    extra = [Path(path) for path in args.releases]
    items: list[dict] = []
    for cell in grid.cells:
        if args.limit and cell.index > args.limit:
            break
        override = overrides.get(str(cell.index), {})
        yandex_path = out / "yandex" / f"{cell.index:02d}.json"
        yandex = json.loads(yandex_path.read_text()) if yandex_path.exists() else {}
        crop = _display_path(out / "crops" / f"{cell.index:02d}.jpg")
        entry = {
            "index": cell.index,
            "crop": crop,
            "queries": [],
            "yandex_tags": yandex.get("tags") or [],
            "yandex_objects": yandex.get("objects") or [],
            "candidates": [],
            "chosen": None,
            "status": "unable to identify",
            "note": yandex.get("error") or "",
        }
        try:
            if override.get("release_id"):
                release = _ensure_release(int(override["release_id"]), out, extra, args.pause)
                entry["chosen"] = _chosen_from_release(release)
                entry["status"] = "identified"
                entry["note"] = "release_id set in overrides"
            else:
                if override.get("query"):
                    queries = [override["query"]] if isinstance(override["query"], str) else list(override["query"])
                else:
                    queries = choose_queries(yandex)
                entry["queries"] = queries
                evidence = evidence_texts(yandex)
                objects = [
                    (obj.get("title") or "").strip()
                    for obj in (yandex.get("objects") or [])
                    if (obj.get("title") or "").strip()
                ]
                hits = _cached_hits(out, cell.index, queries, args.force, args.pause)
                ranked = rank_hits(hits, evidence, objects)
                entry["candidates"] = [_candidate(hit) for hit in ranked]
                if not ranked:
                    entry["note"] = entry["note"] or "No Discogs CD matched the cover search."
                else:
                    release = _ensure_release(int(ranked[0]["id"]), out, extra, args.pause)
                    entry["chosen"] = _chosen_from_release(release, ranked[0]["score"])
                    entry["status"] = choose_status(ranked)
                    if any(
                        (hit.get("title") or "").split(" - ", 1)[-1].lower()
                        == (ranked[0].get("title") or "").split(" - ", 1)[-1].lower()
                        and hit["id"] != ranked[0]["id"]
                        for hit in ranked[1:]
                    ):
                        entry["note"] = (
                            "Several pressings share this title. "
                            "Pin release_id in overrides.json if the case is a specific edition."
                        )
        except DiscogsError as exc:
            entry["status"] = "unable to identify"
            entry["note"] = str(exc)
            _log(f"{cell.index:02d} {exc}")
        items.append(entry)
        _log(f"{cell.index:02d} {entry['status']} {((entry.get('chosen') or {}).get('title') or '')}")
        save_json(out / "review.json", {"items": items})
    needs = [item["index"] for item in items if item["status"] != "identified"]
    if needs:
        _log(f"Needs another look: {', '.join(f'{index:02d}' for index in needs)}")
    else:
        _log(f"Identified {len(items)} cases.")
    return 0


def _cached_hits(out: Path, index: int, queries: list[str], force: bool, pause: float) -> list[dict]:
    path = out / "discogs" / "searches" / f"{index:02d}.json"
    if path.exists() and not force:
        cached = json.loads(path.read_text())
        if cached.get("queries") == queries:
            return cached.get("results") or []
    results: list[dict] = []
    for query in queries:
        _log(f"  Discogs search: {query}")
        results.extend(search(query, pause=pause))
    save_json(path, {"queries": queries, "results": results})
    return results


def cmd_export(args: argparse.Namespace) -> int:
    if getattr(args, "selection", None):
        return _export_selection(args)
    out = Path(args.out)
    review = json.loads((out / "review.json").read_text())
    meta = _run_meta(out)
    image_name = meta.get("image") or "image"
    overrides = _overrides(Path(args.overrides) if args.overrides else None, out)
    extra = [Path(path) for path in args.releases]
    rows: list[dict] = []
    for item in review["items"]:
        index = int(item["index"])
        override = overrides.get(str(index), {})
        chosen = item.get("chosen") or {}
        release_id = override.get("release_id") or chosen.get("release_id")
        release = None
        if release_id:
            release = _ensure_release(int(release_id), out, extra, args.pause)
        crop = item.get("crop") or _display_path(out / "crops" / f"{index:02d}.jpg")
        rows.extend(
            catalog_rows(
                image_name,
                index,
                crop,
                release,
                album=override.get("album"),
            )
        )
    dest = Path(args.csv) if args.csv else out / "cd_index.csv"
    write_csv(rows, dest)
    _log(f"Wrote {len(rows)} track rows to {dest}")
    return 0


def _export_selection(args: argparse.Namespace) -> int:
    selection = json.loads(Path(args.selection).read_text())
    image_name = selection.get("image") or "image"
    releases = [Path(path) for path in args.releases]
    if args.out:
        releases = _release_dirs(Path(args.out), releases)
    rows: list[dict] = []
    for item in selection["items"]:
        index = int(item["index"])
        release = _find_release(int(item["release_id"]), releases)
        if release is None:
            raise SystemExit(f"Missing Discogs file for release {item['release_id']} (index {index})")
        crop = item.get("crop") or ""
        rows.extend(catalog_rows(image_name, index, crop, release, album=item.get("album")))
    dest = Path(args.csv)
    write_csv(rows, dest)
    _log(f"Wrote {len(rows)} track rows to {dest}")
    return 0


def cmd_align(args: argparse.Namespace) -> int:
    from cdindex.align import AlignSession, serve

    session = AlignSession(
        Path(args.image),
        Path(args.out),
        args.rows,
        args.cols,
        args.inset,
    )
    serve(session, port=args.port)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cmd_detect(args)
    if not args.detect_only:
        cmd_search(args)
        cmd_identify(args)
        cmd_export(args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdindex",
        description="Crop a photo of CD cases, search each cover, and write a track CSV.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="Find the case grid and write one crop per case")
    _add_detect_args(detect)
    detect.set_defaults(func=cmd_detect)

    align = sub.add_parser("align", help="Drag the grid into place in a browser, then save the crops")
    align.add_argument("image", help="Original photo, without the green preview lines")
    align.add_argument("--out", required=True, help="Run directory for grid.json and crops")
    align.add_argument("--rows", type=int, help="Starting row count")
    align.add_argument("--cols", type=int, help="Starting column count")
    align.add_argument("--inset", type=int, help="Pixels trimmed inside each case")
    align.add_argument("--port", type=int, default=47321)
    align.set_defaults(func=cmd_align)

    search = sub.add_parser("search", help="Reverse image search each crop with Yandex")
    search.add_argument("--out", required=True, help="Run directory created by detect")
    search.add_argument("--force", action="store_true")
    search.add_argument("--limit", type=int, default=0, help="Stop after this index")
    search.add_argument("--pause", type=float, default=1.0, help="Seconds between uploads")
    search.set_defaults(func=cmd_search)

    identify = sub.add_parser("identify", help="Match cover searches to Discogs releases")
    _add_identify_args(identify)
    identify.set_defaults(func=cmd_identify)

    export = sub.add_parser("export", help="Write the one-row-per-track CSV")
    _add_export_args(export)
    export.set_defaults(func=cmd_export)

    run = sub.add_parser("run", help="detect, search, identify, and export")
    _add_detect_args(run)
    run.add_argument("--force", action="store_true", help="Redo image searches and Discogs searches")
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--pause", type=float, default=2.7, help="Seconds between Discogs requests")
    run.add_argument("--search-pause", type=float, default=1.0, help="Seconds between Yandex uploads")
    run.add_argument("--overrides", help="JSON map of index to release_id and album")
    run.add_argument(
        "--releases",
        action="append",
        default=[],
        help="Extra directory of cached Discogs release JSON. Repeat to add more.",
    )
    run.add_argument("--csv", help="CSV path. Defaults to OUT/cd_index.csv")
    run.add_argument("--detect-only", action="store_true", help="Stop after crops and preview.jpg")
    run.set_defaults(func=cmd_run)
    return parser


def _add_detect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("image", help="Photo of the cases")
    parser.add_argument("--out", required=True, help="Directory for crops, review, and CSV")
    parser.add_argument("--rows", type=int, help="Force the number of rows")
    parser.add_argument("--cols", type=int, help="Force the number of columns")
    parser.add_argument("--inset", type=int, default=6, help="Pixels cut inside each detected box")
    parser.add_argument("--grid", help="Use this grid.json instead of detecting")


def _add_identify_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True)
    parser.add_argument("--overrides")
    parser.add_argument("--releases", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pause", type=float, default=2.7)


def _add_export_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", help="Run directory with review.json")
    parser.add_argument("--csv", help="Where to write the CSV")
    parser.add_argument("--overrides")
    parser.add_argument("--releases", action="append", default=[])
    parser.add_argument("--pause", type=float, default=2.7)
    parser.add_argument(
        "--selection",
        help="Build the CSV from a JSON list of release ids instead of review.json",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "export" and not args.selection and not args.out:
        parser.error("export needs --out or --selection")
    if args.command == "export" and args.selection and not args.csv:
        parser.error("export --selection needs --csv")
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
