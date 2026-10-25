import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from cdindex.catalog import catalog_rows, release_tracks, write_csv
from cdindex.discogs import album_phrase, choose_queries, rank_hits, score_release
from cdindex.grid import best_regular_grid, crop_bounds, detect_grid, load_grid
from cdindex.yandex import parse_results_html

ROOT = Path(__file__).resolve().parents[1]
PHOTO = ROOT / "assets" / "a760bb13-1170-47fb-88be-bf50082ed5f3.jpg"
RELEASES = ROOT / "output" / "discogs" / "releases"
HAND_GRID = ROOT / "examples" / "table-48-grid.json"
SELECTION = ROOT / "examples" / "table-48.json"

# Outer boxes of the published crops, measured on the photo before the inset.
HAND_COLS = [(178, 632), (662, 1088), (1118, 1528), (1558, 1978), (2008, 2448), (2478, 2945)]
HAND_ROWS = [
    (128, 492),
    (492, 878),
    (878, 1258),
    (1258, 1668),
    (1668, 2080),
    (2080, 2488),
    (2488, 2898),
    (2898, 3305),
]


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union else 0.0


class GridTests(unittest.TestCase):
    def test_regular_signal_finds_the_cells(self):
        proj = np.zeros(2200, dtype=np.float64)
        for index in range(8):
            left = 80 + index * 250
            right = left + 220
            proj[left - 1 : left + 2] = 12
            proj[right - 1 : right + 2] = 12
        start, pitch, _gutter, count, score = best_regular_grid(proj, count=8)
        self.assertEqual(count, 8)
        self.assertGreater(score, 0.5)
        self.assertLess(abs(pitch - 250), 20)
        self.assertLess(abs(start - 80), 20)

    def test_photo_grid_overlaps_the_hand_measured_cases(self):
        image = cv2.imread(str(PHOTO))
        self.assertIsNotNone(image)
        grid = detect_grid(image, inset=6)
        self.assertEqual((grid.cols, grid.rows), (6, 8))
        self.assertEqual(len(grid.cells), 48)
        scores = []
        for cell in grid.cells:
            known = (
                HAND_COLS[cell.col - 1][0],
                HAND_ROWS[cell.row - 1][0],
                HAND_COLS[cell.col - 1][1],
                HAND_ROWS[cell.row - 1][1],
            )
            scores.append(_iou((cell.x0, cell.y0, cell.x1, cell.y1), known))
        self.assertGreaterEqual(sum(scores) / len(scores), 0.75)
        self.assertEqual(grid.cells[0].index, 1)
        self.assertEqual(grid.cells[-1].index, 48)

    def test_hand_grid_crops_match_the_published_covers(self):
        image = cv2.imread(str(PHOTO))
        grid = load_grid(HAND_GRID)
        self.assertEqual(len(grid.cells), 48)
        for cell in grid.cells:
            y0, y1, x0, x1 = crop_bounds(grid, cell, image.shape[:2])
            published = cv2.imread(str(ROOT / "output" / "crops" / f"{cell.index:02d}.jpg"))
            self.assertLessEqual(abs((y1 - y0) - published.shape[0]), 1, cell.index)
            self.assertEqual(x1 - x0, published.shape[1], cell.index)


class CatalogTests(unittest.TestCase):
    def test_mix_compilation_disc_numbers_and_spacers(self):
        christmas = json.loads((RELEASES / "9152475.json").read_text())
        rows = catalog_rows("photo.jpg", 1, "crops/01.jpg", christmas)
        self.assertEqual(rows[0]["CD Artist"], "Various")
        self.assertEqual(rows[0]["Track Artist"], "Showaddywaddy")
        self.assertEqual(rows[0]["Track Title"], "Hey Mr. Christmas")
        self.assertEqual(rows[0]["CD Number"], "1")
        self.assertEqual(len(rows), 14)

        joss = json.loads((RELEASES / "916692.json").read_text())
        joss_rows = catalog_rows("photo.jpg", 33, "crops/33.jpg", joss)
        titles = [row["Track Title"] for row in joss_rows]
        self.assertIn("Sleep Like A Child", titles)
        self.assertIn("Daniel", titles)
        self.assertNotIn("(silence)", titles)
        self.assertTrue(all(row["CD Number"] == "1" for row in joss_rows))

        blondie = json.loads((RELEASES / "2504107.json").read_text())
        blondie_rows = catalog_rows("photo.jpg", 28, "crops/28.jpg", blondie)
        self.assertEqual(len(blondie_rows), 15)
        self.assertNotIn("Bonus Tracks", [row["Track Title"] for row in blondie_rows])

        motown = json.loads((RELEASES / "629140.json").read_text())
        discs = {disc for disc, _artist, _title in release_tracks(motown)}
        self.assertEqual(discs, {1, 2, 3})

    def test_featured_credit_is_added_to_the_track_artist(self):
        release = {
            "title": "Missing You",
            "artists": "Chris de Burgh",
            "tracks": [
                {
                    "pos": "2",
                    "title": "The Connemara Coast",
                    "type": "track",
                    "artists": "",
                    "featuring": ["The Chieftains"],
                }
            ],
        }
        rows = catalog_rows("photo.jpg", 39, "crops/39.jpg", release)
        self.assertEqual(rows[0]["Track Artist"], "Chris de Burgh feat. The Chieftains")
        self.assertEqual(rows[0]["CD Artist"], "Chris de Burgh")

    def test_missing_release_is_marked(self):
        rows = catalog_rows("photo.jpg", 4, "crops/04.jpg", None)
        self.assertEqual(rows[0]["Track Title"], "unable to identify")

    def test_selection_matches_the_curated_track_counts(self):
        selection = json.loads(SELECTION.read_text())
        import csv

        curated: dict[int, int] = {}
        with (ROOT / "cd_index.csv").open(newline="") as handle:
            for row in csv.DictReader(handle):
                curated[int(row["Index"])] = curated.get(int(row["Index"]), 0) + 1
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.csv"
            from cdindex.cli import main

            status = main(
                [
                    "export",
                    "--selection",
                    str(SELECTION),
                    "--releases",
                    str(RELEASES),
                    "--csv",
                    str(dest),
                ]
            )
            self.assertEqual(status, 0)
            with dest.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        counts: dict[int, int] = {}
        for row in rows:
            counts[int(row["Index"])] = counts.get(int(row["Index"]), 0) + 1
        self.assertEqual(counts, curated)
        self.assertEqual(len(rows), sum(curated.values()))
        self.assertEqual(
            list(rows[0]),
            [
                "Input Image",
                "Index",
                "CD Artist",
                "Album",
                "CD Number",
                "Track Artist",
                "Track Title",
                "Cover Crop",
            ],
        )


class SearchRankingTests(unittest.TestCase):
    def test_cover_tags_prefer_the_title_that_appears_more_often(self):
        yandex = {
            "tags": [
                "madonna like a prayer",
                "like a prayer madonna",
                "madonna like a virgin",
            ],
            "objects": [{"title": "Like a Virgin", "subtitle": "Album"}],
        }
        queries = choose_queries(yandex, limit=2)
        self.assertTrue(any("prayer" in query for query in queries))
        hits = [
            {
                "id": 1,
                "title": "Madonna - Like A Virgin",
                "country": "Europe",
                "format": ["CD", "Album"],
                "catno": "V1",
            },
            {
                "id": 2,
                "title": "Madonna - Like A Prayer",
                "country": "Europe",
                "format": ["CD", "Album"],
                "catno": "P1",
            },
            {
                "id": 3,
                "title": "Madonna - Like A Prayer",
                "country": "US",
                "format": ["Vinyl", "LP"],
                "catno": "LP",
            },
        ]
        evidence = yandex["tags"]
        objects = ["Like a Virgin"]
        self.assertIsNone(score_release(hits[2], evidence, objects))
        ranked = rank_hits(hits, evidence, objects)
        self.assertEqual(ranked[0]["id"], 2)
        self.assertEqual(album_phrase(ranked[0]["title"]), "Like A Prayer")

    def test_object_title_promotes_the_tag_that_names_the_album(self):
        yandex = {
            "tags": [
                "cher dov è l amore",
                "cher strong enough",
                "cher believe",
            ],
            "objects": [{"title": "Believe", "subtitle": "Album"}],
        }
        queries = choose_queries(yandex, limit=2)
        self.assertIn("believe", queries[0].lower())

    def test_album_beats_a_single_with_the_same_title(self):
        evidence = ["cher believe"]
        single = {"id": 1, "title": "Cher - Believe", "country": "UK", "format": ["CD", "Single"]}
        album = {"id": 2, "title": "Cher - Believe", "country": "UK", "format": ["CD", "Album"]}
        ranked = rank_hits([single, album], evidence, ["Believe"])
        self.assertEqual(ranked[0]["id"], 2)

    def test_uk_pressing_ranks_above_the_same_european_title_when_tags_tie(self):
        evidence = ["bee gees number ones"]
        uk = {"id": 10, "title": "Bee Gees - Number Ones", "country": "UK", "format": ["CD"]}
        europe = {"id": 11, "title": "Bee Gees - Number Ones", "country": "Europe", "format": ["CD"]}
        ranked = rank_hits([europe, uk], evidence)
        self.assertEqual([hit["id"] for hit in ranked], [10, 11])

    def test_yandex_html_tags_are_read(self):
        page = (
            '<html>&quot;cbirTags&quot;:{&quot;tags&quot;:[{&quot;text&quot;:&quot;cher believe&quot;}]},'
            '&quot;cbirObjectResponses&quot;:{&quot;objectResponses&quot;:'
            '[{&quot;title&quot;:&quot;Believe&quot;,&quot;subtitle&quot;:&quot;Album&quot;}]},'
            '&quot;cbirSites&quot;:{&quot;sites&quot;:[{&quot;title&quot;:&quot;Believe (Cher album)&quot;,'
            '&quot;url&quot;:&quot;https://example.test/believe&quot;,&quot;domain&quot;:&quot;example.test&quot;}]}'
            "</html>"
        )
        parsed = parse_results_html(page)
        self.assertEqual(parsed["tags"], ["cher believe"])
        self.assertEqual(parsed["objects"][0]["title"], "Believe")
        self.assertEqual(parsed["sites"][0]["domain"], "example.test")


class CsvWriterTests(unittest.TestCase):
    def test_writer_uses_the_track_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracks.csv"
            write_csv(catalog_rows("shot.jpg", 2, "crops/02.jpg", None), path)
            text = path.read_text()
        self.assertTrue(text.startswith("Input Image,Index,CD Artist,Album,CD Number,Track Artist,Track Title,Cover Crop\n"))
        self.assertIn("unable to identify", text)


if __name__ == "__main__":
    unittest.main()
