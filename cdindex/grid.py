"""Find a regular grid of jewel cases and crop each cover.

Cases are indexed left to right, top to bottom, starting at 1.
A hand-corrected grid.json can replace the detector.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Cell:
    index: int
    row: int
    col: int
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass
class Grid:
    image_width: int
    image_height: int
    rows: int
    cols: int
    slope: float
    x_ref: int
    inset_x: int
    inset_y: int
    cells: list[Cell]
    edge_score: float = 0.0

    def to_json(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_json(cls, data: dict) -> Grid:
        cells = [Cell(**c) for c in data["cells"]]
        return cls(
            image_width=int(data["image_width"]),
            image_height=int(data["image_height"]),
            rows=int(data["rows"]),
            cols=int(data["cols"]),
            slope=float(data.get("slope") or 0),
            x_ref=int(data.get("x_ref") or 0),
            inset_x=int(data.get("inset_x") or 0),
            inset_y=int(data.get("inset_y") or 0),
            cells=cells,
            edge_score=float(data.get("edge_score") or 0),
        )


def load_grid(path: Path) -> Grid:
    return Grid.from_json(json.loads(Path(path).read_text()))


def save_grid(grid: Grid, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(grid.to_json(), indent=2) + "\n")


def dominant_period(proj: np.ndarray, lo: int, hi: int) -> int | None:
    """Return the strongest repeating gap between lo and hi, or None."""
    if hi - lo < 4 or len(proj) < hi + 2:
        return None
    centered = proj.astype(np.float64) - float(proj.mean())
    corr = np.correlate(centered, centered, mode="full")[len(centered) - 1 :]
    if corr[0] <= 0:
        return None
    corr = corr / corr[0]
    hi = min(hi, len(corr) - 1)
    if hi <= lo:
        return None
    window = corr[lo:hi]
    peak = lo + int(np.argmax(window))
    if float(corr[peak]) < 0.12:
        return None
    return peak


def best_regular_grid(
    proj: np.ndarray,
    count: int | None = None,
    pitch_hint: int | None = None,
) -> tuple[int, int, int, int, float]:
    """Fit equal cells to a 1-D edge projection.

    Returns start, pitch, gutter, cell count, and mean edge score in 0..1.
    """
    n = len(proj)
    peak = float(proj.max()) if n else 0.0
    if peak <= 0:
        raise ValueError("No edges to fit a grid against.")
    strength = proj.astype(np.float64) / peak
    pitch0 = pitch_hint or dominant_period(proj, max(40, n // 16), max(80, n // 2))
    if pitch0 is None:
        raise ValueError(
            "Could not see a repeating row or column of cases. "
            "Pass --rows and --cols, or a grid.json."
        )

    best: tuple[float, int, int, int, int] | None = None
    for dp in range(-40, 41, 2):
        pitch = pitch0 + dp
        if pitch < 40:
            continue
        for gutter in (8, 12, 18, 28, 40):
            width = pitch - gutter
            if width < int(pitch * 0.72):
                continue
            for start in range(0, pitch, 3):
                lefts: list[int] = []
                x = start
                while x + width < n - 2:
                    lefts.append(x)
                    x += pitch
                ncells = len(lefts)
                if ncells < 1 or ncells > 16:
                    continue
                if count is not None and ncells != count:
                    continue
                vals: list[float] = []
                for left in lefts:
                    right = left + width
                    vals.append(float(strength[max(0, left - 12) : left + 13].max()))
                    vals.append(float(strength[max(0, right - 12) : min(n, right + 13)].max()))
                if float(np.percentile(vals, 25)) < 0.15:
                    continue
                score = float(np.mean(vals))
                if _better(score, ncells, best):
                    best = (score, ncells, pitch, gutter, start)
    if best is None:
        raise ValueError(
            "Could not place a grid on this image. "
            "Pass --rows and --cols, or a grid.json."
        )
    score, ncells, pitch, gutter, start = best
    return start, pitch, gutter, ncells, score


def _better(score: float, ncells: int, best: tuple[float, int, int, int, int] | None) -> bool:
    if best is None:
        return True
    best_score, best_n, *_ = best
    if ncells > best_n and score >= best_score - 0.03:
        return True
    if ncells == best_n and score > best_score:
        return True
    if ncells < best_n and score > best_score + 0.03:
        return True
    return False


def _projection(image: np.ndarray, axis: str) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if axis == "x":
        energy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        y0, y1 = int(height * 0.08), int(height * 0.92)
        proj = energy[y0:y1].mean(axis=0)
    else:
        energy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        x0, x1 = int(width * 0.25), int(width * 0.75)
        proj = energy[:, x0:x1].mean(axis=1)
    return np.convolve(proj, np.ones(9) / 9, mode="same")


def single_case_grid(image: np.ndarray, inset: int) -> Grid:
    height, width = image.shape[:2]
    margin_x = max(inset, int(width * 0.02))
    margin_y = max(inset, int(height * 0.02))
    cell = Cell(1, 1, 1, margin_x, margin_y, width - margin_x, height - margin_y)
    return Grid(width, height, 1, 1, 0.0, 0, 0, 0, [cell], 0.0)


def detect_grid(
    image: np.ndarray,
    rows: int | None = None,
    cols: int | None = None,
    inset: int = 6,
) -> Grid:
    """Detect axis-aligned case boxes. Y grows downward, X to the right."""
    if rows == 1 and cols == 1:
        return single_case_grid(image, inset)
    height, width = image.shape[:2]
    vx = _projection(image, "x")
    hy = _projection(image, "y")
    x_start, x_pitch, x_gutter, ncols, x_score = best_regular_grid(vx, count=cols)
    y_start, y_pitch, y_gutter, nrows, y_score = best_regular_grid(hy, count=rows)
    if rows is not None and nrows != rows:
        raise ValueError(f"Asked for {rows} rows but found {nrows}.")
    if cols is not None and ncols != cols:
        raise ValueError(f"Asked for {cols} columns but found {ncols}.")
    x_width = x_pitch - x_gutter
    y_width = y_pitch - y_gutter
    cells: list[Cell] = []
    index = 1
    for row in range(nrows):
        y0 = y_start + row * y_pitch
        y1 = y0 + y_width
        for col in range(ncols):
            x0 = x_start + col * x_pitch
            x1 = x0 + x_width
            cells.append(Cell(index, row + 1, col + 1, int(x0), int(y0), int(x1), int(y1)))
            index += 1
    return Grid(
        image_width=width,
        image_height=height,
        rows=nrows,
        cols=ncols,
        slope=0.0,
        x_ref=0,
        inset_x=inset,
        inset_y=inset,
        cells=cells,
        edge_score=round((x_score + y_score) / 2, 4),
    )


def crop_bounds(grid: Grid, cell: Cell, image_shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """Return y0, y1, x0, x1 inside the image, after slope and inset."""
    height, width = image_shape
    center_x = (cell.x0 + cell.x1) // 2
    shift = int(round(grid.slope * (center_x - grid.x_ref)))
    x0 = max(0, cell.x0 + grid.inset_x)
    x1 = min(width, cell.x1 - grid.inset_x)
    y0 = max(0, cell.y0 + shift + grid.inset_y)
    y1 = min(height, cell.y1 + shift - grid.inset_y)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"Cell {cell.index} collapsed to an empty crop.")
    return y0, y1, x0, x1


def write_crops(image: np.ndarray, grid: Grid, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for cell in grid.cells:
        y0, y1, x0, x1 = crop_bounds(grid, cell, image.shape[:2])
        path = dest / f"{cell.index:02d}.jpg"
        cv2.imwrite(str(path), image[y0:y1, x0:x1], [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        paths.append(path)
    return paths


def write_preview(image: np.ndarray, grid: Grid, path: Path) -> None:
    """Draw the detected boxes on a smaller copy of the photo."""
    canvas = image.copy()
    for cell in grid.cells:
        y0, y1, x0, x1 = crop_bounds(grid, cell, image.shape[:2])
        cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), (40, 220, 40), 3)
        cv2.putText(
            canvas,
            str(cell.index),
            (x0 + 8, y0 + 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (40, 220, 40),
            2,
            cv2.LINE_AA,
        )
    scale = min(1.0, 1400 / canvas.shape[1])
    if scale < 1:
        canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
