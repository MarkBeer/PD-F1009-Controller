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
                "Needs Review",
            ],
        )
        self.assertTrue(all(row["Needs Review"] == "no" for row in rows))


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


class AlignTests(unittest.TestCase):
    def test_four_corners_divide_into_the_case_grid(self):
        from cdindex.grid import cells_from_block, warp_quad

        cells = cells_from_block(2, 2, [[0, 0], [100, 0], [100, 40], [0, 40]])
        self.assertEqual(len(cells), 4)
        self.assertEqual((cells[0].x0, cells[0].y0, cells[0].x1, cells[0].y1), (0, 0, 50, 20))
        self.assertEqual(cells[3].row, 2)
        self.assertEqual(cells[3].col, 2)
        image = np.zeros((80, 120, 3), dtype=np.uint8)
        image[10:50, 20:70] = (0, 0, 220)
        warped = warp_quad(image, [[20, 10], [70, 10], [70, 50], [20, 50]])
        self.assertGreater(warped[:, :, 2].mean(), 200)
        self.assertEqual(warped.shape[1], 50)
        self.assertEqual(warped.shape[0], 40)

    def test_near_cases_get_more_room_than_far_cases(self):
        from cdindex.grid import cells_from_block

        cells = cells_from_block(2, 1, [[40, 0], [80, 0], [120, 100], [0, 100]])
        far_height = cells[0].y1 - cells[0].y0
        near_height = cells[1].y1 - cells[1].y0
        self.assertLess(far_height, near_height)

    def test_align_page_saves_one_crop_per_case(self):
        import json
        import threading
        from urllib.request import Request, urlopen

        from cdindex.align import AlignSession, make_server

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image_path = folder / "photo.jpg"
            canvas = np.zeros((80, 100, 3), dtype=np.uint8)
            canvas[:, :] = (40, 90, 140)
            cv2.imwrite(str(image_path), canvas)
            session = AlignSession(image_path, folder / "run", rows=2, cols=2, inset=0)
            server = make_server(session, "127.0.0.1", 0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                page = urlopen(f"http://127.0.0.1:{port}/").read()
                self.assertIn(b"Drag a corner", page)
                payload = json.dumps(
                    {
                        "rows": 2,
                        "cols": 2,
                        "inset": 0,
                        "corners": [[0, 0], [99, 0], [99, 79], [0, 79]],
                    }
                ).encode()
                request = Request(
                    f"http://127.0.0.1:{port}/save",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                saved = json.loads(urlopen(request).read())
                self.assertEqual(saved["cases"], 4)
                self.assertIn(b"Search, identify, and export", page)
                self.assertEqual(len(list((folder / "run" / "crops").glob("*.jpg"))), 4)
                grid = json.loads((folder / "run" / "grid.json").read_text())
                self.assertEqual(grid["rows"], 2)
                self.assertEqual(len(grid["block_corners"]), 4)
            finally:
                server.shutdown()
                server.server_close()


class PhotoWindowTests(unittest.TestCase):
    def test_the_page_serves_the_photo_given_on_the_command_line(self):
        import threading
        from urllib.request import urlopen

        from cdindex.align import AlignSession, make_server

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            red_path = folder / "table-red.jpg"
            blue_path = folder / "table-blue.jpg"
            red = np.zeros((30, 40, 3), dtype=np.uint8)
            blue = np.zeros((30, 40, 3), dtype=np.uint8)
            red[:, :] = (0, 0, 230)
            blue[:, :] = (230, 40, 0)
            cv2.imwrite(str(red_path), red)
            cv2.imwrite(str(blue_path), blue)
            red_session = AlignSession(red_path, folder / "red-run", rows=1, cols=1, inset=0)
            blue_session = AlignSession(blue_path, folder / "blue-run", rows=1, cols=1, inset=0)
            red_server = make_server(red_session, "127.0.0.1", 0)
            blue_server = make_server(blue_session, "127.0.0.1", 0)
            threads = [
                threading.Thread(target=red_server.serve_forever, daemon=True),
                threading.Thread(target=blue_server.serve_forever, daemon=True),
            ]
            for thread in threads:
                thread.start()
            try:
                red_state = json.loads(urlopen(f"http://127.0.0.1:{red_server.server_address[1]}/state").read())
                blue_state = json.loads(urlopen(f"http://127.0.0.1:{blue_server.server_address[1]}/state").read())
                self.assertIn("table-red.jpg", red_state["photo"])
                self.assertIn("table-blue.jpg", blue_state["photo"])
                self.assertNotEqual(red_state["photo"], blue_state["photo"])
                with urlopen(f"http://127.0.0.1:{red_server.server_address[1]}{red_state['photo']}") as response:
                    self.assertIn("no-store", response.headers["Cache-Control"])
                    shown = cv2.imdecode(np.frombuffer(response.read(), dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertGreater(shown[:, :, 2].mean(), 200)
                self.assertLess(shown[:, :, 0].mean(), 30)
                with urlopen(f"http://127.0.0.1:{blue_server.server_address[1]}{blue_state['photo']}") as response:
                    shown = cv2.imdecode(np.frombuffer(response.read(), dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertGreater(shown[:, :, 0].mean(), 200)
                self.assertLess(shown[:, :, 2].mean(), 30)
            finally:
                red_server.shutdown()
                blue_server.shutdown()
                red_server.server_close()
                blue_server.server_close()

    def test_a_saved_grid_is_not_kept_for_a_different_photo(self):
        import os
        import time

        from cdindex.align import AlignSession

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "first.jpg"
            second = folder / "second.jpg"
            canvas = np.zeros((40, 50, 3), dtype=np.uint8)
            cv2.imwrite(str(first), canvas)
            cv2.imwrite(str(second), canvas)
            out = folder / "run"
            session = AlignSession(first, out, rows=1, cols=1, inset=0)
            session.save({"rows": 1, "cols": 1, "inset": 0, "corners": [[0, 0], [49, 0], [49, 39], [0, 39]]})
            reopened = AlignSession(first, out, rows=None, cols=None, inset=0)
            self.assertEqual(reopened.corners[0], [0, 0])
            other = AlignSession(second, out, rows=None, cols=None, inset=0)
            self.assertNotEqual(other.corners[0], [0, 0])
            os.utime(first, (time.time() + 30, time.time() + 30))
            replaced = AlignSession(first, out, rows=None, cols=None, inset=0)
            self.assertNotEqual(replaced.corners[0], [0, 0])

    def test_a_busy_port_is_reported_before_the_old_window_is_reused(self):
        import socket

        from cdindex.align import ensure_port_free

        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        try:
            with self.assertRaises(SystemExit) as caught:
                ensure_port_free(port, "127.0.0.1")
            self.assertIn(str(port), str(caught.exception))
            self.assertIn("previous photo", str(caught.exception))
        finally:
            held.close()


class RunFolderTests(unittest.TestCase):
    def test_each_photo_gets_its_own_folder(self):
        from cdindex.runs import resolve_run_directory

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "output"
            first, note = resolve_run_directory(parent, Path("table-a.jpg"))
            second, _second_note = resolve_run_directory(parent, Path("table-b.jpg"))
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, parent)
            self.assertEqual(second.parent, parent)
            self.assertIn("table-a", first.name)
            self.assertIn("table-b", second.name)
            self.assertIn("New run folder", note)
            self.assertEqual(json.loads((first / "run.json").read_text())["image"], "table-a.jpg")
            resumed, resume_note = resolve_run_directory(first, Path("table-a.jpg"))
            self.assertEqual(resumed, first)
            self.assertIn("Resuming", resume_note)

    def test_a_different_photo_does_not_reuse_a_finished_run(self):
        from cdindex.runs import resolve_run_directory

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "output" / "run"
            run.mkdir(parents=True)
            (run / "run.json").write_text(json.dumps({"image": "first.jpg"}))
            (run / "yandex").mkdir()
            (run / "yandex" / "01.json").write_text("{}")
            new, note = resolve_run_directory(run, Path("second.jpg"))
            self.assertNotEqual(new, run)
            self.assertEqual(new.parent, run.parent)
            self.assertFalse(str(new).startswith(str(run) + "/"))
            self.assertFalse((new / "yandex").exists())
            self.assertIn("first.jpg", note)
            self.assertTrue((run / "yandex" / "01.json").exists())


class ProcessPageTests(unittest.TestCase):
    def test_the_page_can_export_and_only_one_job_runs(self):
        import threading
        import time
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen

        from cdindex.align import AlignSession, make_server

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image_path = folder / "photo.jpg"
            canvas = np.zeros((80, 100, 3), dtype=np.uint8)
            cv2.imwrite(str(image_path), canvas)
            session = AlignSession(image_path, folder / "run", rows=2, cols=2, inset=0)
            server = make_server(session, "127.0.0.1", 0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                early = Request(
                    f"http://127.0.0.1:{port}/process",
                    data=b'{"steps": ["export"]}',
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(early)
                self.assertEqual(caught.exception.code, 400)

                payload = json.dumps(
                    {"rows": 2, "cols": 2, "inset": 0, "corners": [[0, 0], [99, 0], [99, 79], [0, 79]]}
                ).encode()
                urlopen(
                    Request(
                        f"http://127.0.0.1:{port}/save",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                )
                review = {
                    "items": [
                        {
                            "index": 1,
                            "crop": "crops/01.jpg",
                            "chosen": None,
                            "status": "unable to identify",
                        }
                    ]
                }
                (folder / "run" / "review.json").write_text(json.dumps(review))
                started = json.loads(
                    urlopen(
                        Request(
                            f"http://127.0.0.1:{port}/process",
                            data=b'{"steps": ["export"]}',
                            headers={"Content-Type": "application/json"},
                        )
                    ).read()
                )
                self.assertIn(started["running"], (True, False))
                progress = started
                for _ in range(50):
                    if not progress["running"]:
                        break
                    time.sleep(0.05)
                    progress = json.loads(urlopen(f"http://127.0.0.1:{port}/progress").read())
                self.assertFalse(progress["running"])
                self.assertIsNone(progress["error"])
                self.assertEqual(progress["results"][0]["status"], "unable to identify")
                self.assertTrue(any("Wrote" in line for line in progress["lines"]))
                csv = urlopen(f"http://127.0.0.1:{port}/cd_index.csv").read().decode()
                self.assertIn("Track Title", csv)
                self.assertIn("unable to identify", csv)

                release = threading.Event()

                def wait(steps, force):
                    del steps, force
                    release.wait(3)

                session.start_job(["export"], False, worker=wait)
                with self.assertRaises(RuntimeError):
                    session.start_job(["export"], False)
                blocked = Request(
                    f"http://127.0.0.1:{port}/process",
                    data=b'{"steps": ["export"]}',
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as busy:
                    urlopen(blocked)
                self.assertEqual(busy.exception.code, 409)
                release.set()
                for _ in range(50):
                    if not session.progress()["running"]:
                        break
                    time.sleep(0.05)
                self.assertFalse(session.progress()["running"])
            finally:
                server.shutdown()
                server.server_close()


class SearchCacheTests(unittest.TestCase):
    def test_a_newer_crop_is_searched_again(self):
        import os
        import time

        from cdindex.cli import search_result_is_current

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            crop = folder / "01.jpg"
            saved = folder / "01.json"
            crop.write_bytes(b"crop")
            saved.write_text('{"tags": ["old"], "objects": [], "sites": []}')
            os.utime(saved, (time.time() + 10, time.time() + 10))
            self.assertTrue(search_result_is_current(crop, saved))
            os.utime(crop, (time.time() + 20, time.time() + 20))
            self.assertFalse(search_result_is_current(crop, saved))
            saved.write_text('{"tags": [], "objects": [], "sites": [], "error": "blocked"}')
            os.utime(saved, (time.time() + 30, time.time() + 30))
            self.assertFalse(search_result_is_current(crop, saved))


class ExportReviewTests(unittest.TestCase):
    def test_needs_review_is_copied_onto_every_track(self):
        import csv

        from cdindex.cli import main
        from cdindex.discogs import save_json

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            release = {
                "id": 42,
                "title": "Believe",
                "artists": "Cher",
                "tracks": [
                    {"pos": "1", "title": "Believe", "type": "track"},
                    {"pos": "2", "title": "Strong Enough", "type": "track"},
                ],
            }
            save_json(out / "discogs" / "releases" / "42.json", release)
            save_json(
                out / "review.json",
                {
                    "items": [
                        {
                            "index": 6,
                            "crop": "crops/06.jpg",
                            "status": "needs_review",
                            "chosen": {"release_id": 42, "title": "Believe"},
                        },
                        {
                            "index": 7,
                            "crop": "crops/07.jpg",
                            "status": "identified",
                            "chosen": {"release_id": 42, "title": "Believe"},
                        },
                    ]
                },
            )
            save_json(out / "run.json", {"image": "table.jpg", "cases": 2})
            dest = out / "cd_index.csv"
            status = main(["export", "--out", str(out), "--csv", str(dest), "--pause", "0"])
            self.assertEqual(status, 0)
            with dest.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual([row["Needs Review"] for row in rows], ["yes", "yes", "no", "no"])
        self.assertEqual([row["Index"] for row in rows], ["6", "6", "7", "7"])


class CsvWriterTests(unittest.TestCase):
    def test_writer_uses_the_track_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracks.csv"
            write_csv(catalog_rows("shot.jpg", 2, "crops/02.jpg", None), path)
            text = path.read_text()
        self.assertTrue(
            text.startswith(
                "Input Image,Index,CD Artist,Album,CD Number,Track Artist,Track Title,Cover Crop,Needs Review\n"
            )
        )
        self.assertIn("unable to identify", text)
        self.assertIn(",no\n", text)


if __name__ == "__main__":
    unittest.main()
