# PD-F1009 catalog app

React UI and FastAPI backend for loading catalog CDs into a Pioneer PD-F1009 and picking a loaded disc or track to play. Play commands go to an Arduino IR bridge when configured; otherwise they are logged locally.

## Setup

```bash
# from repo root
pip install -r requirements.txt
cd app/web && npm install && cd ../..
```

Use Node 18+ (this repo often uses `nvm use 22`).

Catalog CSV columns include `Discogs Release ID`. The app does **not** auto-import CSV on startup; upload a CSV from the Load screen (`POST /api/catalog/import`) and set the **crop base folder** (project root, a run folder, or a `crops/` directory). Slot assignments are preserved across re-imports by `(source image, photo index, CD number)`.

## Run

Terminal 1 — API (opens `app/db/catalog.sqlite`, no CSV import):

```bash
python -m app
```

Terminal 2 — UI (proxies `/api` to port 8000):

```bash
cd app/web && npm run dev
```

Open http://127.0.0.1:5173

- **Load** — upload CSV, set Arduino URL, assign discs to slots 1–300 (or 0 for single loader)
- **Play** — search loaded discs, play disc/track, Pause / Stop / Prev / Next

On **Edit album**, tick **Fetch artist, album, track list, and cover from Discogs** to replace metadata from a Discogs release ID (`POST /api/albums/{id}/discogs-sync`). No account required; optional `DISCOGS_TOKEN` raises the rate limit. Slot assignments are kept when disc numbers still match.

## Docker

One container serves the API and the built React UI on port 8000.

```bash
# from repo root
docker compose up --build
```

Open http://127.0.0.1:8000

- SQLite persists in the `disc-detect-data` volume (`DISC_DETECT_DB=/data/catalog.sqlite`).
- Cover crops: mount a host folder and set **crop base** on the Load screen to `/crops` (default compose mount is `./crops`; override with `DISC_DETECT_CROPS=/path/to/crops`).
- To reuse an existing DB file: stop the stack, then add a bind mount for `/data/catalog.sqlite` in `docker-compose.yml`.

Without Compose:

```bash
docker build -t disc-detect .
docker run --rm -p 8000:8000 -v disc-detect-data:/data -v "$PWD/crops:/crops:ro" disc-detect
```

## Arduino REST contract

Set the base URL on the Load page (example `http://192.168.1.50`, no trailing slash). The app calls:

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | — | `{ "ok": true }` |
| `POST` | `/play` | `{ "disc": 0-300, "track"?: 1+ }` | Select disc (and optional track), then play |
| `POST` | `/pause` | `{}` | Pause |
| `POST` | `/stop` | `{}` | Stop |
| `POST` | `/next` | `{}` | Next track |
| `POST` | `/prev` | `{}` | Previous track |
| `GET` | `/status` | — | `{ "state": "playing"\|"paused"\|"stopped", "disc": N\|null, "track": N\|null }` |

If the URL is empty, commands stay local and are still written to `play_log` for the now-playing bar.

## Tests

```bash
pytest tests/test_app_catalog.py tests/test_player_transport.py -q
# indexer pipeline: pytest indexer/tests -q
```
